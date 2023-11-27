import json
import logging

from homeway.compat import Compat
from homeway.sentry import Sentry


# The context class we return if we want to handle this request.
class ResponseHandlerContext:
    # Possible context types.
    TODO = 1
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
        # uri = uri.lower()
        # # Handle Mainsail configs
        # if uri.endswith("/config.json"):
        #     return ResponseHandlerContext(ResponseHandlerContext.TODO)
        return None


    # !! Interface Function !! This implementation must not change!
    # If we returned a context above in CheckIfResponseNeedsToBeHandled, this will be called after the web request is made
    # and the body is fully read. The entire body will be read into the bodyBuffer.
    # We are able to modify the bodyBuffer as we wish or not, but we must return the full bodyBuffer back to be returned.
    def HandleResponse(self, contextObject:ResponseHandlerContext, bodyBuffer: bytes) -> bytes:
        try:
            # if contextObject.Type == ResponseHandlerContext.MainsailConfig:
            #     return self._HandleMainsailConfig(bodyBuffer)
            # elif contextObject.Type == ResponseHandlerContext.CameraStreamerWebRTCSdp:
            #     return self._HandleWebRtcSdpResponse(bodyBuffer)
            # else:
            self.Logger.Error("WebRequestResponseHandler tired to handle a context with an unknown Type? "+str(contextObject.Type))
        except Exception as e:
            Sentry.Exception("WebRequestResponseHandler exception while handling mainsail config.", e)
        return bodyBuffer


    def _HandleConfig(self, bodyBuffer:bytes) -> bytes:
        #
        # Note that we identify this file just by dont a .endsWith("/config.json") to the URL. Thus other things could match it
        # and we need to be careful to only edit it if we find what we expect.
        #
        mainsailConfig = json.loads(bodyBuffer.decode("utf8"))
        if "instancesDB" in mainsailConfig:
            # Set mainsail and be sure to clear our any instances.
            mainsailConfig["instancesDB"] = "moonraker"
            mainsailConfig["instances"] = []
            # Older versions struggle to connect to the websocket if we don't set this port as well
            # We can always set it to 443, because we will always have SSL.
            mainsailConfig["port"] = 443
        return json.dumps(mainsailConfig, indent=4).encode("utf8")
