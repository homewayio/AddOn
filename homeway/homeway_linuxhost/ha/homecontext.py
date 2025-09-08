import json
import time
import logging
import threading
from typing import List

from homeway.sentry import Sentry
from homeway.compression import Compression, CompressionContext, CompressionResult

from .connection import Connection
from .eventhandler import EventHandler


# Used to keep track of context for the assistant devices.
class AssistantDeviceContext:
    def __init__(self, entityId:str, deviceId:str, areaId:str, floorId:str):
        self.EntityId:str = entityId
        self.DeviceId:str = deviceId
        self.AreaId:str = areaId
        self.FloorId:str = floorId


# Captures the current context and state of the home in a way that can be sent to the server.
class HomeContext:

    # The worker will always refresh after this time.
    # Since we only update the state cache for some state changes, this is also the amount of time between
    # full state refreshes.
    WorkerRefreshTimeSec = 60 * 60
    WorkerRefreshTimeInFailureModeSec = 60 * 5

    # The string that will be used for anything that doesn't have a field.
    # For example, areas don't need a floor, devices don't need an area, and entities don't need a device.
    NoneString = "none"

    # Some sanity constants.
    MaxStringLength = 100

    # These are also enforced on the server side.
    MaxEntityCount = 5000


    def __init__(self, logger:logging.Logger, haConnection:Connection, eventHandler:EventHandler):
        self.Logger = logger
        self.HaConnection = haConnection
        self.EventHandler = eventHandler
        self.MostRecentUpdateSuccess = True

        # Setup our callbacks
        self.HaConnection.SetHomeContextOnConnectedCallback(self._OnNewHaWsConnected)
        self.EventHandler.SetHomeContextCallback(self._OnStateChangedCallback)

        # Setup the worker thread
        self.WorkerGoEvent = threading.Event()
        self.WorkerThread = threading.Thread(target=self._Worker)

        # Setup the cached data
        self.CacheLock = threading.Lock()
        self.CacheUpdatedEvent = threading.Event()
        self.HomeContextResult:CompressionResult = None
        self.AllowedEntityIds:dict = None
        self.AssistantDeviceContexts:List[AssistantDeviceContext] = []



    def Start(self):
        # Start our worker thread that maintains state.
        self.WorkerThread.start()


    # Returns the home context, as compressed bytes.
    # Returns None on failure.
    def GetHomeContext(self) -> CompressionResult:
        with self.CacheLock:
            # If we have a cached version, we are good to go.
            if self.HomeContextResult is not None:
                return self.HomeContextResult
            self.CacheUpdatedEvent.clear()

        # If we don't have a cached version, try to set the worker to go.
        # and then wait on the cache event.
        self.Logger.info("Home Context doesn't have a context yet, trying to force it...")
        self.WorkerGoEvent.set()

        # Don't wait long, we don't want to block the request.
        # If we don't get something back, the server will just have to wait.
        self.CacheUpdatedEvent.wait(1.0)

        # Return whatever we have or None.
        return self.HomeContextResult


    # Does a live query for the current states and returns them as and a the live context as compression results.
    def GetStatesAndLiveContext(self) -> CompressionResult:
        start = time.time()
        filterTime = 0.0
        try:
            # Query for the states
            states = self._QueryCurrentState()
            if states is None:
                return None

            # Filter out what we don't want.
            filterTime = time.time()
            (states, activeAssistEntityId) = self._FilterStateList(states)

            # Build the result wrapper.
            # These are part of the server shared model, so we can't change them.
            stateContext = {
                "EntityStates": states
            }

            # Build the live context, even if the active assist entity id is None.
            liveContext = self._BuildLiveContext(activeAssistEntityId)

            # Use separators to reduce size.
            stateContextStr = json.dumps(stateContext, separators=(',', ':'))
            liveContextStr = None
            if liveContext is not None:
                liveContextStr = json.dumps(liveContext, separators=(',', ':'))

            # Now compress what we have.
            stateCompressionResult = None
            liveContextCompressionResult = None
            with CompressionContext(self.Logger) as context:
                b = stateContextStr.encode("utf-8")
                context.SetTotalCompressedSizeOfData(len(b))
                stateCompressionResult = Compression.Get().Compress(context, b)
            if liveContextStr is not None:
                with CompressionContext(self.Logger) as context:
                    b = liveContextStr.encode("utf-8")
                    context.SetTotalCompressedSizeOfData(len(b))
                    liveContextCompressionResult = Compression.Get().Compress(context, b)

            # Return the result!
            return stateCompressionResult, liveContextCompressionResult

        except Exception as e:
            Sentry.OnException("Home Context GetStates error", e)
        finally:
            self.Logger.debug(f"Home Context GetStates - Total: {time.time() - start}s - Filter: {time.time() - filterTime}s")
        return None, None


    # Fired when a new connection is made to HA.
    def _OnNewHaWsConnected(self):
        # Ask the worker to refresh.
        self.WorkerGoEvent.set()


    # Fired when the event handler detects a entity/device/area/whatever was added, removed, or the name changed.
    def _OnStateChangedCallback(self) -> None:
        # If this happens, we need to update the Home Context.
        self.WorkerGoEvent.set()


    # The background worker that gets and refreshes the current home state.
    def _Worker(self):
        # Go into the main loop.
        while True:
            try:
                # First, wait on the event.
                # The amount of time depends on if the last update was successful or not.
                waitTimeSec = HomeContext.WorkerRefreshTimeSec
                if self.MostRecentUpdateSuccess is False:
                    waitTimeSec = HomeContext.WorkerRefreshTimeInFailureModeSec
                self.WorkerGoEvent.wait(waitTimeSec)
                self.WorkerGoEvent.clear()
                self.Logger.debug("HomeContext worker started")

                # Reset this flag.
                self.MostRecentUpdateSuccess = False

                # We either hit the timeout or got invoked, so we will run
                start = time.time()
                result = self._QueryAllObjects()
                if result is None:
                    continue

                # Handle and set the result
                parse = time.time()
                self._HandleAllObjectsResult(result)

                # Success!
                self.Logger.debug(f"HomeContext worker finished. Total: {time.time() - start}s - Parse: {time.time() - parse}s")

                # Success!
                self.MostRecentUpdateSuccess = True

            except Exception as e:
                Sentry.OnException("HomeContext worker error", e)
                # Always sleep on error.
                time.sleep(60)


    # This does a query for all things, basically everything beyond state.
    def _QueryAllObjects(self) -> "HomeContextQueryResult":
        try:
            s = threading.Semaphore(0)
            result = HomeContextQueryResult()
            # A helper that will get the type and store it in the result.
            def getThing(count:int, result:HomeContextQueryResult):
                try:
                    # Get the message type.
                    msgType = None
                    if count == 0:
                        msgType = "config/entity_registry/list"
                    elif count == 1:
                        msgType = "config/device_registry/list"
                    elif count == 2:
                        msgType = "config/area_registry/list"
                    elif count == 3:
                        msgType = "config/floor_registry/list"
                    elif count == 4:
                        msgType = "config/label_registry/list"
                    else:
                        self.Logger.error(f"_QueryAll count out of range: {count}")
                        return

                    # Query for the info.
                    response = self.HaConnection.SendMsg({"type" : msgType}, waitForResponse=True)

                    # Convert the False failure to a None
                    if response is not False:
                        if count == 0:
                            result.Entities = response
                        elif count == 1:
                            result.Devices = response
                        elif count == 2:
                            result.Areas = response
                        elif count == 3:
                            result.Floors = response
                        elif count == 4:
                            result.Labels = response
                except Exception as e:
                    Sentry.OnException(f"Home Context query all {type} exception.", e)
                finally:
                    s.release()

            # Kick off all the queries in parallel.
            total = 5
            for i in range(total):
                threading.Thread(target=getThing, args=(i, result)).start()

            # Wait for all the queries to finish.
            for i in range(total):
                #pylint: disable=consider-using-with
                s.acquire(True, 10.0)

            return result
        except Exception as e:
            Sentry.OnException("Home Context _QueryAllObjects error", e)
        return None


    # Parses the results, builds the output, and sets it.py
    def _HandleAllObjectsResult(self, result:"HomeContextQueryResult"):
        # We want to summarize the data down to a small structure that the model can easily understand, but it still has
        # all of the association context.
        # Floors are the bottom level
        #   Each area can only have one floor
        #      Each each device can only have one area
        #         Each entity can only have one device or area
        #
        # Note that entities can be associated with areas, but not devices. This is because some entities are software only.
        # Also note that each entry (floors, areas, devices, etc) can have a "none" entry indicating that it doesn't have that association.
        # So the nones will chain, so a device with no area, will be in a none floor, and then a none area.
        #
        # Labels are referenced by id in any object, and then the label contexts are listed at the end.
        # Also, we want to filter to only devices and entities that should be exposed to the assistant
        # Finally, we want to be sure to include any alternative names set.
        #
        # For the data we keep, try to keep the same names as the HA API uses, so it's easier to understand.
        #

        # Build the floors.
        floors = {}
        floorResults = self._GetResultsFromHaMsg("floors", result.Floors)
        if floorResults is not None:
            for f in floorResults:
                # Create our object and get the important ids.
                floor = {}
                floorId = self._CopyAndGetId("floor_id", f, floor)
                # Add the rest of the data.
                self._CopyPropertyIfExists("aliases", f, floor)
                self._CopyPropertyIfExists("level", f, floor)
                self._CopyPropertyIfExists("name", f, floor)
                # Set it
                floors[floorId] = floor
        else:
            # If this fails, add an empty floor.
            floor = { "floor_id" : HomeContext.NoneString, "name" : "Unknown" }
            floors[HomeContext.NoneString] = floor

        # Build the areas.
        # We build these sub dicts so we can easily associate them with the floors and in case something like areas fails, we can still send the floors.
        areas = {}
        areaResults = self._GetResultsFromHaMsg("areas", result.Areas)
        if areaResults is not None:
            for a in areaResults:
                # Create our object and get the important ids
                area = { }
                areaId = self._CopyAndGetId("area_id", a, area)
                floorId = self._CopyAndGetId("floor_id", a, area, warnIfMissing=False)
                # Add the rest of the data.
                self._CopyPropertyIfExists("aliases", a, area)
                self._CopyPropertyIfExists("name", a, area)
                self._CopyPropertyIfExists("labels", a, area, destKey="label_ids")
                # Set it
                self._AddToBaseMap(floors, floorId, "areas", areaId, area)
                areas[areaId] = area
        else:
            # If this fails, add an empty area.
            areaId = HomeContext.NoneString
            area = { "area_id" : areaId, "floor_id" : HomeContext.NoneString, "name" : "Unknown" }
            self._AddToBaseMap(floors, HomeContext.NoneString, "areas", areaId, area)
            areas[areaId] = area

        # Build the devices
        devices = {}
        deviceResults = self._GetResultsFromHaMsg("devices", result.Devices)
        if areaResults is not None:
            for d in deviceResults:
                # Check if the device is disabled, if so, don't expose it.
                if self._IsDisabled(d):
                    continue
                # Create our object and get the important ids
                device = { }
                # Keep the device ID since it can be used for association or for some API calls.
                deviceId = self._CopyAndGetId("id", d, device)
                # Some devices don't have areas, that's fine, we also don't need to copy it into the dest object.
                areaId = self._CopyAndGetId("area_id", d, device, copyIntoDst=False, warnIfMissing=False)
                # Add the rest of the data.
                self._CopyPropertyIfExists("entry_type", d, device)
                self._CopyPropertyIfExists("manufacturer", d, device)
                self._CopyPropertyIfExists("model", d, device)
                self._CopyPropertyIfExists("name_by_user", d, device)
                self._CopyPropertyIfExists("name", d, device)
                self._CopyPropertyIfExists("labels", d, device, destKey="label_ids")
                # Set it
                # Some devices don't have areas, that's fine.
                self._AddToBaseMap(areas, areaId, "devices", deviceId, device)
                devices[deviceId] = device
        else:
            # If this fails, add an empty device.
            deviceId = HomeContext.NoneString
            device = { "id" : deviceId, "area_id" : HomeContext.NoneString, "name" : "Unknown" }
            self._AddToBaseMap(areas, HomeContext.NoneString, "devices", deviceId, device)
            devices[deviceId] = device


        # Build the entities
        entities = {}
        entityResults = self._GetResultsFromHaMsg("entity", result.Entities)
        if entityResults is not None:
            for e in entityResults:
                # Important - We always allow assist devices, so the engine has context
                # on the devices that are active and where it can announce.
                # So we don't check if they are disabled or exposed.
                entityId:str = e.get("entity_id", None)
                if self._IsAssistEntityId(entityId):
                    self.Logger.debug("Allowing %s because it's an assist.", entityId)
                else:
                    # Check if the entity is disabled, if so, don't expose it.
                    if self._IsDisabled(e):
                        continue
                    # Check if it should be exposed to the assistant.
                    if self._IsExposeToAssistant(e) is False:
                        continue

                # Create our object and get the important ids
                entity = { }
                # We don't copy this id back into the dest object, because it's a random string that
                # isn't used for anything beyond associate this device with the entity.
                # Since the model doesn't need it to call any apis or add any context, we remove it for size.
                entityId = self._CopyAndGetId("id", e, entity, copyIntoDst=False)
                deviceId = self._CopyAndGetId("device_id", e, entity, copyIntoDst=False, warnIfMissing=False)
                areaId = self._CopyAndGetId("area_id", e, entity, copyIntoDst=False, warnIfMissing=False)
                # Add the rest of the data.
                self._CopyPropertyIfExists("entity_id", e, entity)
                self._CopyPropertyIfExists("entry_category", e, entity)
                self._CopyPropertyIfExists("name", e, entity)
                self._CopyPropertyIfExists("original_name", e, entity)
                self._CopyPropertyIfExists("platform", e, entity)
                # Set it
                # Some entities, like scenes and such, don't have a device but can still have an area.
                # So if there's no device id but there is an area id, add it directly to the area.
                if (deviceId is None or deviceId == HomeContext.NoneString) and (areaId is not None and areaId != HomeContext.NoneString):
                    self._AddToBaseMap(areas, areaId, "entities", entityId, entity)
                else:
                    # In all other cases, add it to the device. This might associate with a device or it might be unknown.
                    self._AddToBaseMap(devices, deviceId, "entities", entityId, entity)
                entities[entityId] = entity
        else:
            # If this fails, add an empty device.
            eId = HomeContext.NoneString
            entity = { "id" : eId, "entity_id" : "unknown.unknown", "device_id" : HomeContext.NoneString }
            self._AddToBaseMap(devices, HomeContext.NoneString, "entities", eId, entity)
            entities[eId] = entity

        # Summarize down the allowed entity ids into a map, we can use for fast lookups with state.
        allowedEntityLookupMap = {}
        for e in entities.values():
            eId = e.get("entity_id", None)
            if eId is not None:
                allowedEntityLookupMap[eId] = True

        # Build the labels - Do this as a list directly.
        # For these, they hang out on their own. The areas, devices, and entities will reference them by id, so this just adding context to the id.
        labels = []
        labelResults = self._GetResultsFromHaMsg("labels", result.Labels)
        if labelResults is not None:
            for l in labelResults:
                # Create our object and get the important ids
                label = { }
                self._CopyPropertyIfExists("label_id", l, label)
                self._CopyPropertyIfExists("name", l, label)
                self._CopyPropertyIfExists("description", l, label)
                # Set it
                labels.append(label)

        # We can prune any devices that don't have entities.
        # This can happen if there are no entities exposed to the assistant from the device.
        # If there are no entities exposed, there's no way to control it or see the state of it, so we might as well remove it.
        for f in floors.values():
            for a in f.get("areas", {}).values():
                idsToRemove = []
                for d in a.get("devices", {}).items():
                    # If there are no entities, remove the device.
                    if len(d[1].get("entities", {})) == 0:
                        idsToRemove.append(d[0])
                for i in idsToRemove:
                    del a["devices"][i]

        # To reduce size, we can convert the maps to lists, since in the map each object is indexed by it's id and then also self contains it's id.
        floors = list(floors.values())
        for f in floors:
            # Convert the areas to a list.
            f["areas"] = list(f.get("areas", {}).values())
            for a in f["areas"]:
                # Ensure there are devices to convert
                if "devices" in a:
                    # Convert devices into the list
                    a["devices"] = list(a.get("devices", {}).values())
                    for d in a["devices"]:
                        # Convert the entities to a list.
                        d["entities"] = list(d.get("entities", {}).values())
                # There can also be entities that don't have devices, so they are directly in the area.
                if "entities" in a:
                    a["entities"] = list(a.get("entities", {}).values())


        # To build the live context object, we need to keep track of the context of the assist devices
        assistDeviceContexts = []
        for f in floors:
            areas = f.get("areas", [])
            for a in areas:
                devices = a.get("devices", [])
                for d in devices:
                    foundAssistDevice = False
                    entities = d.get("entities", [])
                    for e in entities:
                        entityId = e.get("entity_id", None)
                        if entityId is not None:
                            if self._IsAssistEntityId(entityId):
                                assistDeviceContexts.append(AssistantDeviceContext(entityId, d.get("id", None), a.get("area_id", None), f.get("floor_id", None)))
                                foundAssistDevice = True
                        if foundAssistDevice:
                            # Break to ensure we only list each device once.
                            break

        # Finally, package them into the final object.
        # This is the format that the server expects, so we can't change it.
        homeContext = {
            "floors" : floors,
            "labels" : labels,
        }
        # Dump it to a string, so there's less work for the request to do.
        # Use separators to reduce size.
        homeContextStr = json.dumps(homeContext, separators=(',', ':'))
        # Now compress it!
        compressionResult = None
        with CompressionContext(self.Logger) as context:
            b = homeContextStr.encode("utf-8")
            context.SetTotalCompressedSizeOfData(len(b))
            compressionResult = Compression.Get().Compress(context, b)

        # Lock and swap
        with self.CacheLock:
            self.HomeContextResult = compressionResult
            self.AllowedEntityIds = allowedEntityLookupMap
            self.AssistantDeviceContexts = assistDeviceContexts
            self.CacheUpdatedEvent.set()


    # Important ID helper
    def _CopyAndGetId(self, key:str, source:dict, dest:dict, copyIntoDst:bool = True, warnIfMissing:bool = True) -> str:
        value = source.get(key, None)
        if value is None:
            if warnIfMissing:
                self.Logger.warning(f"GetHomeContext - Object missing {key}. {json.dumps(source)}")
            value = HomeContext.NoneString
        if copyIntoDst:
            dest[key] = value
        return value


    # Add to base map helper
    def _AddToBaseMap(self, base:dict, baseId:str, baseKey:str, valueKey:str, value:dict) -> None:
        # Ensure the base id exists in the base. (ex, if the floor id exists in floors)
        if baseId not in base:
            # This can happen for all of the cases, areas can not have a floor, devices can not have an area, and entities can not have devices.
            base[baseId] = {}
        # Ensure the list exists in the base. (ex ensure the Areas list exists for the floor id)
        if baseKey not in base[baseId]:
            base[baseId][baseKey] = {}
        # Add this value to the list.
        base[baseId][baseKey][valueKey] = value



    # Returns true if the object is disabled.
    def _IsDisabled(self, obj:dict) -> bool:
        if "disabled_by" in obj and obj["disabled_by"] is not None:
            #name = obj.get("name", "Unknown")
            #self.Logger.debug(f"Home Context - Skipping {name} is disabled by {obj['disabled_by']}.")
            return True
        return False


    # Is exposed to assistant
    def _IsExposeToAssistant(self, obj:dict) -> bool:
        options = obj.get("options", None)
        if options is None:
            return True
        conversation = options.get("conversation", None)
        if conversation is None:
            return True
        return conversation.get("should_expose", True)


    # Copies a property from a source to a destination if it exists.
    def _CopyPropertyIfExists(self, key:str, source:dict, dest:dict, defaultValue=None, copyIfEmpty=False, destKey:str=None):
        if destKey is None:
            destKey = key
        value = source.get(key, None)
        if value is None:
            if defaultValue is not None:
                dest[destKey] = defaultValue
            return
        if isinstance(value, list):
            if len(value) == 0 and copyIfEmpty is False:
                return
        if isinstance(value, dict):
            if len(value) == 0 and copyIfEmpty is False:
                return
        if isinstance(value, str):
            value = self._ClampString(value)
        dest[destKey] = value


    # String helper.
    def _ClampString(self, value:str) -> str:
        if value is None:
            return None
        if len(value) > HomeContext.MaxStringLength:
            return value[0:HomeContext.MaxStringLength]
        return value


    # Attempts to return the results from a HA message.
    def _GetResultsFromHaMsg(self, name:str, root:dict) -> list:
        success = root.get("success", False)
        if not success:
            self.Logger.warning(f"GetHomeContext - {name} request returned success false.")
            return None

        result = root.get("result", None)
        if result is None:
            self.Logger.warning(f"GetHomeContext - {name} request had no results object.")
        return result


    # Returns if the given entity ID is an assist
    def _IsAssistEntityId(self, entityId:str) -> bool:
        # The full id is 'assist_satellite', but this is good enough.
        return entityId is not None and entityId.startswith("assist")


    #
    # State Logic
    #

    # Does a query only for the current states.
    def _QueryCurrentState(self) -> list:
        try:
            # Query for the state
            response = self.HaConnection.SendMsg({"type" : "get_states"}, waitForResponse=True)

            # Check for a failure.
            if response is False:
                self.Logger.warning("Home Context failed to get current state.")
                return None

            # Get the results
            result = self._GetResultsFromHaMsg("states", response)
            if result is None:
                return None
            return result
        except Exception as e:
            Sentry.OnException("Home Context _QueryCurrentState error", e)
        return None


    # Handles a full state response from the server.
    # This returns two things!
    #    1) list - The filtered entities
    #    2) str - The active assist entity id (if there is one, otherwise None)
    def _FilterStateList(self, states:list):

        # No need to take the lock, since this is a read only operation.
        allowedEntityIdMap = self.AllowedEntityIds
        if allowedEntityIdMap is None:
            self.Logger.warning("Home Context - AllowedEntityIds is None, skipping state filter.")

        result = []
        assistActiveEntityId = None
        for s in states:
            # Get the entity ID
            entityId:str = s.get("entity_id", None)
            if entityId is None:
                continue

            # Do a special check for any assist devices.
            if self._IsAssistEntityId(entityId):
                # If the assist has an active state, we will report it as the active entity id
                # States are defined here AssistSatelliteState, but there are other states it can have like "unavailable"
                # So we use an allow list.
                state = s.get("state", None)
                if state is not None:
                    # The state at this point should be processing, but we will allow listening and responding as well.
                    if state == "listening" or state == "processing" or state == "responding":
                        assistActiveEntityId = entityId

            # Ensure it's an allowed entity.
            if allowedEntityIdMap is not None and entityId not in allowedEntityIdMap:
                continue

            # Remove the common things that we know are large and we don't need.
            s.pop("last_reported", None)
            s.pop("last_updated", None)
            s.pop("last_changed", None)
            s.pop("context", None)

            # We try to allow as much as possible, but we filter attributes that are too long.
            # Some images and such have huge urls.
            attributes = s.get("attributes", None)
            if attributes is not None:
                newAttributes = {}
                for a in attributes:
                    # Filter some things we know have little value.
                    # friendly_name is already part of the home context.
                    if a == "id" or a == "icon" or a == "friendly_name" or a == "access_token" or a.find("video") != -1 or a.find("picture") != -1 or a.find("image") != -1:
                        continue
                    if attributes[a] is None:
                        continue
                    if isinstance(attributes[a], str):
                        if len(attributes[a]) > HomeContext.MaxStringLength:
                            continue
                    newAttributes[a] = attributes[a]
                s["attributes"] = newAttributes

            # Add it back to the list.
            result.append(s)

        return result, assistActiveEntityId


    # Builds the live context.
    def _BuildLiveContext(self, activeAssistEntityId:str) -> dict:
        # activeAssistEntityId can be None if there is no active assist.
        assistantDeviceContext:AssistantDeviceContext = None
        if activeAssistEntityId is not None:
            # Find the device context for the active assist entity.
            with self.CacheLock:
                for c in self.AssistantDeviceContexts:
                    if c.EntityId == activeAssistEntityId:
                        assistantDeviceContext = c
                        break

        # Always return the context, even if it's None, so that the model can always expect it.
        # Use a naming format that matches the names in the home context and states list.
        return {
            "RequestLocationContext" : {
                "entity_id" : assistantDeviceContext.EntityId if assistantDeviceContext is not None else "None",
                "device_id" : assistantDeviceContext.EntityId if assistantDeviceContext is not None else "None",
                "area_id" : assistantDeviceContext.AreaId if assistantDeviceContext is not None else "None",
                "floor_id" : assistantDeviceContext.FloorId if assistantDeviceContext is not None else "None",
            }
        }


class HomeContextQueryResult:
    def __init__(self):
        self.Entities:dict = None
        self.Devices:dict = None
        self.Areas:dict = None
        self.Floors:dict = None
        self.Labels:dict = None
