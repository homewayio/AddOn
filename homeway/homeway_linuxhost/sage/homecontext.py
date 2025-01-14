import time
import logging

from homeway.sentry import Sentry

from ..ha.connection import Connection


# Captures the current context and state of the home in a way that can be sent to the server.
class HomeContext:

    # These are also enforced on the server side.
    MaxEntityCount = 1000
    MaxAreasCount = 100


    def __init__(self, logger:logging.Logger, haConnection:Connection):
        self.Logger = logger
        self.HaConnection = haConnection


    # Tries to get current context of the home, like devices, areas, and states.
    # Returns None on failure.
    def GetHomeContext(self) -> dict:
        start = time.time()
        parseStart = 0
        try:
            # Try to get the state and areas from the HA connection.
            # Remember that each of the internal values can be null.
            result = self.HaConnection.GetEntityStateAndAreas()
            if result is None:
                self.Logger.warning("GetHomeContext - No result from GetEntityStateAndAreas.")
                return None

            # This is what we will return.
            # The format of this needs to stay common with what the server expects.
            parseStart = time.time()
            out = {}

            # These are the raw results from HA which has tons of info. We don't want it all, so we filter it.
            if result.States is not None:
                out["States"] = self.HandleStateResponse(result.States)
            else:
                self.Logger.warning("GetHomeContext failed to get current home states.")
            if result.Areas is not None:
                out["AreasAndFloors"] = self.HandleAreasResponse(result.Areas)
            else:
                self.Logger.warning("GetHomeContext failed to get current areas.")

            return out
        except Exception as e:
            Sentry.Exception("GetHomeContext error", e)
        finally:
            self.Logger.debug(f"GetHomeContext - Total Latency: {time.time() - start}s - Parse Latency: {time.time() - parseStart}s - Call Latency: {parseStart - start}s" )
        return None


    # Handles a state response from the server.
    def HandleStateResponse(self, root:dict) -> dict:
        success = root.get("success", False)
        if not success:
            self.Logger.warning("GetHomeContext - GetEntityStateAndAreas request failed failed.")
            return None

        result = root.get("result", None)
        if result is None:
            self.Logger.warning("GetHomeContext - GetEntityStateAndAreas request failed failed.")
            return None

        # We reduce the amount of data we send back to the server, but want to keep the same format.
        # We also want this to be fast, so we do it in place.
        i = 0
        out = []
        for item in result:
            # We only want a certain amount of entities.
            if i >= HomeContext.MaxEntityCount:
                break

            entityId = item.get("entity_id", None)
            # Ensure there's an id or if it's something we want to skip.
            #   Update - These are just software properties, so we don't need them.
            #   Camera - The model can't really do anything with this and they have huge URLs.
            #   Image  - Same as camera.
            #   The rest we just don't need, so don't send them.
            if (entityId is None
                or entityId.startswith("update")
                or entityId.startswith("camera")
                or entityId.startswith("image")
                or entityId.startswith("assist_satellite")
                or entityId.startswith("date")
                or entityId.startswith("datetime")
                or entityId.startswith("stt")
                or entityId.startswith("text")
                or entityId.startswith("time")
                or entityId.startswith("number")
                or entityId.startswith("tts")
                or entityId.startswith("wake_word")
                ):
                continue

            # Remove the common things that we know are large and we don't need.
            item.pop("last_reported", None)
            item.pop("last_updated", None)
            item.pop("last_changed", None)
            item.pop("context", None)
            out.append(item)
            i += 1
        return out


    # Handles a areas response from the server.
    def HandleAreasResponse(self, root:dict) -> dict:
        success = root.get("success", False)
        if not success:
            self.Logger.warning("GetHomeContext - GetEntityStateAndAreas area request failed failed.")
            return None

        result = root.get("result", None)
        if result is None:
            self.Logger.warning("GetHomeContext - GetEntityStateAndAreas area request failed failed.")
            return None

        # We reduce the amount of data we send back to the server, but want to keep the same format.
        # We also want this to be fast, so we do it in place.
        i = 0
        out = []
        for item in result:
            # We only want a certain amount of entities.
            if i >= HomeContext.MaxAreasCount:
                break

            # Remove the common things that we know are large and we don't need.
            item.pop("icon", None)
            item.pop("picture", None)
            item.pop("created_at", None)
            item.pop("modified_at", None)
            out.append(item)
            i += 1
        return out
