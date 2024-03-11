import os
from enum import Enum


# Defines the types of protocols that can be built for the server url.
class ServerProtocol(Enum):
    Http = 1
    Websocket = 2


# A common class for storing the Home Assistant server information
class ServerInfo:

    ServerIpOrHostname = None
    ServerPort = None
    ServerUseHttps = False
    AccessToken = None


    @staticmethod
    def SetServerInfo(serverIpOrHostname:str, serverPort:int, useHttps:bool, accessToken_CanBeNone:str=None):
        ServerInfo.ServerIpOrHostname = serverIpOrHostname
        ServerInfo.ServerPort = serverPort
        ServerInfo.ServerUseHttps = useHttps
        ServerInfo.AccessToken = accessToken_CanBeNone


    # Returns the full <protocol>://<host or ip>:<port>
    @staticmethod
    def GetServerBaseUrl(p:ServerProtocol) -> str:
        # Figure out the protocol
        protocol = None
        if p == ServerProtocol.Http:
            protocol = "https" if ServerInfo.ServerUseHttps is True else "http"
        elif p == ServerProtocol.Websocket:
            protocol = "wss" if ServerInfo.ServerUseHttps is True else "ws"
        else:
            raise Exception("Unknown protocol passed to GetServerBaseUrl")

        # If we got an access token from the env, we are running in an addon, so return the core path
        envToken = ServerInfo._GetEnvAccessToken()
        if envToken is not None and len(envToken) > 0:
            return f"{protocol}://supervisor/core"

        # Otherwise, we are running in a plugin, so return the server ip and port
        return f"{protocol}://{ServerInfo.ServerIpOrHostname}:{ServerInfo.ServerPort}"


    # Returns the access token, either from the environment or passed from the config.
    @staticmethod
    def GetAccessToken() -> str:
        envToken = ServerInfo._GetEnvAccessToken()
        if envToken is not None and len(envToken) > 0:
            return envToken
        return ServerInfo.AccessToken


    @staticmethod
    def _GetEnvAccessToken() -> str:
        return os.environ.get('SUPERVISOR_TOKEN', None)
