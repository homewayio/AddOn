import time
import logging
import threading

import requests

from homeway.sentry import Sentry

# Handles any events from Home Assistant we care about.
class EventHandler:

    # Determines how long we will wait from the last request sync queue before we make the call
    # This is important, because events can fire back to back quickly, so we need to collapse them. We also want to prevent
    # calling it constantly if the use is changing a lot of stuff.
    c_RequestSyncCollapseDelayTimeSec = 5.0

    def __init__(self, logger:logging.Logger, pluginId:str) -> None:
        self.Logger = logger
        self.PluginId = pluginId
        self.HomewayApiKey = ""

        # Request Sync logic
        self.RsLock = threading.Lock()
        self.RsLastRequestTime = 0
        self.RsQueuedRequests = 0
        self.RsThread = None


    def SetHomewayApiKey(self, key:str) -> None:
        # When we get the API key, if it's the first time it's being set, request a sync to make sure
        # things are in order.
        if self.HomewayApiKey is None or len(self.HomewayApiKey) == 0:
            self._QueueRequestSync()
        self.HomewayApiKey = key


    # Called by the HA connection class when HA sends any event.
    def OnEvent(self, event:dict):

        # Check for required fields
        if "event_type" not in event:
            self.Logger.warn("Event Handler got an event that was missing the event_type.")
            return
        eventType = event["event_type"]

        # For Request Sync, we only need to care about things that the assistants observer. These are things like
        #   - Device Names
        #   - Device Room Locations
        #   - Devices Add/Removed
        #
        # When a device is added or removed, we get many of these
        #   event_type:device_registry_updated
        #   event_type:entity_registry_updated
        # When a device is renamed, we get a...
        #   event_type:state_changed
        #       where data.old_state.attributes.friendly_name != data.new_state.attributes.friendly_name
        # When a device room changes, we get a
        #   event_type:device_registry_updated
        if eventType == "device_registry_updated" or eventType == "entity_registry_updated":
            self._QueueRequestSync()
        if eventType == "state_changed":
            if "data" in event and "old_state" in event["data"] and "new_state" in event["data"]:
                data = event["data"]
                if "attributes" in data["old_state"] and "friendly_name" in data["old_state"]["attributes"] and "attributes" in data["new_state"] and "friendly_name" in data["new_state"]["attributes"]:
                    if data["old_state"]["attributes"]["friendly_name"] != data["new_state"]["attributes"]["friendly_name"]:
                        self._QueueRequestSync()


    def _QueueRequestSync(self):
        # Since we get a lot of events back to back, we will collapse them all into
        # a single request, using a delay.
        self.Logger.debug("We detected a device change/add/remove, _QueueRequestSync called")
        with self.RsLock:
            # Set this request time. Ensure we set this first, to make sure it's set when the thread spawns.
            self.RsLastRequestTime = time.time()

            # If there is no thread, start one now.
            if self.RsThread is None:
                self.RsThread = threading.Thread(target=self._RequestSyncWorker)
                self.RsThread.daemon = True
                self.RsThread.start()


    def _RequestSyncWorker(self):
        try:
            # First, wait until the most recent request was more than c_RequestSyncCollapseDelayTimeSec ago.
            while True:
                with self.RsLock:
                    timeDiffSec = time.time() - self.RsLastRequestTime
                    if timeDiffSec >= EventHandler.c_RequestSyncCollapseDelayTimeSec:
                        # Time do do it.
                        break
                # Out of lock, sleep and try again.
                time.sleep(2)

            self.Logger.info("We detected a device change/add/remove, calling the request sync API")
            # We clear the thread now, to ensure that if calls are made while we are processing, the will get reported.
            # The problem is we don't know when during the processing the sync is done, we assume now to be safe.
            with self.RsLock:
                self.RsThread = None

            # Ensure we have an API key.
            if self.HomewayApiKey is None or len(self.HomewayApiKey) == 0:
                self.Logger.warn("We wanted to do a RequestSync but don't have an API key.")
                return

            # Make the call.
            # Request sync is easy, because it doesn't require any context. The assistant will call back into use with a sync command
            # where we will send the full device context again.
            result = requests.post('https://homeway.io/api/plugin-api/requestsync',
                                    json={"PluginId": self.PluginId, "ApiKey": self.HomewayApiKey},
                                    timeout=30)

            # Validate the response.
            if result.status_code != 200:
                self.Logger.warn(f"Request Sync failed, the API returned {result.status_code}")

        except Exception as e:
            Sentry.Exception("_RequestSyncWorker exception", e)
