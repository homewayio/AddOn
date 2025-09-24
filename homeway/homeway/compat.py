from typing import Optional
from .interfaces import IWebRequestHandler, IServerInfoHandler

# Compat between different possible platforms.

class Compat:

    _WebRequestResponseHandler:Optional[IWebRequestHandler] = None
    @staticmethod
    def GetWebRequestResponseHandler() -> Optional[IWebRequestHandler]:
        return Compat._WebRequestResponseHandler
    @staticmethod
    def SetWebRequestResponseHandler(obj:IWebRequestHandler):
        Compat._WebRequestResponseHandler = obj


    _ServerInfoHandler:Optional[IServerInfoHandler] = None
    @staticmethod
    def GetServerInfoHandler() -> Optional[IServerInfoHandler]:
        return Compat._ServerInfoHandler
    @staticmethod
    def SetServerInfoHandler(obj:IServerInfoHandler):
        Compat._ServerInfoHandler = obj
