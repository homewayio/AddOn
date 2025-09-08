import time
import json
import base64
import logging
import concurrent.futures

from .streammsgbuilder import StreamMsgBuilder
from .httprequest import HttpRequest
from .httprequest import PathTypes
from .sentry import Sentry

from .Proto.HaApiTarget import HaApiTarget

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

    _Instance = None


    @staticmethod
    def Init(logger):
        CommandHandler._Instance = CommandHandler(logger)


    @staticmethod
    def Get():
        return CommandHandler._Instance


    def __init__(self, logger:logging.Logger):
        self.Logger = logger
        self.ConfigManager = None
        self.AccountLinkStatusUpdateHandler = None


    # Registers the config manager, which is need
    def RegisterConfigManager(self, configManager):
        self.ConfigManager = configManager


    # Get's callbacks when the printer link status changes.
    def RegisterAccountLinkStatusUpdateHandler(self, accountLinkStatusUpdateHandler):
        self.AccountLinkStatusUpdateHandler = accountLinkStatusUpdateHandler


    #
    # Command Handlers
    #

    # The goal here is to keep as much of the common logic as common as possible.
    def ProcessCommand(self, commandPath, jsonObj_CanBeNone) -> "CommandResponse":
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

        # Unknown command
        return CommandResponse.Error(CommandHandler.c_CommandError_UnknownCommand, "The command path didn't match any known commands.")


    #
    # Common Handler Core Logic
    #

    # Returns True or False depending if this request is a Homeway command or not.
    # If it is, HandleCommand should be used to get the response.
    def IsCommandRequest(self, httpInitialContext):
        # Get the path to check if it's a command or not.
        if httpInitialContext.PathType() != PathTypes.Relative:
            return None
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
    def HandleCommand(self, httpInitialContext, postBody_CanBeNone):
        # Get the command path.
        path = StreamMsgBuilder.BytesToString(httpInitialContext.Path())
        if path is None:
            raise Exception("IsCommandHttpRequest Http request has no path field in HandleCommand.")

        # Everything after our prefix is part of the command path
        commandPath = path[len(CommandHandler.c_CommandHandlerPathPrefix):]

        # Parse the args. Args are optional, it depends on the command.
        jsonObj_CanBeNone = None
        try:
            if postBody_CanBeNone is not None:
                jsonObj_CanBeNone = json.loads(postBody_CanBeNone)
        except Exception as e:
            Sentry.OnException("CommandHandler error while parsing command args.", e)
            responseObj = CommandResponse.Error(CommandHandler.c_CommandError_ArgParseFailure, str(e))

        # Handle the command
        responseObj = None
        try:
            responseObj = self.ProcessCommand(commandPath, jsonObj_CanBeNone)
        except Exception as e:
            Sentry.OnException("CommandHandler error while handling command.", e)
            responseObj = CommandResponse.Error(CommandHandler.c_CommandError_ExecutionFailure, str(e))


        # Build the result
        resultBytes = None
        try:
            # Build the common response.
            jsonResponse = {
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
        return HttpRequest.Result(200, headers, StreamMsgBuilder.BytesToString(httpInitialContext.Path()), False, fullBodyBuffer=resultBytes)


    def HandleBatchApiRequestsCommand(self, jsonArgs:dict) -> "CommandResponse":

        # Get the command list
        requestList = jsonArgs.get("Requests", None)
        if requestList is None:
            return CommandResponse.Error(CommandHandler.c_CommandError_ArgParseFailure, "No 'Requests' list provided.")
        if not isinstance(requestList, list):
            return CommandResponse.Error(CommandHandler.c_CommandError_ArgParseFailure, "'Requests' is not a list.")

        # Try to invoke each API call, and make sure to send the results in the same order.
        # Even if a command fails, we want to still run all of the commands.
        def _InvokeApi(request:dict) -> dict:
            start = time.time()
            result = {}
            try:
                # Should be a relative URL string.
                relativeUrl = request.get("Url", None)
                if relativeUrl is None:
                    raise Exception("No 'Url' provided in request.")
                # Should be a string with the http method.
                method = request.get("Method", None)
                if method is None:
                    raise Exception("No 'Method' provided in request.")
                # Optional - Should be a dictionary with the headers.
                headers = request.get("Headers", None)
                # Optional - This should be a base64 encoded byte array of anything.
                data = request.get("Data", None)
                if data is not None:
                    data = base64.b64decode(data)

                # Make the request, be sure to use the API core target to get auth added.
                response = HttpRequest.MakeHttpCall(self.Logger, relativeUrl, PathTypes.Relative, method, headers, data, allowRedirects=False, apiTarget=HaApiTarget.Core)

                # Always try to read the body, if there is any.
                # We don't care if this fails, we just want to try to get the body.
                resultBodyStr = None
                try:
                    response.ReadAllContentFromStreamResponse(self.Logger)
                    resultBodyStr = base64.b64encode(response.FullBodyBuffer).decode(encoding="utf-8")
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
            results = []
            for future in futureList:
                try:
                    # Use a long timeout, just to make sure we don't get stuck forever.
                    # The http call will always use a timeout as well.
                    results.append(future.result(60*2))
                except Exception as e:
                    self.Logger.error(f"HandleBatchApiCallCommand Exception from future: {e}")
                    results.append({"Error":"Failed to execute"})

        return CommandResponse.Success({"Responses":results})


# A helper class that's the result of all ran commands.
class CommandResponse():

    @staticmethod
    def Success(resultDict:dict = None):
        if resultDict is None:
            resultDict = {}
        return CommandResponse(200, resultDict, None)


    @staticmethod
    def Error(statusCode, errorStr_CanBeNull):
        return CommandResponse(statusCode, None, errorStr_CanBeNull)


    def __init__(self, statusCode, resultDict, errorStr_CanBeNull):
        self.StatusCode = statusCode
        self.ResultDict = resultDict
        self.ErrorStr = errorStr_CanBeNull
