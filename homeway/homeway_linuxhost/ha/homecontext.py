import json
import time
import copy
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from homeway.buffer import Buffer
from homeway.sentry import Sentry
from homeway.compression import Compression, CompressionContext, CompressionResult
from homeway.interfaces import IHomeContext

from .connection import Connection
from .eventhandler import EventHandler


# Used to keep track of context for the assistant devices.
class AssistantDeviceContext:
    def __init__(self, entityId:str, deviceId:Optional[str], areaId:Optional[str], floorId:Optional[str]):
        self.EntityId:str = entityId
        self.DeviceId:Optional[str] = deviceId
        self.AreaId:Optional[str] = areaId
        self.FloorId:Optional[str] = floorId


# Captures the current context and state of the home in a way that can be sent to the server.
class HomeContext(IHomeContext):

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
        self.EventHandler.SetHomeContext(self)

        # Setup the worker thread
        self.WorkerGoEvent = threading.Event()
        self.WorkerThread = threading.Thread(target=self._Worker)

        # Setup the cached data
        self.CacheLock = threading.Lock()
        self.CacheUpdatedEvent = threading.Event()

        # These are sent to Sage to give full home context and live state context
        self.SageHomeContextResult:Optional[CompressionResult] = None
        self.SageLiveStateEntityFilter:Optional[Dict[str, str]] = None

        # These cache the full device and entity tree for use by the server and other components.
        self.FullDeviceAndEntityTree:Optional[List[Dict[str, Any]]] = None
        # This is the same entity that are in the full tree, but just dumped into a map for fast lookups.
        self.FullEntityMap:Optional[Dict[str, Dict[str, Any]]] = None

        # This is used to keep track of ALL devices and entities for use by the assistant and other system components.
        self.AssistantDeviceContexts:List[AssistantDeviceContext] = []



    def Start(self):
        # Start our worker thread that maintains state.
        self.WorkerThread.start()


    # Returns the home context, as compressed bytes.
    # Returns None on failure.
    def GetSageHomeContext(self) -> Optional[CompressionResult]:
        with self.CacheLock:
            # If we have a cached version, we are good to go.
            if self.SageHomeContextResult is not None:
                return self.SageHomeContextResult
            self.CacheUpdatedEvent.clear()

        # If we don't have a cached version, try to set the worker to go.
        # and then wait on the cache event.
        self.Logger.info("Home Context doesn't have a context yet, trying to force it...")
        self.WorkerGoEvent.set()

        # Don't wait long, we don't want to block the request.
        # If we don't get something back, the server will just have to wait.
        self.CacheUpdatedEvent.wait(1.0)

        # Return whatever we have or None.
        return self.SageHomeContextResult


    # Returns the full floor -> area -> device -> entity tree.
    def GetFullDeviceAndEntityTree(self, forceRefresh: bool) -> Optional[List[Dict[str, Any]]]:
        with self.CacheLock:
            # If we have a cached version, we are good to go.
            if self.FullDeviceAndEntityTree is not None and not forceRefresh:
                return self.FullDeviceAndEntityTree
            self.CacheUpdatedEvent.clear()

        # If we don't have a cached version, try to set the worker to go.
        # and then wait on the cache event.
        if forceRefresh is False:
            self.Logger.info("Home Context doesn't have a device and entity list, trying to force it...")
        self.WorkerGoEvent.set()

        # Don't wait long, we don't want to block the request.
        # If we don't get something back, the server will just have to wait.
        self.CacheUpdatedEvent.wait(5.0)

        # Return whatever we have or None.
        return self.FullDeviceAndEntityTree


    # Does a live query for the current states and returns them as and a the live context as compression results.
    def GetStatesAndLiveContext(self) -> Tuple[Optional[CompressionResult], Optional[CompressionResult]]:
        start = time.time()
        filterTime = 0.0
        try:
            # Query for the states
            states = self._QueryCurrentState()
            if states is None:
                return None, None

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
            stateCompressionResult:Optional[CompressionResult] = None
            liveContextCompressionResult:Optional[CompressionResult] = None
            with CompressionContext(self.Logger) as context:
                b = stateContextStr.encode("utf-8")
                context.SetTotalCompressedSizeOfData(len(b))
                stateCompressionResult = Compression.Get().Compress(context, Buffer(b))
            if liveContextStr is not None:
                with CompressionContext(self.Logger) as context:
                    b = liveContextStr.encode("utf-8")
                    context.SetTotalCompressedSizeOfData(len(b))
                    liveContextCompressionResult = Compression.Get().Compress(context, Buffer(b))

            # Return the result!
            return stateCompressionResult, liveContextCompressionResult

        except Exception as e:
            Sentry.OnException("Home Context GetStates error", e)
        finally:
            self.Logger.debug(f"Home Context GetStates - Total: {time.time() - start}s - Filter: {time.time() - filterTime}s")
        return None, None


    # Looks up a full entity dict by its entity ID, or None if not found.
    def GetEntityById(self, entityId: str, forceRefresh:bool=False) -> Optional[Dict[str, Any]]:
        # Since this can be used by the state event handler frequently, we use a map for fast lookups.
        # These entities are the exact same objects that are in the full device and entity tree.
        with self.CacheLock:
            # If we have a cached version, we are good to go.
            if self.FullEntityMap is not None and not forceRefresh:
                entry = self.FullEntityMap.get(entityId, None)
                if entry is not None:
                    return entry
            self.CacheUpdatedEvent.clear()

        # If we don't have a cached version, try to set the worker to go.
        # and then wait on the cache event.
        if forceRefresh is False:
            self.Logger.info(f"Home Context doesn't have a entry for {entityId} and entity list, trying to force it...")
        self.WorkerGoEvent.set()

        # Don't wait long, we don't want to block the request.
        # If we don't get something back, the server will just have to wait.
        self.CacheUpdatedEvent.wait(5.0)

        # Try again to get the refreshed entry.
        with self.CacheLock:
            if self.FullEntityMap is not None:
                return self.FullEntityMap.get(entityId, None)
        return None


    # Given a device or entity dict and assistant types, this returns true or false if it's exposed or not.
    # If multiple assistants are checked, only one must be exposed to return true.
    def IsExposeToAssistant(self, obj:Dict[str, Any], checkAlexa:bool=False, checkGoogle:bool=False, checkSage:bool=False) -> bool:
        # We check the options field for the relevant flags.
        # If if the field is missing, HA defines that as NOT being exposed, we tested this to be sure this is the correct behavior.
        options = obj.get("options", None)
        if options is None:
            # If there are no options, it's not exposed.
            return False
        if checkSage:
            # This is what HA uses to toggle on and off the exposure to the assist agents.
            if options.get("conversation", {}).get("should_expose", False) is True:
                return True
        if checkAlexa:
            if options.get("cloud.alexa", {}).get("should_expose", False) is True:
                return True
        if checkGoogle:
            if options.get("cloud.google_assistant", {}).get("should_expose", False) is True:
                return True
        # Default to false if the options don't exist or they are false.
        return False


    # Given a device or entity dict and assistant types, this returns if it's disabled by the user, integration, or other system.
    def IsDisabled(self, obj:Dict[str, Any]) -> bool:
        # We have confirmed that if this field exists, it means the object is disabled.
        # If it's enabled, this field will not exist.
        disabled_by = obj.get("disabled_by", None)
        if disabled_by is not None:
            #name = obj.get("name", "Unknown")
            #self.Logger.debug(f"Home Context - Skipping {name} is disabled by {disabled_by}.")
            return True
        return False


    # This logic needs to be the same as it's done in Home Assistant, to make sure the name matches.
    # Some entities don't have a friendly name, but it's required for us to send to the assistants.
    def MakeFriendlyNameFromEntityId(self, entityId:str) -> Optional[str]:
        # We do the same logic home assistant does here, if there's no friendly name we take the entity id and remove the underscores.
        parts = entityId.split(".")
        if len(parts) != 2 or len(parts[1]) == 0:
            self.Logger.warning(f"Entity {entityId} is missing a friendly name and has an invalid entity id format.")
            return None
        return parts[1].replace("_", " ")


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
                (fullEntitiesCount, entitiesExposedToSageCount) = self._HandleAllObjectsResult(result)

                # Success!
                self.Logger.debug(f"HomeContext worker finished. Total: {time.time() - start}s - Parse: {time.time() - parse}s - Full Entities Found: {fullEntitiesCount} - Exposed to Sage: {entitiesExposedToSageCount}")

                # Success!
                self.MostRecentUpdateSuccess = True

            except Exception as e:
                Sentry.OnException("HomeContext worker error", e)
                # Always sleep on error.
                time.sleep(60)


    # This does a query for all things, basically everything beyond state.
    def _QueryAllObjects(self) -> Optional["HomeContextQueryResult"]:
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
                    if response is not None:
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
    def _HandleAllObjectsResult(self, result:"HomeContextQueryResult") -> Tuple[int, int]:
        # We want to summarize the data down to a small structure that the model can easily understand,
        # and we also use this structure to present the device and entity to the Assistants.
        # Floors are the root bottom level
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

        # Build the floors - this is the root of all objects, so it must contain all devices and entities even if they don't have an area or floor.
        floors:Dict[str, Any] = {}
        # We need to create a "none" floor for areas that don't have a floor or if we failed to find floors.
        # We will always create it now, and prune it later if it's not needed.
        # We only add the fields that are required to be there.
        floors[HomeContext.NoneString] = { "floor_id" : HomeContext.NoneString, "name" : HomeContext.NoneString }

        floorResults = self._GetResultsFromHaMsg("floors", result.Floors)
        if floorResults is not None:
            for f in floorResults:
                # Create our object and get the important ids.
                floor:Dict[str, Any] = {}
                floorId = self._CopyAndGetId("floor_id", f, floor)
                # Add the rest of the data.
                self._CopyPropertyIfExists("aliases", f, floor)
                self._CopyPropertyIfExists("level", f, floor)
                self._CopyPropertyIfExists("name", f, floor)
                # Set it
                floors[floorId] = floor

        # Build the areas.
        # We build these sub dicts so we can easily associate them with the floors and in case something like areas fails, we can still send the floors.
        areas:Dict[str, Any] = {}
        # We need to create a "none" area for devices that don't have a area or if we failed to find areas.
        # We will always create it now, and prune it later if it's not needed.
        # We only add the fields that are required to be there.
        areaId = HomeContext.NoneString
        area = { "area_id" : areaId, "floor_id" : HomeContext.NoneString, "name" : HomeContext.NoneString }
        self._AddToBaseMap(floors, HomeContext.NoneString, "areas", areaId, area)
        areas[areaId] = area

        areaResults = self._GetResultsFromHaMsg("areas", result.Areas)
        if areaResults is not None:
            for a in areaResults:
                # Create our object and get the important ids
                area:Dict[str, Any] = { }
                areaId = self._CopyAndGetId("area_id", a, area)
                floorId = self._CopyAndGetId("floor_id", a, area, warnIfMissing=False)
                # Add the rest of the data.
                self._CopyPropertyIfExists("aliases", a, area)
                self._CopyPropertyIfExists("name", a, area)
                self._CopyPropertyIfExists("labels", a, area, destKey="label_ids")
                # Set it
                self._AddToBaseMap(floors, floorId, "areas", areaId, area)
                areas[areaId] = area

        # Build the devices
        devices:Dict[str, Any] = {}
        # We need to create a "none" device for entities that don't have a area or if we failed to find areas.
        # We will always create it now, and prune it later if it's not needed.
        # We only add the fields that are required to be there.
        deviceId = HomeContext.NoneString
        noneDevice = { "id" : deviceId, "area_id" : HomeContext.NoneString, "name" : HomeContext.NoneString }
        self._AddToBaseMap(areas, HomeContext.NoneString, "devices", deviceId, noneDevice)
        devices[deviceId] = noneDevice

        deviceResults = self._GetResultsFromHaMsg("devices", result.Devices)
        if deviceResults is not None:
            for d in deviceResults:
                # Create our object and get the important ids
                device:Dict[str, Any] = { }
                # Keep the device ID since it can be used for association or for some API calls.
                deviceId = self._CopyAndGetId("id", d, device)
                # Some devices don't have areas, that's fine, we also don't need to copy it into the dest object.
                # If the device doesn't have an area, it will get an areaId of "none".
                areaId = self._CopyAndGetId("area_id", d, device, copyIntoDst=False, warnIfMissing=False)
                # Add the rest of the data.
                self._CopyPropertyIfExists("entry_type", d, device)
                self._CopyPropertyIfExists("manufacturer", d, device)
                self._CopyPropertyIfExists("model", d, device)
                self._CopyPropertyIfExists("name_by_user", d, device)
                self._CopyPropertyIfExists("name", d, device)
                self._CopyPropertyIfExists("labels", d, device, destKey="label_ids")
                # It's important to copy this field so we can filter out disabled devices.
                self._CopyPropertyIfExists("disabled_by", d, device)
                # Set it
                self._AddToBaseMap(areas, areaId, "devices", deviceId, device)
                devices[deviceId] = device

        # Build the entities
        # There's no need to build a none entity, since it's the leaf nodes of the tree.
        entities:Dict[str, Any] = {}
        entityResults = self._GetResultsFromHaMsg("entity", result.Entities)
        if entityResults is not None:
            for e in entityResults:
                # Create our object and get the important ids
                entity:Dict[str, Any] = { }
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
                # Now we add all of the properties we want in the full state tree, but we DON'T want to expose to Sage.
                # The reason is most of these are really large, and Sage doesn't need them.
                self._AddFullStateOptionalEntityProperties(e, entity)
                # Set it
                # Some entities don't have devices, so we add them to the special "none" device under the area.
                if deviceId is None:
                    deviceId = HomeContext.NoneString
                self._AddToBaseMap(devices, deviceId, "entities", entityId, entity)
                entities[entityId] = entity

        # Build the labels - Do this as a list directly.
        # For these, they hang out on their own. The areas, devices, and entities will reference them by id, so this just adding context to the id.
        labelsList:List[Dict[str, Any]] = []
        labelResults = self._GetResultsFromHaMsg("labels", result.Labels)
        if labelResults is not None:
            for labelResult in labelResults:
                # Create our object and get the important ids
                label:Dict[str, Any] = {}
                self._CopyPropertyIfExists("label_id", labelResult, label)
                self._CopyPropertyIfExists("name", labelResult, label)
                self._CopyPropertyIfExists("description", labelResult, label)
                # Set it
                labelsList.append(label)

        # Now that we have the full list of objects, we can prune any of the none objects that aren't used.
        # We need to do this in reverse order, so start with devices, then areas, then floors.
        for f in floors.values():
            areas:Dict[str, Any] = f.get("areas", {})
            for a in areas.values():
                devices = a.get("devices", {})
                if HomeContext.NoneString in devices:
                    # Check if this none device has any entities.
                    noneDeviceObj = devices[HomeContext.NoneString]
                    entities = noneDeviceObj.get("entities", {})
                    if len(entities) == 0:
                        # Remove it
                        del devices[HomeContext.NoneString]
            # After processing all areas, check if the none area is needed.
            if HomeContext.NoneString in areas:
                noneAreaObj = areas[HomeContext.NoneString]
                devices = noneAreaObj.get("devices", {})
                if len(devices) == 0:
                    # Remove it
                    del areas[HomeContext.NoneString]
        # After processing all floors, check if the none floor is needed.
        if HomeContext.NoneString in floors:
            noneFloorObj = floors[HomeContext.NoneString]
            areas = noneFloorObj.get("areas", {})
            if len(areas) == 0:
                # Remove it
                del floors[HomeContext.NoneString]

        # To reduce size, we can convert the maps to lists, since in the map each object is indexed by it's id and then also self contains it's id.
        floorsList = list(floors.values())
        for f in floorsList:
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

        # To build the live context object, we need to keep track of the context of the assist devices.
        assistDeviceContexts:List[AssistantDeviceContext] = []
        for f in floorsList:
            areasList:List[Dict[str, Any]] = f.get("areas", [])
            for a in areasList:
                devicesList:List[Dict[str, Any]] = a.get("devices", [])
                for d in devicesList:
                    foundAssistDevice = False
                    entitiesList:List[Dict[str, Any]] = d.get("entities", [])
                    for e in entitiesList:
                        entityIdLoop:Optional[str] = e.get("entity_id", None)
                        if entityIdLoop is not None:
                            if self._IsAssistEntityId(entityIdLoop):
                                assistDeviceContexts.append(AssistantDeviceContext(entityIdLoop, d.get("id", None), a.get("area_id", None), f.get("floor_id", None)))
                                foundAssistDevice = True
                        if foundAssistDevice:
                            # Break to ensure we only list each device once.
                            break

        # Make a deep copy of the full tree, which is need for the GetFullDeviceAndEntityTree call.
        fullDeviceAndEntityTree = copy.deepcopy(floorsList)

        # We dump all of the entries in the tree into a map for fast lookups.
        fullEntityMap:Dict[str, Dict[str, Any]] = {}
        for f in fullDeviceAndEntityTree:
            for a in f.get("areas", []):
                for d in a.get("devices", []):
                    for e in d.get("entities", []):
                        eId = e.get("entity_id", None)
                        if eId is not None:
                            fullEntityMap[eId] = e

        # Important! - Now we must strip out the extra data we need for the full tree but don't want to send to Sage.
        # This includes:
        #   - Disabled devices and entities
        #   - Entities that are not exposed to Sage
        #   - The optional full state properties we added to the entities.
        entitiesExposedToSageCount = 0
        for f in floorsList:
            areasList:List[Dict[str, Any]] = f.get("areas", [])
            for a in areasList:
                # Make a list of devices to remove.
                deviceIndexToRemove:List[int] = []
                devicesList:List[Dict[str, Any]] = a.get("devices", [])
                for i, d in enumerate(devicesList):
                    # A device can be disabled, but it can't have an assistant exposed state, only entities can.
                    if self.IsDisabled(d):
                        deviceIndexToRemove.append(i)
                        continue
                    # Make a list of entities to remove.
                    entityIndexToRemove:List[int] = []
                    entitiesList:List[Dict[str, Any]] = d.get("entities", [])
                    for i, e in enumerate(entitiesList):
                        # Check to see if we need to remove this entity.
                        if self.IsDisabled(e) or not self.IsExposeToAssistant(e, checkSage=True):
                            entityIndexToRemove.append(i)
                        # Important! AFTER we do our checks (that require the fields to be removed)
                        # ensure we strip out any optional fields that we don't want to send to Sage.
                        self._RemoveFullStateOptionalEntityProperties(e)
                    # Remove them in reverse order.
                    for index in reversed(entityIndexToRemove):
                        del d["entities"][index]
                    entitiesExposedToSageCount += len(d.get("entities", []))
                # Remove them in reverse order.
                for deviceIndex in reversed(deviceIndexToRemove):
                    del a["devices"][deviceIndex]

        # Now that the floorsList list is filtered to things we will send to Sage, we need to make a map
        # of entity ids that we also want to send to Sage for the live state context.
        sageLiveStateUpdateEntityFilter:Dict[str, Any] = {}
        for f in floorsList:
            for a in f.get("areas", []):
                for d in a.get("devices", []):
                    for e in d.get("entities", []):
                        eId = e.get("entity_id", None)
                        if eId is not None:
                            sageLiveStateUpdateEntityFilter[eId] = True

        # Finally, package them into the final object that will be sent to Sage.
        # This is the format that the server expects, so we can't change it.
        homeContext = {
            "floors" : floorsList,
            "labels" : labelsList,
        }
        # Dump it to a string, so there's less work for the request to do.
        # Use separators to reduce size.
        homeContextStr = json.dumps(homeContext, separators=(',', ':'))
        # Now compress it!
        compressionResult = None
        with CompressionContext(self.Logger) as context:
            b = homeContextStr.encode("utf-8")
            context.SetTotalCompressedSizeOfData(len(b))
            compressionResult = Compression.Get().Compress(context, Buffer(b))

        # Lock and swap
        with self.CacheLock:
            # These are the compressed results we send to Sage.
            self.SageHomeContextResult = compressionResult
            self.SageLiveStateEntityFilter = sageLiveStateUpdateEntityFilter
            # These are the device contexts of any device that's an assist device.
            self.AssistantDeviceContexts = assistDeviceContexts
            # This is the full device and entity tree for other components that need it.
            self.FullDeviceAndEntityTree = fullDeviceAndEntityTree
            # This is the full entity map for fast lookups.
            self.FullEntityMap = fullEntityMap
            self.CacheUpdatedEvent.set()

        # Note that the full entity count will be 5-10 less than HA, because there are some special entities that don't get IDs and aren't exposed,
        # like the "Home Assistant" entry in the conversation domain.
        return len(fullEntityMap), entitiesExposedToSageCount


    # Add optional entity properties that are needed for the full state, but not for Sage.
    # We do this in it's own function to ensure we add and remove them before making the Home Context for Sage.
    def _AddFullStateOptionalEntityProperties(self, state:Dict[str, Any], dest:Dict[str, Any]) -> None:
        #
        # IMPORTANT!! - These must be kept in sync with the _RemoveFullStateOptionalEntityProperties function below!!
        #
        # We need this disabled state to do the disabled filtering for Sage in this Home Context.
        self._CopyPropertyIfExists("disabled_by", state, dest)
        # We need the options for the Home Context and State Event Handler can filter out entities that don't need to be sent to assistants.
        # This is also used by the service to check assistant state when it queries for all devices and entities, so it must be included here.
        self._CopyPropertyIfExists("options", state, dest)


    # This should remove ALL OF THE optional entity properties added above!!
    def _RemoveFullStateOptionalEntityProperties(self, state:Dict[str, Any]) -> None:
        #
        # IMPORTANT!! - These must be kept in sync with the _AddFullStateOptionalEntityProperties function above!!
        #
        if "disabled_by" in state:
            del state["disabled_by"]
        if "options" in state:
            del state["options"]


    # Important ID helper
    def _CopyAndGetId(self, key:str, source:Dict[str, Any], dest:Dict[str, Any], copyIntoDst:bool=True, warnIfMissing:bool=True) -> str:
        value = source.get(key, None)
        if value is None:
            if warnIfMissing:
                self.Logger.warning(f"GetHomeContext - Object missing {key}. {json.dumps(source)}")
            value = HomeContext.NoneString
        if copyIntoDst:
            dest[key] = value
        return value


    # Add to base map helper
    def _AddToBaseMap(self, base:Dict[str, Any], baseId:str, baseKey:str, valueKey:str, value:Dict[str, Any]) -> None:
        # Ensure the base id exists in the base. (ex, if the floor id exists in floors)
        if baseId not in base:
            # This can happen for all of the cases, areas can not have a floor, devices can not have an area, and entities can not have devices.
            base[baseId] = {}
        # Ensure the list exists in the base. (ex ensure the Areas list exists for the floor id)
        if baseKey not in base[baseId]:
            base[baseId][baseKey] = {}
        # Add this value to the list.
        base[baseId][baseKey][valueKey] = value


    # Copies a property from a source to a destination if it exists.
    def _CopyPropertyIfExists(self, key:str, source:Dict[str, Any], dest:Dict[str, Any], defaultValue:Optional[Any]=None, copyIfEmpty=False, destKey:Optional[str]=None):
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
    def _GetResultsFromHaMsg(self, name:str, root:Optional[Dict[str, Any]]) -> Optional[Any]:
        if root is None:
            self.Logger.warning(f"GetHomeContext - {name} request had no root object.")
            return None
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
    def _QueryCurrentState(self) -> Optional[List[Dict[str, Any]]]:
        try:
            # Query for the state
            response = self.HaConnection.SendMsg({"type" : "get_states"}, waitForResponse=True)

            # Check for a failure.
            if response is None:
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
    def _FilterStateList(self, states:List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[str]]:

        # No need to take the lock, since this is a read only operation.
        allowedEntityIdFilterMap = self.SageLiveStateEntityFilter
        if allowedEntityIdFilterMap is None:
            self.Logger.warning("Home Context - SageLiveStateEntityFilter is None, skipping state filter.")

        result:List[Dict[str, Any]] = []
        assistActiveEntityId:Optional[str] = None
        for s in states:
            # Get the entity ID
            entityId:Optional[str] = s.get("entity_id", None)
            if entityId is None:
                continue

            # Do a special check for any assist devices.
            if self._IsAssistEntityId(entityId):
                # If the assist has an active state, we will report it as the active entity id
                # States are defined here AssistSatelliteState, but there are other states it can have like "unavailable"
                # So we use an allow list.
                state:Optional[str] = s.get("state", None)
                if state is not None:
                    # The state at this point should be processing, but we will allow listening and responding as well.
                    if state == "listening" or state == "processing" or state == "responding":
                        assistActiveEntityId = entityId

            # Ensure it's an allowed entity.
            if allowedEntityIdFilterMap is not None and entityId not in allowedEntityIdFilterMap:
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
    def _BuildLiveContext(self, activeAssistEntityId:Optional[str]) -> Dict[str, Any]:
        # activeAssistEntityId can be None if there is no active assist.
        assistantDeviceContext:Optional[AssistantDeviceContext] = None
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
        self.Entities:Optional[Dict[str,Any]] = None
        self.Devices:Optional[Dict[str,Any]] = None
        self.Areas:Optional[Dict[str,Any]] = None
        self.Floors:Optional[Dict[str,Any]] = None
        self.Labels:Optional[Dict[str,Any]] = None
