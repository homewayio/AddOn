import time
import json
import logging
import threading

import requests

from homeway.sentry import Sentry

# Handles any events from Home Assistant we care about.
class EventHandler:

    # Determines how long we will wait from the first request to send a collapsed batch of events.
    # We want this to be quite short, so events are responsive. But long enough to prevent super spamming events.
    c_RequestCollapseDelayTimeSec = 1.0

    # Useful for debugging.
    c_LogEvents = True

    def __init__(self, logger:logging.Logger, pluginId:str) -> None:
        self.Logger = logger
        self.PluginId = pluginId
        self.HomewayApiKey = ""

        # Request collapse logic.
        self.ThreadEvent = threading.Event()
        self.Lock = threading.Lock()
        self.SendEvents = []

        # Start the send thread.
        self.Thread = threading.Thread(target=self._StateChangeSender)
        self.Thread.daemon = True
        self.Thread.start()


    def SetHomewayApiKey(self, key:str) -> None:
        # When we get the API key, if it's the first time it's being set, request a sync to make sure
        # things are in order.
        if self.HomewayApiKey is None or len(self.HomewayApiKey) == 0:
            self._QueueStateChangeSend(self._GetStateChangeSendEvent("startup_sync"))
        self.HomewayApiKey = key


    # Called by the HA connection class when HA sends any event.
    def OnEvent(self, event:dict, haVersion:str):

        # Check for required fields
        if "event_type" not in event:
            self.Logger.warn("Event Handler got an event that was missing the event_type.")
            return
        eventType = event["event_type"]

        # Right now, we only need to look at state changed events.
        if eventType != "state_changed":
            return

        # Log if needed.
        if EventHandler.c_LogEvents and self.Logger.isEnabledFor(logging.DEBUG):
            self.Logger.info(f"Incoming HA State Changed Event:\r\n{json.dumps(event, indent=2)}")

        # Get the common data.
        # Note that these will still be in data, but can be set to null.
        if "data" not in event or "old_state" not in event["data"] or "new_state" not in event["data"] or "entity_id" not in event["data"]:
            self.Logger.warn("Event Handler got an event that was missing the data/old_state/new_state/entity_id fields.")
            return

        entityId = event["data"]["entity_id"]
        newState_CanBeNone = event["data"]["new_state"]
        oldState_CanBeNone = event["data"]["old_state"]
        # We can combine report state and request sync for assistants into a single API.
        # When a device is added...
        #    state_changed is fired with a null old_state and a new_state with the device info.
        # When a device is removed...
        #    state_changed is fired with a null new_state and a old_state with the device info.
        # When a device is renamed...
        #    state_changed is fired with a old_state and a new_state and the "friendly name" changes.
        # When a device state changes, like it's turned on or off...
        #   state_changed is fired with a old_state and a new_state and the "state" changes.

        # For state changes, we only care about a subset of devices. Some types are way to verbose to report.
        # A full list of entity can be found here: https://developers.home-assistant.io/docs/core/entity/
        if (    entityId.startswith("light.")  is False
            and entityId.startswith("switch.") is False
            and entityId.startswith("cover.")  is False
            and entityId.startswith("fan.")    is False
            and entityId.startswith("lock.")   is False
            and entityId.startswith("climate.")is False):
            # But, we will send every device add/remove/change to the API.
            # We will always send if the new or old state are None, meaning an add or remove.
            if newState_CanBeNone is not None and oldState_CanBeNone is not None:
                # Finally, we will always send if there's a friendly name change.
                if (    "attributes" not in oldState_CanBeNone
                    or "friendly_name" not in oldState_CanBeNone["attributes"]
                    or "attributes" not in newState_CanBeNone
                    or "friendly_name" not in newState_CanBeNone["attributes"]
                    or  newState_CanBeNone["attributes"]["friendly_name"] == oldState_CanBeNone["attributes"]["friendly_name"]):
                    # If we are here...
                    #    This is not a entity we always sent.
                    #    There is a new state and old state
                    #    There's NO friendly name change.
                    # So we ignore it.
                    return

        # If we get here, this is an status change we want to send.
        self._QueueStateChangeSend(self._GetStateChangeSendEvent(entityId, haVersion, newState_CanBeNone, oldState_CanBeNone))


    # Converts the HA events to our send event format.
    def _GetStateChangeSendEvent(self, entityId:str, haVersion_CanBeNone:str = None, newState_CanBeNone:dict = None, oldState_CanBeNone:dict = None) -> dict:
        sendEvent = {
            "EntityId": entityId
        }
        if haVersion_CanBeNone is not None:
            sendEvent["HaVersion"] = haVersion_CanBeNone

        # Helpers
        def _removeIfInDict(d:dict, key:str):
            if key in d:
                del d[key]
        def _trimState(state:dict):
            _removeIfInDict(state, "entity_id")
            _removeIfInDict(state, "context")
            _removeIfInDict(state, "last_changed")
            _removeIfInDict(state, "last_updated")
        # If there's a new state, add it.
        if newState_CanBeNone is not None:
            _trimState(newState_CanBeNone)
            sendEvent["NewState"] = newState_CanBeNone
        # If there's a old state, add it.
        if oldState_CanBeNone is not None:
            _trimState(oldState_CanBeNone)
            sendEvent["OldState"] = oldState_CanBeNone
        return sendEvent


    def _QueueStateChangeSend(self, sendEvent:dict):
        # We collapse individual calls in to a single batch call based on a time threshold.
        self.Logger.debug("_QueueStateChangeSend called")
        with self.Lock:
            # Add this event to the queue.
            self.SendEvents.append(sendEvent)
            # Set the event to wake up the thread, if it's not already.
            self.ThreadEvent.set()


    def _StateChangeSender(self):
        while True:
            try:
                # Always check if there's something to send. If not, we sleep.
                hasEventsToSend = False
                with self.Lock:
                    if len(self.SendEvents) > 0:
                        hasEventsToSend = True
                    else:
                        # Make sure all pending events are cleared.
                        self.ThreadEvent.clear()

                # If there's nothing to send, we sleep.
                if hasEventsToSend is False:
                    self.ThreadEvent.wait()
                    continue

                # We have something to send, so we sleep for a bit to allow more events to batch up.
                time.sleep(EventHandler.c_RequestCollapseDelayTimeSec)

                # Ensure we have an API key.
                if self.HomewayApiKey is None or len(self.HomewayApiKey) == 0:
                    self.Logger.warn("We wanted to do a send state change events, but don't have an API key.")
                    time.sleep(30.0)
                    continue

                # Now we collect what exists and send them.
                sendEvents = []
                with self.Lock:
                    sendEvents = self.SendEvents
                    self.SendEvents = []

                self.Logger.info(f"_StateChangeSender is sending {(len(sendEvents))} assistant state change events.")

                # Make the call.
                result = requests.post('https://homeway.io/api/plugin-api/statechangeevents',
                                        json={"PluginId": self.PluginId, "ApiKey": self.HomewayApiKey, "Events": sendEvents },
                                        timeout=30)

                # Validate the response.
                if result.status_code != 200:
                    self.Logger.warn(f"Send Change Events failed, the API returned {result.status_code}")

            except Exception as e:
                Sentry.Exception("_StateChangeSender exception", e)

