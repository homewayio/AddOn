import logging

from homeway.compat import Compat
from homeway.sentry import Sentry
from homeway.httprequest import HttpRequest
from homeway.customfileserver import CustomFileServer


# The context class we return if we want to handle this request.
class ResponseHandlerContext:
    # Possible context types.
    HomeAssistantHtmlPage = 1
    def __init__(self, t:int) -> None:
        self.Type = t


# Implements the platform specific logic for web request response handler.
class WebRequestResponseHandler:

    # The static instance.
    _Instance = None

    @staticmethod
    def Init(logger:logging.Logger):
        WebRequestResponseHandler._Instance = WebRequestResponseHandler(logger)
        Compat.SetWebRequestResponseHandler(WebRequestResponseHandler._Instance)


    @staticmethod
    def Get():
        return WebRequestResponseHandler._Instance


    def __init__(self, logger:logging.Logger):
        self.Logger = logger


    # !! Interface Function !! This implementation must not change!
    # Given a URL (which can be absolute or relative) check if we might want to edit the response.
    # If no, then None is returned and the call is handled as normal.
    # If yes, some kind of context object must be returned, which will be given back to us.
    #     If yes, the entire response will be read as one full byte buffer, and given for us to deal with.
    def CheckIfResponseNeedsToBeHandled(self, uri:str) -> ResponseHandlerContext:
        try:
            # Parse out only the path.
            path = HttpRequest.ParseOutPath(uri)
            if path is None:
                self.Logger.warn(f"WebRequestResponseHandler failed to parse path from uri: {uri}")

            # Try to detect any load of the main html page.
            # This isn't a perfect list, but for now we think it's safer to do an opt-in.
            # TODO - We could also do this by checking the return type of the http call, checking for HTML. The hard part about that is
            # we still need to only target the HA HTML pages, not other pages like addons might add.
            path = path.lower()
            if path == "/" or path.startswith(("/lovelace", "/auth/authorize", "/map", "/energy", "/logbook", "/config", "/profile", "/todo", "/history")):
                return ResponseHandlerContext(ResponseHandlerContext.HomeAssistantHtmlPage)
        except Exception as e:
            Sentry.Exception(f"CheckIfResponseNeedsToBeHandled failed to parse path from uri: {uri}", e)
        return None


    # !! Interface Function !! This implementation must not change!
    # If we returned a context above in CheckIfResponseNeedsToBeHandled, this will be called after the web request is made
    # and the body is fully read. The entire body will be read into the bodyBuffer.
    # We are able to modify the bodyBuffer as we wish or not, but we must return the full bodyBuffer back to be returned.
    def HandleResponse(self, contextObject:ResponseHandlerContext, bodyBuffer: bytes) -> bytes:
        try:
            if contextObject.Type == ResponseHandlerContext.HomeAssistantHtmlPage:
                return self._HandleHomeAssistantHtmlPage(bodyBuffer)
            self.Logger.error(f"WebRequestResponseHandler tired to handle a context with an unknown Type? {contextObject.Type}")
        except Exception as e:
            Sentry.Exception("WebRequestResponseHandler exception while handling mainsail config.", e)
        return bodyBuffer


    def _HandleHomeAssistantHtmlPage(self, bodyBuffer: bytes) -> bytes:
        # This is a index page, let's inject our js we use to help with when the user's data runs out.
        # Find the </head> tag and insert our config before it.
        headEnd = bodyBuffer.find(b"</head>")
        if headEnd == -1:
            self.Logger.warn("Failed to find </head> tag in index page.")
            return bodyBuffer
        customHeaderInclude = CustomFileServer.Get().GetCustomHtmlHeaderIncludeBytes()
        if customHeaderInclude is None:
            self.Logger.error("Failed to get custom header include from the custom file server, it's not ready yet, but this shouldn't be able to happen!")
            return bodyBuffer
        return bodyBuffer[:headEnd] + customHeaderInclude + bodyBuffer[headEnd:]
