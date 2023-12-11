
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
