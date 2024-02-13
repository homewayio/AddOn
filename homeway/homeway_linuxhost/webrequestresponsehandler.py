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
        return None


    # !! Interface Function !! This implementation must not change!
    # If we returned a context above in CheckIfResponseNeedsToBeHandled, this will be called after the web request is made
    # and the body is fully read. The entire body will be read into the bodyBuffer.
    # We are able to modify the bodyBuffer as we wish or not, but we must return the full bodyBuffer back to be returned.
    def HandleResponse(self, contextObject:ResponseHandlerContext, bodyBuffer: bytes) -> bytes:
        try:
            self.Logger.Error("WebRequestResponseHandler tired to handle a context with an unknown Type? "+str(contextObject.Type))
        except Exception as e:
            Sentry.Exception("WebRequestResponseHandler exception while handling mainsail config.", e)
        return bodyBuffer
