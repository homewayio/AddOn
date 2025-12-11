import time
import json
import base64
import logging
import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from .interfaces import IConfigManager, IAccountLinkStatusUpdateHandler, IHomeContext, IHomeAssistantWebSocket
from .streammsgbuilder import StreamMsgBuilder
from .httpresult import HttpResult
from .httprequest import PathTypes, HttpRequest
from .sentry import Sentry
from .buffer import Buffer

from .Proto.HaApiTarget import HaApiTarget
from .Proto.HttpInitialContext import HttpInitialContext


# A helper class that's the result of all ran commands.
class CommandResponse():

    @staticmethod
    def Success(resultDict:Optional[Dict[str,Any]]=None):
        if resultDict is None:
            resultDict = {}
        return CommandResponse(200, resultDict, None)


    @staticmethod
    def Error(statusCode:int, errorStr_CanBeNull:Optional[str]=None):
        return CommandResponse(statusCode, None, errorStr_CanBeNull)


    def __init__(self, statusCode:int, resultDict:Optional[Dict[str,Any]], errorStr_CanBeNull:Optional[str]):
        self.StatusCode = statusCode
        self.ResultDict = resultDict
        self.ErrorStr = errorStr_CanBeNull


#
# Platform Command Handler Interface
#
# This interface provides the platform specific code for command handlers
# Each platform MUST implement this interface and MUST implement the function signatures in the same way.
#
# This class is responsible for handling Commands.
#
class CommandHandler:

    # The prefix all commands must use to be handled as a command.
    # This must be lowercase, to match the lower() we call on the incoming path.
    # This must end with a /, so it's the correct length when we remove the prefix.
    c_CommandHandlerPathPrefix = "/homeway-command-api/"

    #
    # Common Errors
    #
    # These are also defined in the service and need to stay in sync.
    #
    # These are all command system errors.
    c_CommandError_UnknownFailure = 750
    c_CommandError_ArgParseFailure = 751
    c_CommandError_ExecutionFailure = 752
    c_CommandError_ResponseSerializeFailure = 753
    c_CommandError_UnknownCommand = 754

    _Instance:"CommandHandler" = None #pyright: ignore[reportAssignmentType]


    @staticmethod
    def Init(logger:logging.Logger):
        CommandHandler._Instance = CommandHandler(logger)


    @staticmethod
    def Get() -> "CommandHandler":
        return CommandHandler._Instance


    def __init__(self, logger:logging.Logger):
        self.Logger = logger
        self.ConfigManager:Optional[IConfigManager] = None
        self.HomeContext:Optional[IHomeContext] = None
        self.AccountLinkStatusUpdateHandler:Optional[IAccountLinkStatusUpdateHandler] = None
        self.HaWebSocketCon:Optional[IHomeAssistantWebSocket] = None


    # Registers the config manager, which is need
    def RegisterConfigManager(self, configManager:IConfigManager):
        self.ConfigManager = configManager


    # Registers the home context manager, which is needed for some commands.
    def RegisterHomeContext(self, homeContext:IHomeContext):
        self.HomeContext = homeContext


    # Registers the Home Assistant WebSocket connection, which is needed for some commands.
    def RegisterHomeAssistantWebsocketCon(self, haWebSocketCon:IHomeAssistantWebSocket):
        self.HaWebSocketCon = haWebSocketCon


    # Get's callbacks when the printer link status changes.
    def RegisterAccountLinkStatusUpdateHandler(self, accountLinkStatusUpdateHandler:IAccountLinkStatusUpdateHandler):
        self.AccountLinkStatusUpdateHandler = accountLinkStatusUpdateHandler


    #
    # Command Handlers
    #

    # The goal here is to keep as much of the common logic as common as possible.
    def ProcessCommand(self, commandPath:str, jsonObj_CanBeNone:Optional[Dict[str, Any]]) -> CommandResponse:
        # To lower, to match any case.
        commandPathLower = commandPath.lower()
        if commandPathLower.startswith("ping"):
            return CommandResponse.Success({"Message":"Pong"})

        # Handle the batch API call command. Used by Sage to be more efficient.
        if commandPathLower.startswith("batch-web-requests"):
            if jsonObj_CanBeNone is None:
                return CommandResponse.Error(CommandHandler.c_CommandError_ArgParseFailure, "No arguments provided.")
            if self.ConfigManager is None:
                return CommandResponse.Error(CommandHandler.c_CommandError_ExecutionFailure, "No config manager.")
            return self.HandleBatchApiRequestsCommand(jsonObj_CanBeNone)

        # Can be used to make any Home Assistant WebSocket API call.
        if commandPathLower.startswith("ha-websocket-api-call"):
            if jsonObj_CanBeNone is None:
                return CommandResponse.Error(CommandHandler.c_CommandError_ArgParseFailure, "No arguments provided.")
            if self.HaWebSocketCon is None:
                return CommandResponse.Error(CommandHandler.c_CommandError_ExecutionFailure, "No Home Assistant WebSocket connection.")
            haVersion = self.HaWebSocketCon.GetHomeAssistantVersionString()
            result = self.HaWebSocketCon.SendAndReceiveMsg(jsonObj_CanBeNone)
            successful = result is not None
            return CommandResponse.Success({"Success": successful, "HaVersion": haVersion, "Result": result})

        # Returns the Home Assistant version string, if known.
        if commandPathLower.startswith("get-ha-version"):
            if self.HaWebSocketCon is None:
                return CommandResponse.Error(CommandHandler.c_CommandError_ExecutionFailure, "No Home Assistant WebSocket connection.")
            haVersion = self.HaWebSocketCon.GetHomeAssistantVersionString()
            successful = haVersion is not None
            return CommandResponse.Success({"Success": successful, "HaVersion": haVersion})

        # restart-if-needed - Deprecated 1.0.5 (3/16/2024) for `get-config-status`
        # Returns this addon's status with the config. This works for both container and standalone addons.
        if commandPathLower.startswith("get-config-status"):
            needsRestartForAssistantConfigs = False
            canEditConfig = False
            if self.ConfigManager is not None:
                # Check all of the other status before NeedsRestart, since that will restart HA if needed.
                canEditConfig = self.ConfigManager.CanEditConfig()
                needsRestartForAssistantConfigs = self.ConfigManager.NeedsRestart()
            return CommandResponse.Success(
                {
                    "CanEditConfig" : canEditConfig,
                    "NeedsRestartForAssistantConfigs": needsRestartForAssistantConfigs,
                })

        # Used to tell the addon that there's an account linked to this addon. Mostly used to update the webserver page.
        if commandPathLower.startswith("update-account-link-status"):
            if jsonObj_CanBeNone is None:
                return CommandResponse.Error(CommandHandler.c_CommandError_ArgParseFailure, "No arguments provided.")
            if self.AccountLinkStatusUpdateHandler is None:
                return CommandResponse.Error(CommandHandler.c_CommandError_ExecutionFailure, "No account link update handler.")
            self.AccountLinkStatusUpdateHandler.OnAccountLinkStatusUpdate(jsonObj_CanBeNone["IsLinked"])
            return CommandResponse.Success()

        # Used for Assistant device control
        # This must return the full entity tree.
        if commandPathLower.startswith("get-full-device-and-entity-tree"):
            # Read the optional force refresh arg.
            forceRefresh = False
            if jsonObj_CanBeNone is not None:
                forceRefresh = bool(jsonObj_CanBeNone.get("ForceRefresh", False))
            if self.HomeContext is None:
                return CommandResponse.Error(CommandHandler.c_CommandError_ExecutionFailure, "No home context.")
            allEntities = self.HomeContext.GetFullDeviceAndEntityTree(forceRefresh)
            successful = allEntities is not None
            return CommandResponse.Success({"Success": successful, "Floors": allEntities})

        # Unknown command
        return CommandResponse.Error(CommandHandler.c_CommandError_UnknownCommand, "The command path didn't match any known commands.")


    #
    # Common Handler Core Logic
    #

    # Returns True or False depending if this request is a Homeway command or not.
    # If it is, HandleCommand should be used to get the response.
    def IsCommandRequest(self, httpInitialContext:HttpInitialContext) -> bool:
        # Get the path to check if it's a command or not.
        if httpInitialContext.PathType() != PathTypes.Relative:
            return False
        path = StreamMsgBuilder.BytesToString(httpInitialContext.Path())
        if path is None:
            raise Exception("IsCommandHttpRequest Http request has no path field in IsCommandRequest.")
        pathLower = path.lower()
        # If the path starts with our special prefix, it's for us!
        return pathLower.startswith(CommandHandler.c_CommandHandlerPathPrefix)


    # Handles a command and returns an HttpResult
    #
    # Note! It's very important that the HttpResult has all of the properties the generic system expects! For example,
    # it must have the FullBodyBuffer (similar to the snapshot helper) and a valid response object JUST LIKE the requests lib would return.
    #
    def HandleCommand(self, httpInitialContext:HttpInitialContext, postBody:Optional[Buffer]) -> HttpResult:
        # Parse the command path and the optional json args.
        commandPath:str = ""
        jsonObj:Optional[Dict[str, Any]] = None
        responseObj:Optional[CommandResponse] = None
        try:
            # Get the command path and json args, the json object can be null if there are no args.
            commandPath, _, jsonObj = self._GetPathAndJsonArgs(httpInitialContext, postBody)
        except Exception as e:
            Sentry.OnException("CommandHandler error while parsing command args.", e)
            responseObj = CommandResponse.Error(CommandHandler.c_CommandError_ArgParseFailure, str(e))

        # If the args parse was successful, try to handle the command.
        if responseObj is None:
            try:
                responseObj = self.ProcessCommand(commandPath, jsonObj)
            except Exception as e:
                Sentry.OnException("CommandHandler error while handling command.", e)
                responseObj = CommandResponse.Error(CommandHandler.c_CommandError_ExecutionFailure, str(e))

        if responseObj is None:
            responseObj = CommandResponse.Error(CommandHandler.c_CommandError_ExecutionFailure, str("No response object returned."))

        # Build the result
        resultBytes:Optional[bytes] = None
        try:
            # Build the common response.
            jsonResponse:Dict[str, Any] = {
                "Status" : responseObj.StatusCode
            }
            if responseObj.ErrorStr is not None:
                jsonResponse["Error"] = responseObj.ErrorStr
            if responseObj.ResultDict is not None:
                jsonResponse["Result"] = responseObj.ResultDict

            # Serialize to bytes
            resultBytes = json.dumps(jsonResponse).encode(encoding="utf-8")

        except Exception as e:
            Sentry.OnException("CommandHandler failed to serialize response.", e)
            # Use a known good json object for this error.
            resultBytes = json.dumps(
                {
                    "Status": CommandHandler.c_CommandError_ResponseSerializeFailure,
                    "Error":"Serialize Response Failed"
                }).encode(encoding="utf-8")

        # Build the full result
        # Make sure to set the content type, so the response can be compressed.
        headers = {
            "Content-Type": "text/json"
        }
        url = StreamMsgBuilder.BytesToString(httpInitialContext.Path())
        if url is None:
            url = "Unknown"
        return HttpResult(200, headers, url, False, fullBodyBuffer=Buffer(resultBytes))


    def HandleBatchApiRequestsCommand(self, jsonArgs:Dict[str, Any]) -> CommandResponse:

        # Get the command list
        requestList:Optional[List[Dict[str, Any]]] = jsonArgs.get("Requests", None)
        if requestList is None:
            return CommandResponse.Error(CommandHandler.c_CommandError_ArgParseFailure, "No 'Requests' list provided.")
        if not isinstance(requestList, list):
            return CommandResponse.Error(CommandHandler.c_CommandError_ArgParseFailure, "'Requests' is not a list.")

        # Try to invoke each API call, and make sure to send the results in the same order.
        # Even if a command fails, we want to still run all of the commands.
        def _InvokeApi(request:Dict[str, Any]) -> Dict[str, Any]:
            start = time.time()
            result:Dict[str, Any] = {}
            try:
                # Should be a relative URL string.
                relativeUrl:Optional[str] = request.get("Url", None)
                if relativeUrl is None:
                    raise Exception("No 'Url' provided in request.")
                # Should be a string with the http method.
                method:Optional[str] = request.get("Method", None)
                if method is None:
                    raise Exception("No 'Method' provided in request.")
                # Optional - Should be a dictionary with the headers.
                headers:Optional[Dict[str, str]] = request.get("Headers", None)
                # Optional - This should be a base64 encoded byte array of anything.
                data:Optional[bytes] = request.get("Data", None)
                dataBuffer:Optional[Buffer] = None
                if data is not None:
                    dataBuffer = Buffer(base64.b64decode(data))

                # Make the request, be sure to use the API core target to get auth added.
                response = HttpRequest.MakeHttpCall(self.Logger, relativeUrl, PathTypes.Relative, method, headers, dataBuffer, allowRedirects=False, apiTarget=HaApiTarget.Core)
                if response is None:
                    raise Exception("HttpRequest.MakeHttpCall returned None.")

                # Always try to read the body, if there is any.
                # We don't care if this fails, we just want to try to get the body.
                resultBodyStr = None
                try:
                    response.ReadAllContentFromStreamResponse(self.Logger)
                    fullBodyBuffer = response.FullBodyBuffer
                    if fullBodyBuffer is None:
                        raise Exception("No FullBodyBuffer in response.")
                    resultBodyStr = base64.b64encode(fullBodyBuffer.GetBytesLike()).decode(encoding="utf-8")
                except Exception:
                    pass

                # Package the result
                result["StatusCode"] = int(response.StatusCode)
                result["Body"] = resultBodyStr
            except Exception as e:
                self.Logger.error(f"HandleBatchApiCallCommand Exception from command: {e}")
                result["Error"] = "Exception in making request."
            result["DurationMs"] = int((time.time()-start) * 1000)
            return result


        # Run all of the commands in parallel.
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            # Debug helper
            if self.Logger.isEnabledFor(logging.DEBUG):
                for request in requestList:
                    data = request.get("Data", None)
                    if data is not None:
                        data = base64.b64decode(data)
                    self.Logger.debug(f"HandleBatchApiCallCommand Request: {json.dumps(request)} - Data: {data}")

            # Submit all tasks
            futureList = {pool.submit(_InvokeApi, request): request for request in requestList}

            # Wait for all tasks to complete and process results
            # Wait for them in order, since we need to send the results back in order.
            results:List[Dict[str, Any]] = []
            for future in futureList:
                try:
                    # Use a long timeout, just to make sure we don't get stuck forever.
                    # The http call will always use a timeout as well.
                    results.append(future.result(60*2))
                except Exception as e:
                    self.Logger.error(f"HandleBatchApiCallCommand Exception from future: {e}")
                    results.append({"Error":"Failed to execute"})

        return CommandResponse.Success({"Responses":results})


    # A helper to parse the context and json args. Throws if it fails!
    def _GetPathAndJsonArgs(self, httpInitialContext:HttpInitialContext, postBody:Optional[Buffer]) -> Tuple[str, str, Optional[Dict[str, Any]]]:
        # Get the command path.
        path = StreamMsgBuilder.BytesToString(httpInitialContext.Path())
        if path is None:
            raise Exception("IsCommandHttpRequest Http request has no path field in HandleCommand.")

        # Everything after our prefix is part of the command path
        commandPath = path[len(CommandHandler.c_CommandHandlerPathPrefix):]
        commandPathLower = commandPath.lower()

        # Parse the args. Args are optional, it depends on the command.
        # Note some of these commands can also be GET requests, so we need to handle that.
        jsonObj:Optional[Dict[str, Any]] = None

        # Parse the POST body if there is one.
        if postBody is not None:
            jsonObj = json.loads(postBody.GetBytesLike())

        # If there is no json object, try for get args.
        if jsonObj is None:
            # This will return None if there are no args.
            # Use the cased version of the string, so get args keep the correct case.
            jsonObj = self._ParseGetArgsAsJson(commandPath)
        return (commandPath, commandPathLower,  jsonObj)


    # If there are GET args, this will parse them into a json object where all values as strings
    # If there are no args, this will return None.
    def _ParseGetArgsAsJson(self, commandPath:str) -> Optional[Dict[str, str]]:
        # We need to remove the ? and split on & to get the args.
        if "?" not in commandPath:
            return None
        try:
            args = commandPath.split("?")[1]
            # Split on & to get the args.
            args = args.split("&")
            # Parse each arg and add it to the jsonObj.
            jsonObj:Dict[str, str] = {}
            for i in args:
                # Split on = to get the key and value.
                keyValue = i.split("=")
                if len(keyValue) != 2:
                    self.Logger.warning("CommandHandler failed to parse args, invalid key value pair: " + i)
                    continue
                else:
                    # Ensure the key is always lower case, but don't mess with the value, things like passwords might need to be case sensitive.
                    key = (str(keyValue[0])).lower()
                    # The value needs to be URL escaped, so we need to decode it.
                    value = unquote(str(keyValue[1]))
                    jsonObj[key] = value
            return jsonObj
        except Exception as e:
            Sentry.OnException("CommandHandler error while parsing GET command args.", e)
        return None
