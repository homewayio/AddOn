import time
import logging
import threading

from typing import Any, Callable, List, Optional

from ..sentry import Sentry

# A simple class that allows queuing of events to be sent in a collapsed manner from a separate thread.
# Events can be queued individually, and the worker thread will call the provided callback with a list of events when it processes them.
class ThreadedQueue:


    def __init__(self,
                 logger:logging.Logger,
                 name:str,
                 callback:Callable[[List[Any]], bool],                # The events are sent on the thread. If true is returned, the event is removed from the queue.
                 collapseDelaySec:Optional[float]=0.2,                # After something is added, how long to wait before processing the queue.
                 minTimeSinceLastAddSec:Optional[float]=None,         # Minimum time since last add before processing the queue.
                 minTimeSinceLastAddMaxWaitSec:Optional[float]=None,  # Maximum time to wait for minTimeSinceLastAddSec before processing anyway.
                 maxQueueSize:Optional[int]=None,                     # If set, the maximum size of the queue. If exceeded, oldest events are dropped.
                 backoffTimeWindowSec:Optional[float]=None,           # If backoffTimeWindowSec is set, enable back off logic. This is the time window to track.
                 backoffAllowedImmediateProcesses:Optional[int]=None, # If back off is enabled, this is the number of allowed immediate processes in the time window.
                 backoffDelaySec:Optional[float]=None,                # If back off is enabled, this is the initial delay to apply when back off is triggered.
                 backoffMaxDelaySec:Optional[float]=None,             # If back off is enabled, this is the maximum delay to apply.
                 backoffMultiplier:Optional[float]=None,              # If back off is enabled, this is the multiplier to apply to the delay each time back off is triggered.
                 ) -> None:
        self.Logger = logger
        self.Name = name
        self.Callback = callback
        self.CollapseDelaySec = collapseDelaySec
        self.MinTimeSinceLastAddSec = minTimeSinceLastAddSec
        self.MinTimeSinceLastAddMaxWaitSec = 5.0
        if minTimeSinceLastAddMaxWaitSec is not None:
            self.MinTimeSinceLastAddMaxWaitSec = minTimeSinceLastAddMaxWaitSec
        self.MaxQueueSize = 5000
        if maxQueueSize is not None:
            self.MaxQueueSize = maxQueueSize

        # Backoff logic
        # Set the defaults first so the values aren't optional.
        self.BackoffTimeWindowSec = backoffTimeWindowSec
        self.BackoffAllowedImmediateProcesses = 2
        self.BackoffDelaySec = 0.2
        self.BackoffMaxDelaySec = 30.0
        self.BackoffMultiplier = 2.0
        self.BackoffWindowStartTime:Optional[float] = None
        self.BackoffHitsInWindow = 0
        if backoffAllowedImmediateProcesses is not None:
            self.BackoffAllowedImmediateProcesses = backoffAllowedImmediateProcesses
        if backoffDelaySec is not None:
            self.BackoffDelaySec = backoffDelaySec
        if backoffMaxDelaySec is not None:
            self.BackoffMaxDelaySec = backoffMaxDelaySec
        if backoffMultiplier is not None:
            self.BackoffMultiplier = backoffMultiplier
        self.Lock = threading.Lock()
        self.ThreadEvent = threading.Event()
        self.Queue:List[Any] = []
        self.LastAddTime:Optional[float] = None

        # Start the send thread.
        self.Thread = threading.Thread(target=self._WorkerThread, name=f"{self.Name} Thread")
        self.Thread.daemon = True
        self.Thread.start()


    # Add an event to the queue.
    def Add(self, event:Any) -> None:
        with self.Lock:
            self.Queue.append(event)
            if self.MinTimeSinceLastAddSec is not None:
                self.LastAddTime = time.time()
        self.ThreadEvent.set()


    # The worker thread that processes the queued events.
    def _WorkerThread(self) -> None:
        while True:
            try:
                # Always check if there's something to process. If not, we sleep.
                # We need to always do this, to ensure if a new event comes in while we're processing, we catch it.
                hasItemsToProcess = False
                with self.Lock:
                    if len(self.Queue) > 0:
                        hasItemsToProcess = True
                    else:
                        # Make sure all pending events are cleared.
                        self.ThreadEvent.clear()

                # If there's nothing to process, we sleep.
                if hasItemsToProcess is False:
                    self.ThreadEvent.wait()
                    continue

                # If we get here, we have something to process.
                # If we have a collapse delay, sleep now.
                if self.CollapseDelaySec is not None:
                    time.sleep(self.CollapseDelaySec)

                # See if we have the min time since last add logic enabled.
                if self.MinTimeSinceLastAddSec is not None:
                    # If we have a minimum time since last add, ensure that has passed.
                    startTime = time.time()
                    while True:
                        # Compute the time since last add.
                        now = time.time()
                        timeSinceLastAddSec = 0.0
                        with self.Lock:
                            if self.LastAddTime is None:
                                self.Logger.warning(f"{self.Name} - LastAddTime is None when checking MinTimeSinceLastAdd")
                                break
                            timeSinceLastAddSec = now - self.LastAddTime

                        # If we are over the min time, break out.
                        if timeSinceLastAddSec >= self.MinTimeSinceLastAddSec:
                            break

                        # See if we've exceeded the max wait time.
                        if (now - startTime) >= self.MinTimeSinceLastAddMaxWaitSec:
                            self.Logger.debug(f"{self.Name} - Exceeded MinTimeSinceLastAddMaxWaitSec of {self.MinTimeSinceLastAddMaxWaitSec}, proceeding anyway")
                            break

                        # Sleep a bit and check again.
                        time.sleep(0.05)

                # Finally, do the backoff logic if enabled.
                if self.BackoffTimeWindowSec is not None:
                    currentTime = time.time()
                    if (self.BackoffWindowStartTime is None) or (currentTime - self.BackoffWindowStartTime > self.BackoffTimeWindowSec):
                        # Start a new window.
                        self.BackoffWindowStartTime = currentTime
                        self.BackoffHitsInWindow = 0

                    self.BackoffHitsInWindow += 1
                    if self.BackoffHitsInWindow > self.BackoffAllowedImmediateProcesses:
                        # We need to back off.
                        delay = self.BackoffDelaySec * (self.BackoffMultiplier ** (self.BackoffHitsInWindow - self.BackoffAllowedImmediateProcesses))
                        if delay > self.BackoffMaxDelaySec:
                            delay = self.BackoffMaxDelaySec
                        self.Logger.debug(f"{self.Name} - Backing off for {delay:.2f} seconds (hit {self.BackoffHitsInWindow} in window)")
                        time.sleep(delay)

                # Swap the queue out.
                queueToProcess:List[Any] = []
                with self.Lock:
                    queueToProcess = self.Queue
                    self.Queue = []
                # Check the max queue size and trim if needed.
                if len(queueToProcess) > self.MaxQueueSize:
                    self.Logger.warning(f"{self.Name} - Queue size {len(queueToProcess)} exceeds MaxQueueSize of {self.MaxQueueSize}, dropping oldest events")
                    queueToProcess = queueToProcess[-self.MaxQueueSize:]

                # Use a try catch around the callback to ensure we catch any exceptions and re-queue the events.
                try:
                    # Process the events
                    if self.Callback(queueToProcess):
                        # Successfully processed, continue.
                        continue
                except Exception as e:
                    Sentry.OnException(f"{self.Name} - Unhandled exception in callback.", e)

                # If we failed to process, re-add the events to the queue.
                with self.Lock:
                    self.Queue = queueToProcess + self.Queue
                    self.Logger.warning(f"{self.Name} - Callback failed to process events, re-adding to queue. Queue size is now {len(self.Queue)}")

                # Sleep a little, and then retry.
                time.sleep(2.0)

            except Exception as e:
                Sentry.OnException(f"{self.Name} thread exception", e)
                continue
