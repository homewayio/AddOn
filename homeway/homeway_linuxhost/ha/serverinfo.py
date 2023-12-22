import os

# A common class for storing the Home Assistant server information
class ServerInfo:

    ServerIpOrHostname = None
    ServerPort = None
    AccessToken = None

    @staticmethod
    def SetServerInfo(serverIpOrHostname:str, serverPort:int, accessToken_CanBeNone:str=None):
        ServerInfo.serverIpOrHostname = serverIpOrHostname
        ServerInfo.serverPort = serverPort
        ServerInfo.AccessToken = accessToken_CanBeNone

    @staticmethod
    def GetServerIpOrHostnameAndPort() -> str:
        envToken = ServerInfo._GetEnvAccessToken()

        # If we got an access token from the env, we are running in an addon, so return the core path
        if envToken is not None and len(envToken) > 0:
            return "supervisor/core"

        # Otherwise, we are running in a plugin, so return the server ip and port
        return ServerInfo.serverIpOrHostname + ":" + str(ServerInfo.serverPort)

    @staticmethod
    def GetAccessToken() -> str:
        envToken = ServerInfo._GetEnvAccessToken()
        if envToken is not None and len(envToken) > 0:
            return envToken
        return ServerInfo.AccessToken


    @staticmethod
    def _GetEnvAccessToken() -> str:
        return os.environ.get('SUPERVISOR_TOKEN', None)
