
# Compat between different possible platforms.
class Compat:

    _WebRequestResponseHandler = None
    @staticmethod
    def GetWebRequestResponseHandler():
        return Compat._WebRequestResponseHandler
    @staticmethod
    def SetWebRequestResponseHandler(obj):
        Compat._WebRequestResponseHandler = obj
    @staticmethod
    def HasWebRequestResponseHandler():
        return Compat._WebRequestResponseHandler is not None

    _ServerInfoHandler = None
    @staticmethod
    def GetServerInfoHandler():
        return Compat._ServerInfoHandler
    @staticmethod
    def SetServerInfoHandler(obj):
        Compat._ServerInfoHandler = obj
    @staticmethod
    def HasServerInfoHandler():
        return Compat._ServerInfoHandler is not None
