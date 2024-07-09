import ssl
import time
import json
import logging
import threading

import requests

from homeway.sentry import Sentry

from .serverinfo import ServerInfo

# Handles any events from Home Assistant we care about.
class EventHandler:

    # How long we will wait from the most recent send before we consider it a new first send period.
    # The first event will be sent more quickly, future events will be delayed more and more to prevent spamming.
    c_SendPeriodWindowSec = 60.0

    # Determines how long we will wait from the first request to send a collapsed batch of events.
    # We want this to be quite short, so events are responsive. But long enough to prevent super spamming events.
    c_RequestCollapseDelayTimeSec = 1.0

    # How long we will wait from the first request to send in a period a collapsed batch of events.
    # This allows one off changes to be more responsive.
    c_RequestCollapseDelayTimeSecFirstSend = 0.1

    # How often we will reset the dict keeping track of spammy events.
    c_SpammyEntityResetWindowSec = 60.0 * 30 # 30 minutes

    # The number of entity updates that can be sent per entity before they are throttled.
    c_SpammyEntityUpdateLimitBeforeThrottle = 30

    # The number of updates between allowed throttled updates.
    c_SpammyEntityUpdateAllowFrequency = 30

    # Useful for debugging.
    c_LogEvents = False

    def __init__(self, logger:logging.Logger, pluginId:str, devLocalHomewayServerAddress_CanBeNone:str) -> None:
        self.Logger = logger
        self.PluginId = pluginId
        self.HomewayApiKey = ""
        self.DevLocalHomewayServerAddress_CanBeNone = devLocalHomewayServerAddress_CanBeNone

        # Request collapse logic.
        self.ThreadEvent = threading.Event()
        self.Lock = threading.Lock()
        self.SendEvents = []
        self.SpammyEntityDict = {}
        self.SendPeriodStartSec = 0.0
        self.SentCountThisPeriod = 0
        self.SpammyEntityWindowStartSec = 0.0

        # Start the send thread.
        self.Thread = threading.Thread(target=self._StateChangeSender)
        self.Thread.daemon = True
        self.Thread.start()

        # We need to detect the temp units.
        self.TempThread = threading.Thread(target=self._TempUnitsDetector)
        self.TempThread.daemon = True
        self.TempThread.start()

        # Must be C or F, default to C
        self.HaTempUnits = "C"


    def SetHomewayApiKey(self, key:str) -> None:
        # When we get the API key, if it's the first time it's being set, request a sync to make sure things are in order.
        hadKey = self.HomewayApiKey is not None and len(self.HomewayApiKey) > 0
        self.HomewayApiKey = key
        if hadKey is False:
            entityId = "startup_sync"
            e = self._GetStateChangeSendEventAndValidate(entityId)
            if e is None:
                self.Logger.error("startup_sync event failed to generate a send payload.")
            self._QueueStateChangeSend(entityId, e)


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
            and entityId.startswith("input_boolean.") is False
            and entityId.startswith("scene.") is False
            and entityId.startswith("cover.")  is False
            and entityId.startswith("fan.")    is False
            and entityId.startswith("lock.")   is False
            and entityId.startswith("alarm_control_panel.")   is False
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
        # Build the dict we will send and validate that everything we need to send is there.
        e = self._GetStateChangeSendEventAndValidate(entityId, haVersion, newState_CanBeNone, oldState_CanBeNone)
        if e is None:
            return
        self._QueueStateChangeSend(entityId, e)


    # Converts the HA events to our send event format.
    def _GetStateChangeSendEventAndValidate(self, entityId:str, haVersion_CanBeNone:str = None, newState_CanBeNone:dict = None, oldState_CanBeNone:dict = None) -> dict:
        sendEvent = {
            "EntityId": entityId
        }
        if haVersion_CanBeNone is not None:
            sendEvent["HaVersion"] = haVersion_CanBeNone

        # Helpers
        def _removeIfInDict(d:dict, key:str):
            # Removes a key from the dict if it's there.
            if key in d:
                del d[key]
        def _trimState(state:dict):
            # Remove any bloat we don't need, to keep the size down.
            _removeIfInDict(state, "entity_id")
            _removeIfInDict(state, "context")
            _removeIfInDict(state, "last_changed")
            _removeIfInDict(state, "last_updated")
        def _validateHasRequiredFields(d:dict) -> bool:
            # Sometimes during startup we get messages without friendly names.
            # We need the friendly names to talk with both Alexa and Google, so we just ignore them.
            # The updates are usually for offline devices anyways, maybe that's why they don't have names?
            a = d.get("attributes", None)
            if a is None:
                return False
            name = a.get("friendly_name", None)
            if name is None or len(name) == 0:
                return False
            return True
        # If there's a new state, add it.
        if newState_CanBeNone is not None:
            if _validateHasRequiredFields(newState_CanBeNone) is False:
                return None
            _trimState(newState_CanBeNone)
            # We add the temp units we detect, so our servers know.
            newState_CanBeNone["HwTempUnits"] = self.HaTempUnits
            sendEvent["NewState"] = newState_CanBeNone
        # If there's a old state, add it.
        if oldState_CanBeNone is not None:
            if _validateHasRequiredFields(oldState_CanBeNone) is False:
                return None
            _trimState(oldState_CanBeNone)
            sendEvent["OldState"] = oldState_CanBeNone
        return sendEvent


    def _QueueStateChangeSend(self, entityId:str, sendEvent:dict):
        # We collapse individual calls in to a single batch call based on a time threshold.
        self.Logger.debug(f"_QueueStateChangeSend called `{entityId}`")
        with self.Lock:
            # Some individual entities seem to be really spammy, we have seen some lights
            # that send updates very often. To mitigate that, we will keep track of how many times
            # each entity reports updates and start limiting them if it's really chatty.
            # This works by keeping track of each entity and the number of times it's updating.
            # If it updates more than x times in a time window, the updates will be throttled.

            # Check if it's time to reset the spammy event dict.
            if time.time() - self.SpammyEntityWindowStartSec > EventHandler.c_SpammyEntityResetWindowSec:
                self.Logger.debug("Event Handler resetting the spammy entity window.")
                self.SpammyEntityWindowStartSec = time.time()
                self.SpammyEntityDict = {}

            # Handle updating the count for this entity
            if entityId in self.SpammyEntityDict:
                updateCount = self.SpammyEntityDict[entityId] + 1
                self.SpammyEntityDict[entityId] = updateCount
                # Check this entity is over the limit
                if updateCount >= EventHandler.c_SpammyEntityUpdateLimitBeforeThrottle:
                    if updateCount == EventHandler.c_SpammyEntityUpdateLimitBeforeThrottle:
                        self.Logger.debug(f"Entity {entityId} just hit the spam limit and will now be throttled.")
                    # The entity is over the limit, check if we should allow this one through.
                    if updateCount % EventHandler.c_SpammyEntityUpdateAllowFrequency != 0:
                        # Drop this update.
                        return
                    self.Logger.debug(f"Allowing a throttled event for {entityId}. Total updates: {updateCount}")
            else:
                self.SpammyEntityDict[entityId] = 1

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

                # If we get here, we have something to send.
                # To allow a quick one off response, we will wait a shorter amount of time for the first send in a new period.
                # To prevent spamming, every time we send in the same period, we will wait a bit longer.
                deltaFromPeriodStart = time.time() - self.SendPeriodStartSec
                if deltaFromPeriodStart > EventHandler.c_SendPeriodWindowSec:
                    # The last send was outside the window, so we are in a new period.
                    self.SendPeriodStartSec = time.time()
                    self.SentCountThisPeriod = 1
                    # This is the first send in a period, we sleep a shorter amount of time.
                    # We still want to sleep some to collapse back-to-back events, but we want to be responsive.
                    time.sleep(EventHandler.c_RequestCollapseDelayTimeSecFirstSend)
                else:
                    # We are in a send period.
                    # This might happen if the user is changing a lot of things rapidly. To prevent spamming, we want to back off event sends.
                    self.SentCountThisPeriod += 1
                    self.SentCountThisPeriod = min(self.SentCountThisPeriod, 3)
                    time.sleep(EventHandler.c_RequestCollapseDelayTimeSec * self.SentCountThisPeriod)

                # Ensure we have an API key.
                if self.HomewayApiKey is None or len(self.HomewayApiKey) == 0:
                    self.Logger.warn("We wanted to do a send state change events, but don't have an API key.")
                    time.sleep(10.0)
                    continue

                # Now we collect what exists and send them.
                sendEvents = []
                with self.Lock:
                    sendEvents = self.SendEvents
                    self.SendEvents = []

                self.Logger.debug(f"_StateChangeSender is sending {(len(sendEvents))} assistant state change events.")

                # Make the call.
                url = "https://homeway.io/api/plugin-api/statechangeevents"
                if self.DevLocalHomewayServerAddress_CanBeNone is not None:
                    url = f"http://{self.DevLocalHomewayServerAddress_CanBeNone}/api/plugin-api/statechangeevents"
                result = requests.post(url, json={"PluginId": self.PluginId, "ApiKey": self.HomewayApiKey, "Events": sendEvents }, timeout=30)

                # Validate the response.
                if result.status_code != 200:
                    self.Logger.warn(f"Send Change Events failed, the API returned {result.status_code}")

            except Exception as e:
                if (e is ConnectionError or e is ssl.SSLError) and "Max retries exceeded with url" in str(e):
                    self.Logger.error("Homeway server is not reachable. Will try again later.", e)
                else:
                    Sentry.Exception("_StateChangeSender exception", e)


    def _TempUnitsDetector(self):
        startup = True
        while True:
            try:
                # Run at startup, otherwise every hour.
                if startup is False:
                    time.sleep(60 * 60)
                else:
                    time.sleep(2.0)
                startup = False

                # Try to get the config API
                configApiJson = ServerInfo.GetConfigApi(self.Logger, 30.0)
                if configApiJson is None:
                    self.Logger.warn("Get config API failed.")
                    continue

                # Parse the result.
                if "unit_system" not in configApiJson:
                    self.Logger.warn("Get config API missing unit_system")
                    continue
                if "temperature" not in configApiJson["unit_system"]:
                    self.Logger.warn("Get config API missing temperature")
                    continue

                if configApiJson["unit_system"]["temperature"] == "°F":
                    self.HaTempUnits = "F"
                elif configApiJson["unit_system"]["temperature"] == "°C":
                    self.HaTempUnits = "C"
                else:
                    self.Logger.warn(f"Get config API unknown temperature unit [{configApiJson['unit_system']['temperature']}]")
            except Exception as e:
                Sentry.Exception("_StateChangeSender exception", e)
