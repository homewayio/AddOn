import ssl
import time
import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from homeway.sentry import Sentry
from homeway.httpsessions import HttpSessions
from homeway.interfaces import IHomeAssistantWebSocket, IHomeContext

from .serverinfo import ServerInfo


# Handles any events from Home Assistant we care about.
class EventHandler:

    # How long we will wait from the most recent send before we consider it a new first send period.
    # The first event will be sent more quickly, future events will be delayed more and more to prevent spamming.
    c_SendPeriodWindowSec = 30.0

    # Determines how long we will wait from the first request to send a collapsed batch of events.
    # We want this to be quite short, so events are responsive. But long enough to prevent super spamming events.
    c_RequestCollapseDelayTimeSec = 1.0

    # How long we will wait from the first request to send in a period a collapsed batch of events.
    # This allows one off changes to be more responsive.
    c_RequestCollapseDelayTimeSecFirstSend = 0.2

    # How often we will reset the dict keeping track of spammy events.
    c_SpammyEntityResetWindowSec = 60.0 * 30 # 30 minutes

    # The number of entity updates that can be sent per entity before they are throttled.
    c_SpammyEntityUpdateLimitBeforeThrottle = 30

    # The number of updates between allowed throttled updates.
    c_SpammyEntityUpdateAllowFrequency = 30

    # Useful for debugging.
    c_LogEvents = False


    def __init__(self, logger:logging.Logger, pluginId:str, devLocalHomewayServerAddress:Optional[str]) -> None:
        self.Logger = logger
        self.PluginId = pluginId
        self.HomewayApiKey:Optional[str] = None
        self.DevLocalHomewayServerAddress:Optional[str] = devLocalHomewayServerAddress

        # Request collapse logic.
        self.ThreadEvent = threading.Event()
        self.Lock = threading.Lock()
        self.SendEvents:List[Dict[Any, Any]] = []
        self.SpammyEntityDict:Dict[str, int] = {}
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

        # A callback to fire if the home context needs to be updated.
        self.HomeContextCallback:Optional[Callable[[], None]] = None
        self.HomeContext:Optional[IHomeContext] = None
        self.HaWebSocketCon:Optional[IHomeAssistantWebSocket] = None


    def SetHomewayApiKey(self, key:str) -> None:
        # When we get the API key, if it's the first time it's being set, request a sync to make sure things are in order.
        hadKey = self.HomewayApiKey is not None and len(self.HomewayApiKey) > 0
        self.HomewayApiKey = key
        if hadKey is False:
            entityId = "startup_sync"
            e = self._GetSendEventAndValidate(entityId, True, True)
            if e is None:
                self.Logger.error("startup_sync event failed to generate a send payload.")
                return
            self._QueueSendEvent(entityId, e, forceSend=True)


    # Called by the HA connection class when HA sends a new state.
    def SetHomeContextCallback(self, callback:Callable[[], None]) -> None:
        self.HomeContextCallback = callback


    # Set the home context object, which we use for looking up entity ids.
    def SetHomeContext(self, homeContext:IHomeContext) -> None:
        self.HomeContext = homeContext


    # Registers the Home Assistant WebSocket connection, which is needed to look up some context.
    def RegisterHomeAssistantWebsocketCon(self, haWebSocketCon:IHomeAssistantWebSocket):
        self.HaWebSocketCon = haWebSocketCon


    # Called by the HA connection class when HA sends any event.
    def OnEvent(self, eventRoot:Dict[str, Any], haVersion:Optional[str]) -> None:

        # Check for required fields
        eventType = eventRoot.get("event_type", None)
        if eventType is None:
            self.Logger.warning("Event Handler got an event that was missing the event_type.")
            return

        # If we get an entity registry update, we need to refresh the home context.
        # This can happen for many things, but we know one of them is if a entity is removed or exposed to assistants.
        if eventType == "entity_registry_updated":
            self._HandleEntityRegistryUpdatedEvent(eventRoot, haVersion)
            return

        # Handle state changed events.
        if eventType == "state_changed":
            self._HandleStateChangedEvent(eventRoot, haVersion)
            return


    def _HandleStateChangedEvent(self, eventRoot:Dict[str, Any], haVersion:Optional[str]) -> None:

        # Log if needed.
        if EventHandler.c_LogEvents and self.Logger.isEnabledFor(logging.DEBUG):
            self.Logger.info(f"Incoming HA State Changed Event:\r\n{json.dumps(eventRoot, indent=2)}")

        # Get the common data.
        # Note that these will still be in data, but can be set to null.
        eventData = eventRoot.get("data", None)
        if eventData is None:
            self.Logger.warning("Event Handler got an event that was missing the data field.")
            return
        entityId = eventData.get("entity_id", None)
        if entityId is None:
            self.Logger.warning("Event Handler got an event that was missing the entity_id field.")
            return
        newState_CanBeNone = eventData.get("new_state", None)
        oldState_CanBeNone = eventData.get("old_state", None)
        if newState_CanBeNone is None and oldState_CanBeNone is None:
            self.Logger.debug(f"Event Handler got an event that was missing both the new_state and old_state fields. {json.dumps(eventRoot, indent=2)}  ")
            return

        # This is a special case, we don't get sun.sun in the list entity command, so we ignore it here.
        if entityId == "sun.sun":
            return

        # This is important, check if the entity is exposed to assistants.
        # This is because we only want to send events for entities that are exposed to assistants.
        # BUT - We don't want to check if this is a removal, since the entity won't be in the home context anymore.
        # For a removal newState_CanBeNone will be None. We will send to both alexa and google, they will handle ignoring it if needed.
        isUpdateForAlexa = True
        isUpdateForGoogle = True
        # Check if this is not a removal.
        if newState_CanBeNone is not None:
            if self.HomeContext is not None:
                fullEntityDict = self.HomeContext.GetEntityById(entityId)
                if fullEntityDict is not None:
                    isUpdateForAlexa  = self.HomeContext.IsExposeToAssistant(fullEntityDict, checkAlexa=True)
                    isUpdateForGoogle = self.HomeContext.IsExposeToAssistant(fullEntityDict, checkGoogle=True)
                    if isUpdateForAlexa is False and isUpdateForGoogle is False:
                        # Not exposed to either assistant, we ignore this event.
                        return
                    # Also check if it's disabled.
                    if self.HomeContext.IsDisabled(fullEntityDict):
                        # Disabled, we ignore this event.
                        return

        # We can combine report state and request sync for assistants into a single API.
        # When a device is added...
        #    state_changed is fired with a null old_state and a new_state with the device info.
        # When a device is removed...
        #    state_changed is fired with a null new_state and a old_state with the device info.
        # When a device is renamed...
        #    state_changed is fired with a old_state and a new_state and the "friendly name" changes.
        # When a device state changes, like it's turned on or off...
        #   state_changed is fired with a old_state and a new_state and the "state" changes.
        isAddRemoveOrNameChange = (
                   newState_CanBeNone is None # This is a remove
                or oldState_CanBeNone is None # This is an add
                or  ("attributes" in oldState_CanBeNone # This is a name change.
                    and "friendly_name" in oldState_CanBeNone["attributes"]
                    and "attributes" in newState_CanBeNone
                    and "friendly_name" in newState_CanBeNone["attributes"]
                    and  newState_CanBeNone["attributes"]["friendly_name"] != oldState_CanBeNone["attributes"]["friendly_name"]))

        # If there was a item change, we need to refresh the home context.
        if isAddRemoveOrNameChange:
            self._FireHomeContextUpdateCallback()

        # If this is just a state change, see if we care about it.
        if isAddRemoveOrNameChange is False:
            # For state changes, we only care about a subset of devices. Some types are way to verbose to report.
            # A full list of entity can be found here: https://developers.home-assistant.io/docs/core/entity/
            # We changed this from a disallow list because we don't want to block new entity types that are added.
            # We mostly don't allow any types that aren't supported.
            # The only exception is sensors, since they can be really chatty and realtime updates aren't super useful.
            if (
                   entityId.startswith("ai_task.") is True
                or entityId.startswith("assist_satellite.") is True
                or entityId.startswith("calendar.") is True
                or entityId.startswith("conversation.") is True
                or entityId.startswith("date.") is True
                or entityId.startswith("datetime.") is True
                or entityId.startswith("geo_location.") is True
                or entityId.startswith("image.") is True
                or entityId.startswith("image_processing.") is True
                or entityId.startswith("number.") is True
                or entityId.startswith("sensor.") is True
                or entityId.startswith("stt.") is True
                or entityId.startswith("tag.") is True
                or entityId.startswith("text.") is True
                or entityId.startswith("time.") is True
                or entityId.startswith("tts.") is True
                or entityId.startswith("update.") is True
                or entityId.startswith("wake_word.") is True
                or entityId.startswith("weather.") is True
            ):
                    # If we are here...
                    #    This is not a entity we always sent.
                    #    There is a new state and old state
                    #    There's NO friendly name change.
                    # So we ignore it.
                return

        # If we get here, this is an status change we want to send.
        # Build the dict we will send and validate that everything we need to send is there.
        e = self._GetSendEventAndValidate(entityId, isUpdateForAlexa, isUpdateForGoogle, haVersion, newState_CanBeNone, oldState_CanBeNone)
        if e is None:
            return
        self._QueueSendEvent(entityId, e)


    def _HandleEntityRegistryUpdatedEvent(self, eventRoot:Dict[str, Any], haVersion:Optional[str]) -> None:
        # For any entity registry updated event, we just refresh the home context.
        # This is because many things can change that we care about, like exposure to assistants.
        self._FireHomeContextUpdateCallback()

        # Get the required info.
        dataDict = eventRoot.get("data", {})
        if dataDict is None:
            self.Logger.warning("Entity Registry Updated Event received, but missing data field. Ignoring event.")
            return
        entityId = dataDict.get("entity_id", None)
        if entityId is None:
            self.Logger.warning("Entity Registry Updated Event received, but missing entity_id field. Ignoring event.")
            return

        # We only sub type of entity registry updated event we care about is exposure changes to assistants.
        # To do that, we check if the data -> changes -> options. If the change involves that field, we send an update.
        # Remember that if the options dict doesn't have a field for an assistant, it means it's not exposed to that assistant.
        changesDict = dataDict.get("changes", None)
        if changesDict is None:
            return
        optionsDict = changesDict.get("options", None)
        if optionsDict is None:
            return

        # We need to kick the rest of this work off to a thread, since we might need to do some lookups via the HA WebSocket connection.
        # And we can't block this thread, since it's the WS receive thread.
        def _HandleEntityRegistryUpdatedEvent_Thread():
            try:
                # We need the HA WebSocket connection to look up the live state of the entity.
                if self.HaWebSocketCon is None or self.HomeContext is None:
                    self.Logger.error("Entity Registry Updated Event received, but no HA WebSocket connection or the Home Context is registered. Ignoring event.")
                    return

                # The entity_registry_updated event includes the old data, so we must query the new data from HA to get the current state.
                newEntityDict = self.HomeContext.GetEntityById(entityId, forceRefresh=True)
                if newEntityDict is None:
                    self.Logger.warning(f"Entity Registry Updated Event received, but entity {entityId} not found in Home Context. Ignoring event.")
                    return

                # We can now determine if this change affects exposure to assistants.
                # If not, we ignore this event.
                changeToAlexa = None
                changeToGoogle = None
                if self.HomeContext.IsExposeToAssistant(changesDict, checkAlexa=True) != self.HomeContext.IsExposeToAssistant(newEntityDict, checkAlexa=True):
                    changeToAlexa = self.HomeContext.IsExposeToAssistant(newEntityDict, checkAlexa=True)
                if self.HomeContext.IsExposeToAssistant(changesDict, checkGoogle=True) != self.HomeContext.IsExposeToAssistant(newEntityDict, checkGoogle=True):
                    changeToGoogle = self.HomeContext.IsExposeToAssistant(newEntityDict, checkGoogle=True)
                if changeToAlexa is None and changeToGoogle is None:
                    # No change to either assistant exposure, we ignore this event.
                    self.Logger.info(f"Entity Registry Updated Event for {entityId} but the old and new google and alexa exposure are the same. Ignoring event.")
                    return

                # We need to get the live state of the entity, so we need to use the HA WebSocket connection.
                # We need the live state to get the attributes, which includes the friendly name and device details require for device add.
                # We can only do this by getting the state of all entities, since there's no way to filter by entity id.
                response = self.HaWebSocketCon.SendAndReceiveMsg({"type":"get_states"})
                if response is None or "success" not in response or response["success"] is not True or "result" not in response:
                    self.Logger.warning("Entity Registry Updated Event received, but failed to get entity states from the HA WebSocket connection. Ignoring event.")
                    return
                result = response["result"]
                # Look through all of the results to find the one we care about.
                foundEntityState:Optional[Dict[str, Any]] = None
                for entity in result:
                    searchEntityId = entity.get("entity_id", None)
                    if searchEntityId is not None and searchEntityId == entityId:
                        foundEntityState = entity
                        break
                if foundEntityState is None:
                    self.Logger.warning(f"Entity Registry Updated Event received, but entity {entityId} not found in HA states. Ignoring event.")
                    return

                # Finally, we can send the events for the assistants that changed exposure.
                if changeToAlexa is not None:
                    isExposedToAlexa = changeToAlexa
                    # If this is now exposed, we set the new state, otherwise it's None and we set the old state.
                    newState_CanBeNone = foundEntityState if isExposedToAlexa else None
                    oldState_CanBeNone = None if isExposedToAlexa else foundEntityState
                    e = self._GetSendEventAndValidate(entityId, True, False, haVersion, newState_CanBeNone, oldState_CanBeNone)
                    if e is not None:
                        self.Logger.info(f"Sending Entity Exposure Add/Remove For Alexa. Entity: {entityId}, Is Exposed: {isExposedToAlexa}")
                        self._QueueSendEvent(entityId, e, forceSend=True)
                    else:
                        self.Logger.warning(f"Entity Registry Updated Event {entityId} for Alexa generated no send payload, likely missing friendly name. Ignoring event for Alexa.")
                if changeToGoogle is not None:
                    # Now we need to make an event for Google.
                    isExposedToGoogle = changeToGoogle
                    # If this is now exposed, we set the new state, otherwise it's None and we set the old state.
                    newState_CanBeNone = foundEntityState if isExposedToGoogle else None
                    oldState_CanBeNone = None if isExposedToGoogle else foundEntityState
                    e = self._GetSendEventAndValidate(entityId, False, True, haVersion, newState_CanBeNone, oldState_CanBeNone)
                    if e is not None:
                        self.Logger.info(f"Sending Entity Exposure Add/Remove For Google Home. Entity: {entityId}, Is Exposed: {isExposedToGoogle}")
                        self._QueueSendEvent(entityId, e, forceSend=True)
                    else:
                        self.Logger.warning(f"Entity Registry Updated Event {entityId} for Google generated no send payload, likely missing friendly name. Ignoring event for Google.")

            except Exception as e:
                Sentry.OnException("_HandleEntityRegistryUpdatedEvent_Thread exception", e)
        threading.Thread(target=_HandleEntityRegistryUpdatedEvent_Thread).start()


    # This handles creating the event format we send for all event types, like state change updates and exposure changes.
    # The event must be targeted at one OR BOTH assistants to handle.
    def _GetSendEventAndValidate(self, entityId:str, isUpdateForAlexa:bool, isUpdateForGoogle:bool, haVersion:Optional[str]=None, newState:Optional[Dict[str, Any]]=None, oldState:Optional[Dict[str, Any]]=None) -> Optional[Dict[str, Any]]:
        sendEvent:Dict[str, Any] = {
            "EntityId": entityId,
            "IsUpdateForAlexa": isUpdateForAlexa,    # This indicates if Alexa should process this change (add/remove/update)
            "IsUpdateForGoogle": isUpdateForGoogle   # This indicates if Google should process this change (add/remove/update)
        }
        if haVersion is not None:
            sendEvent["HaVersion"] = haVersion

        # Helpers
        def _removeIfInDict(d:Dict[str, Any], key:str):
            # Removes a key from the dict if it's there.
            if key in d:
                del d[key]
        def _trimState(state:Dict[str, Any] ):
            # Remove any bloat we don't need, to keep the size down.
            _removeIfInDict(state, "entity_id")
            _removeIfInDict(state, "context")
            _removeIfInDict(state, "last_changed")
            _removeIfInDict(state, "last_updated")
        def _validateHasRequiredFields(d:Dict[str, Any]) -> bool:
            # Attributes are required because it contains device info that we need to build
            # the correct attributes for the assistant device.
            a = d.get("attributes", None)
            if a is None:
                return False
            # The assistants require a friendly name to be present.
            # But we need to handle this exactly the same way as Home Assistant does, which is if there's no
            # friendly name we take the entity id and remove the underscores.
            name = a.get("friendly_name", None)
            if name is None or len(name) == 0:
                if self.HomeContext is None:
                    return False
                name = self.HomeContext.MakeFriendlyNameFromEntityId(entityId)
                if name is None or len(name) == 0:
                    return False
                a["friendly_name"] = name
            return True
        # If there's a new state, add it.
        if newState is not None:
            if _validateHasRequiredFields(newState) is False:
                return None
            _trimState(newState)
            # We add the temp units we detect, so our servers know.
            newState["HwTempUnits"] = self.HaTempUnits
            sendEvent["NewState"] = newState
        # If there's a old state, add it.
        if oldState is not None:
            if _validateHasRequiredFields(oldState) is False:
                return None
            _trimState(oldState)
            sendEvent["OldState"] = oldState
        return sendEvent


    def _QueueSendEvent(self, entityId:str, sendEvent:Dict[str, Any], forceSend:bool = False) -> None:
        # We collapse individual calls in to a single batch call based on a time threshold.
        self.Logger.debug(f"_QueueSendEvent called `{entityId}`")
        with self.Lock:
            if forceSend is False:
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


    def _FireHomeContextUpdateCallback(self) -> None:
        callback = self.HomeContextCallback
        if callback is not None:
            callback()


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
                    # Allow a few events to go through quickly.
                    self.SentCountThisPeriod += 1
                    if self.SentCountThisPeriod <= 5:
                        time.sleep(EventHandler.c_RequestCollapseDelayTimeSecFirstSend)
                    else:
                        # This might happen if the user is changing a lot of things rapidly. To prevent spamming, we want to back off event sends.
                        self.SentCountThisPeriod = min(self.SentCountThisPeriod, 10)
                        time.sleep(EventHandler.c_RequestCollapseDelayTimeSec * self.SentCountThisPeriod)

                # Ensure we have an API key.
                if self.HomewayApiKey is None or len(self.HomewayApiKey) == 0:
                    self.Logger.warning("We wanted to do a send state change events, but don't have an API key.")
                    time.sleep(10.0)
                    continue

                # Now we collect what exists and send them.
                sendEvents:List[Dict[str, Any]] = []
                with self.Lock:
                    sendEvents = self.SendEvents
                    self.SendEvents = []

                self.Logger.debug(f"_StateChangeSender is sending {(len(sendEvents))} assistant state change events.")

                # Make the call.
                url = "https://homeway.io/api/plugin-api/statechangeevents"
                if self.DevLocalHomewayServerAddress is not None:
                    url = f"http://{self.DevLocalHomewayServerAddress}/api/plugin-api/statechangeevents"
                result = HttpSessions.GetSession(url).post(url, json={"PluginId": self.PluginId, "ApiKey": self.HomewayApiKey, "Events": sendEvents }, timeout=30)

                # Validate the response.
                if result.status_code != 200:
                    self.Logger.warning(f"Send Change Events failed, the API returned {result.status_code}")

            except Exception as e:
                if (e is ConnectionError or e is ssl.SSLError) and "Max retries exceeded with url" in str(e):
                    self.Logger.error("Homeway server is not reachable. Will try again later.", e)
                else:
                    Sentry.OnException("_StateChangeSender exception", e)


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
                    self.Logger.warning("Get config API failed.")
                    continue

                # Parse the result.
                if "unit_system" not in configApiJson:
                    self.Logger.warning("Get config API missing unit_system")
                    continue
                if "temperature" not in configApiJson["unit_system"]:
                    self.Logger.warning("Get config API missing temperature")
                    continue

                if configApiJson["unit_system"]["temperature"] == "°F":
                    self.HaTempUnits = "F"
                elif configApiJson["unit_system"]["temperature"] == "°C":
                    self.HaTempUnits = "C"
                else:
                    self.Logger.warning(f"Get config API unknown temperature unit [{configApiJson['unit_system']['temperature']}]")
            except Exception as e:
                Sentry.OnException("_StateChangeSender exception", e)
