import ssl
import time
import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from homeway.sentry import Sentry
from homeway.httpsessions import HttpSessions
from homeway.interfaces import IHomeAssistantWebSocket, IHomeContext
from homeway.util.threadedqueue import ThreadedQueue

from .serverinfo import ServerInfo


class EntityRegistryUpdatedProcessingEvent:
    def __init__(self, entityId:str, changesDict:Dict[str, Any]) -> None:
        self.EntityId = entityId
        self.ChangesDict = changesDict
        self.HasChangeToAlexa:Optional[bool] = None
        self.HasChangeToGoogle:Optional[bool] = None


# Handles any events from Home Assistant we care about.
class EventHandler:

    # How often we will reset the dict keeping track of spammy events.
    c_SpammyEntityResetWindowSec = 60.0 * 30 # 30 minutes

    # The number of entity updates that can be sent per entity before they are throttled.
    c_SpammyEntityUpdateLimitBeforeThrottle = 30

    # The number of updates between allowed throttled updates.
    c_SpammyEntityUpdateAllowFrequency = 30

    # Useful for debugging.
    c_LogEvents = False

    # This is a list of domains we always ignore for state changes.
    # See the impl for reasons why.
    c_DomainsToIgnoreForStateChanges = tuple([ "ai_task.", "assist_satellite.", "calendar.", "conversation.", "date.", "datetime.",
                                "geo_location.", "image.", "image_processing.", "number.", "sensor.", "stt.",
                                "tag.", "text.", "time.", "tts.", "update.", "wake_word.", "weather." ])


    def __init__(self, logger:logging.Logger, pluginId:str, devLocalHomewayServerAddress:Optional[str]) -> None:
        self.Logger = logger
        self.PluginId = pluginId
        self.HomewayApiKey:Optional[str] = None
        self.DevLocalHomewayServerAddress:Optional[str] = devLocalHomewayServerAddress
        self.Lock = threading.Lock()
        self.HaVersion:Optional[str] = None

        # Spammy entity update tracking.
        self.SpammyEntityDict:Dict[str, int] = {}
        self.SpammyEntityWindowStartSec = 0.0

        # Start the send thread.
        self.EventSendThreadedQueue = ThreadedQueue(
            logger=self.Logger,
            name="EventSend",
            callback=self._ProcessSendEvents,
            # Always wait a bit when any event is added to the queue, to allow batching.
            collapseDelaySec=0.2,
            # We also use a backoff to handle batching up of events when they are coming in really fast.
            backoffTimeWindowSec=20.0,
            backoffAllowedImmediateProcesses=5,
            backoffDelaySec=1.0,
            backoffMaxDelaySec=10.0,
            backoffMultiplier=2.0,
            # Limit the queue size to prevent memory issues.
            maxQueueSize=1000
        )

        # Setup a threaded queue to process and bach up the events.
        # This is important for two reasons:
        #   1) Many updates can happen in bulk, and we will get a lot of these events in a short period.
        #   2) It is expensive to process the events, but much less if done in batches.
        #   3) The processing must not happen in the incoming thread, since it's the WS thread.
        self.EntityRegistryUpdatedThreadedQueue = ThreadedQueue(
            logger=self.Logger,
            name="EntityRegistryUpdated",
            callback=self._ProcessEntityRegistryUpdatedEvents,
            # Always wait a bit when any event is added to the queue, to allow batching.
            collapseDelaySec=0.4,
            # We also want to ensure a small amount of time has processed since the last add, to allow more events to come in.
            minTimeSinceLastAddSec=0.1
        )

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

        # Always update the HA version if we get it.
        if haVersion is not None:
            self.HaVersion = haVersion

        # Check for required fields
        eventType = eventRoot.get("event_type", None)
        if eventType is None:
            self.Logger.warning("Event Handler got an event that was missing the event_type.")
            return

        # If we get an entity registry update, we need to refresh the home context.
        # This can happen for many things, but we know one of them is if a entity is removed or exposed to assistants.
        if eventType == "entity_registry_updated":
            self._HandleEntityRegistryUpdatedEvent(eventRoot)
            return

        # Handle state changed events.
        if eventType == "state_changed":
            self._HandleStateChangedEvent(eventRoot)
            return


    def _HandleStateChangedEvent(self, eventRoot:Dict[str, Any]) -> None:
        # Log if needed.
        if EventHandler.c_LogEvents and self.Logger.isEnabledFor(logging.DEBUG):
            self.Logger.info(f"Incoming HA State Changed Event:\r\n{json.dumps(eventRoot, indent=2)}")

        # Get the common data.
        # Note that these will still be in data, but can be set to null.
        eventData:Optional[Dict[str, Any]] = eventRoot.get("data", None)
        if eventData is None:
            self.Logger.warning("Event Handler got an event that was missing the data field.")
            return
        entityId:Optional[str] = eventData.get("entity_id", None)
        if entityId is None:
            self.Logger.warning("Event Handler got an event that was missing the entity_id field.")
            return
        newState_CanBeNone:Optional[Dict[str, Any]] = eventData.get("new_state", None)
        oldState_CanBeNone:Optional[Dict[str, Any]] = eventData.get("old_state", None)
        if newState_CanBeNone is None and oldState_CanBeNone is None:
            self.Logger.debug(f"Event Handler got an event that was missing both the new_state and old_state fields. {json.dumps(eventRoot, indent=2)}  ")
            return

        # This is a special case, we don't get sun.sun in the list entity command, so we ignore it here.
        if entityId == "sun.sun":
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
            # The only exception is `sensor`, since they can be really chatty and realtime updates aren't super useful.
            if entityId.startswith(EventHandler.c_DomainsToIgnoreForStateChanges):
                    # If we are here...
                    #    This is not a entity we always sent.
                    #    There is a new state and old state
                    #    There's NO friendly name change.
                    # So we ignore it.
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
                # Note this CAN NOT force a refresh or it will cause a storm if a lot of events come in.
                fullEntityDict = self.HomeContext.GetEntityById(entityId, forceRefresh=False)
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

        # If we get here, this is an status change we want to send.
        # Build the dict we will send and validate that everything we need to send is there.
        e = self._GetSendEventAndValidate(entityId, isUpdateForAlexa, isUpdateForGoogle, newState_CanBeNone, oldState_CanBeNone)
        if e is None:
            return
        self._QueueSendEvent(entityId, e)


    def _HandleEntityRegistryUpdatedEvent(self, eventRoot:Dict[str, Any]) -> None:
        # For any entity registry updated event, we just refresh the home context.
        # We always do this because there are many reasons for this event beyond what we care about here.
        # We can only do this because the home context has logic to prevent too many refreshes in a short period of time.
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

        # In many cases, these exposure changes are done in bulk, so we want to batch them up a bit.
        # We add this to a queue, and the thread will process them one at a time.
        self.EntityRegistryUpdatedThreadedQueue.Add(EntityRegistryUpdatedProcessingEvent(entityId, changesDict))


    def _ProcessEntityRegistryUpdatedEvents(self, registryUpdateEvents:List[EntityRegistryUpdatedProcessingEvent]) -> bool:
        # We need the HA WebSocket connection to look up the live state of the entity.
        if self.HaWebSocketCon is None or self.HomeContext is None:
            self.Logger.error("Entity Registry Updated Event received, but no HA WebSocket connection or the Home Context is registered. Ignoring event.")
            return True
        start = time.time()

        # We need to get an updated entity dict, so we can determine if the exposure changed.
        # But, we only want to force the refresh on the first event, since that will refresh the home context.
        forceRefresh = True
        updatedList:List[EntityRegistryUpdatedProcessingEvent] = []
        for registryEvent in registryUpdateEvents:
            # The entity_registry_updated event includes the old data, so we must query the new data from HA to get the current state.
            newEntityDict = self.HomeContext.GetEntityById(registryEvent.EntityId, forceRefresh=forceRefresh)
            forceRefresh = False

            # Ensure we got a result.
            if newEntityDict is None:
                self.Logger.warning(f"Entity Registry Updated Event received, but entity {registryEvent.EntityId} not found in Home Context. Ignoring event.")
                continue

            # We can now determine if this change affects exposure to assistants.
            # If not, we ignore this event.
            if self.HomeContext.IsExposeToAssistant(registryEvent.ChangesDict, checkAlexa=True) != self.HomeContext.IsExposeToAssistant(newEntityDict, checkAlexa=True):
                registryEvent.HasChangeToAlexa = self.HomeContext.IsExposeToAssistant(newEntityDict, checkAlexa=True)
            if self.HomeContext.IsExposeToAssistant(registryEvent.ChangesDict, checkGoogle=True) != self.HomeContext.IsExposeToAssistant(newEntityDict, checkGoogle=True):
                registryEvent.HasChangeToGoogle = self.HomeContext.IsExposeToAssistant(newEntityDict, checkGoogle=True)
            if registryEvent.HasChangeToAlexa is None and registryEvent.HasChangeToGoogle is None:
                # No change to either assistant exposure, we ignore this event.
                self.Logger.info(f"Entity Registry Updated Event for {registryEvent.EntityId} but the old and new google and alexa exposure are the same. Ignoring event.")
                continue

            # Add it to the new list so we keep going.
            updatedList.append(registryEvent)

        # Ensure we still have items to process.
        if len(updatedList) == 0:
            return True
        registryUpdateEvents = updatedList

        # We need to get the live state of the entity, so we need to use the HA WebSocket connection.
        # We need the live state to get the attributes, which includes the friendly name and device details require for device add.
        # We can only do this by getting the state of all entities, since there's no way to filter by entity id.
        response = self.HaWebSocketCon.SendAndReceiveMsg({"type":"get_states"})
        if response is None or "success" not in response or response["success"] is not True or "result" not in response:
            self.Logger.warning("Entity Registry Updated Event received, but failed to get entity states from the HA WebSocket connection. Ignoring event.")
            return True
        result = response["result"]

        # Now, we can do the last bit of processing for each event.
        for registryEvent in registryUpdateEvents:
            # Look through all of the results to find the one we care about.
            foundEntityState:Optional[Dict[str, Any]] = None
            for entity in result:
                searchEntityId = entity.get("entity_id", None)
                if searchEntityId is not None and searchEntityId == registryEvent.EntityId:
                    foundEntityState = entity
                    break
            if foundEntityState is None:
                self.Logger.warning(f"Entity Registry Updated Event received, but entity {registryEvent.EntityId} not found in HA states. Ignoring event.")
                continue

            # Finally, we can send the events for the assistants that changed exposure.
            if registryEvent.HasChangeToAlexa is not None:
                isExposedToAlexa = registryEvent.HasChangeToAlexa
                # If this is now exposed, we set the new state, otherwise it's None and we set the old state.
                newState_CanBeNone = foundEntityState if isExposedToAlexa else None
                oldState_CanBeNone = None if isExposedToAlexa else foundEntityState
                e = self._GetSendEventAndValidate(registryEvent.EntityId, True, False, newState_CanBeNone, oldState_CanBeNone)
                if e is not None:
                    #self.Logger.info(f"Sending Entity Exposure Add/Remove For Alexa. Entity: {registryEvent.EntityId}, Is Exposed: {isExposedToAlexa}")
                    self._QueueSendEvent(registryEvent.EntityId, e, forceSend=True)
                else:
                    self.Logger.warning(f"Entity Registry Updated Event {registryEvent.EntityId} for Alexa generated no send payload, likely missing friendly name. Ignoring event for Alexa.")
            if registryEvent.HasChangeToGoogle is not None:
                # Now we need to make an event for Google.
                isExposedToGoogle = registryEvent.HasChangeToGoogle
                # If this is now exposed, we set the new state, otherwise it's None and we set the old state.
                newState_CanBeNone = foundEntityState if isExposedToGoogle else None
                oldState_CanBeNone = None if isExposedToGoogle else foundEntityState
                e = self._GetSendEventAndValidate(registryEvent.EntityId, False, True, newState_CanBeNone, oldState_CanBeNone)
                if e is not None:
                    #self.Logger.info(f"Sending Entity Exposure Add/Remove For Google Home. Entity: {registryEvent.EntityId}, Is Exposed: {isExposedToGoogle}")
                    self._QueueSendEvent(registryEvent.EntityId, e, forceSend=True)
                else:
                    self.Logger.warning(f"Entity Registry Updated Event {registryEvent.EntityId} for Google generated no send payload, likely missing friendly name. Ignoring event for Google.")
        end = time.time()
        self.Logger.debug(f"Entity Registry Updated Event processing took {(end - start):0.2f} seconds for {len(registryUpdateEvents)} events.")
        return True


    # This handles creating the event format we send for all event types, like state change updates and exposure changes.
    # The event must be targeted at one OR BOTH assistants to handle.
    def _GetSendEventAndValidate(self, entityId:str, isUpdateForAlexa:bool, isUpdateForGoogle:bool, newState:Optional[Dict[str, Any]]=None, oldState:Optional[Dict[str, Any]]=None) -> Optional[Dict[str, Any]]:
        sendEvent:Dict[str, Any] = {
            "EntityId": entityId,
            "IsUpdateForAlexa": isUpdateForAlexa,    # This indicates if Alexa should process this change (add/remove/update)
            "IsUpdateForGoogle": isUpdateForGoogle   # This indicates if Google should process this change (add/remove/update)
        }
        # If we have the HA version, add it.
        if self.HaVersion is not None:
            sendEvent["HaVersion"] = self.HaVersion

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
            self.EventSendThreadedQueue.Add(sendEvent)


    def _FireHomeContextUpdateCallback(self) -> None:
        callback = self.HomeContextCallback
        if callback is not None:
            callback()


    # If true is returned, the event was processed. If false, the events will be re-queued.
    def _ProcessSendEvents(self, events:List[Dict[str, Any]]) -> bool:

        # Ensure we have an API key.
        if self.HomewayApiKey is None or len(self.HomewayApiKey) == 0:
            self.Logger.warning("We wanted to do a send state change events, but don't have an API key.")
            time.sleep(10.0)
            return False

        self.Logger.debug(f"_StateChangeSender is sending {(len(events))} assistant state change events.")
        try:
            # Make the call.
            url = "https://homeway.io/api/plugin-api/statechangeevents"
            if self.DevLocalHomewayServerAddress is not None:
                url = f"http://{self.DevLocalHomewayServerAddress}/api/plugin-api/statechangeevents"
            result = HttpSessions.GetSession(url).post(url, json={"PluginId": self.PluginId, "ApiKey": self.HomewayApiKey, "Events": events }, timeout=30)

            # Check for success.
            if result.status_code < 300:
                return True
            # If the issue is a 400, ignore the issue.
            if result.status_code >= 400 and result.status_code < 500:
                self.Logger.error(f"Send Change Events failed with client error {result.status_code}, ignoring the issue.")
                return True

            # Throw an error.
            # The events will be re-queued to send again.
            raise Exception(f"Send Change Events failed, the API returned {result.status_code}")

        except Exception as e:
            if (e is ConnectionError or e is ssl.SSLError) and "Max retries exceeded with url" in str(e):
                self.Logger.error("Homeway server is not reachable. Will try again later.", e)
            else:
                Sentry.OnException("_StateChangeSender exception", e)
        return False


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
