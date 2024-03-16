import os
import logging
from enum import Enum

import requests


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
        # If we got an access token from the env, we are running in an addon,
        # so we will use the docker core path. If we are using the docker hostname,
        # We never want to use https, so force it to false.
        localUseHttps = ServerInfo.ServerUseHttps
        envToken = ServerInfo._GetEnvAccessToken()
        if envToken is not None:
            localUseHttps = False

        # Figure out the protocol and apply https if needed.
        protocol = None
        if p == ServerProtocol.Http:
            protocol = "https" if localUseHttps is True else "http"
        elif p == ServerProtocol.Websocket:
            protocol = "wss" if localUseHttps is True else "ws"
        else:
            raise Exception("Unknown protocol passed to GetServerBaseUrl")

        # If we are running in an addon, return the docker hostname core path.
        # No https or port is needed, as it's a local docker hostname.
        if envToken is not None:
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


    # Returns the access token from the environment.
    # If there is no token, this returns None
    @staticmethod
    def _GetEnvAccessToken() -> str:
        # Ensure that if we get a token, it's not an empty string.
        token = os.environ.get('SUPERVISOR_TOKEN', None)
        if token is not None and len(token) > 0:
            return token
        return None


    # Tries to call the /api/config api on Home Assistant.
    # Returns a json dict of the result, see: https://developers.home-assistant.io/docs/api/rest/
    # If it fails, it returns None.
    @staticmethod
    def GetConfigApi(logger:logging.Logger, timeoutSec:float = 1.0) -> dict:
        try:
            # Ensure we have an API key.
            accessToken = ServerInfo.GetAccessToken()
            if accessToken is None:
                logger.warn("GetConfigApi failed because we have no HA API token.")
                return None

            # Make the request.
            headers = {}
            headers["Authorization"] = "Bearer "+accessToken
            uri = f"{(ServerInfo.GetServerBaseUrl(ServerProtocol.Http))}/api/config"
            # It's important to set verify (verify SSL certs) to false, because if the connection is using https, we aren't using a hostname, so the connection will fail otherwise.
            # This is safe to do, because the connection is either going be over localhost or on the local LAN
            result = requests.get(uri, headers=headers, timeout=timeoutSec, verify=False)

            # Check the response.
            if result.status_code != 200:
                logger.warn(f"Get config API {result.status_code}")
                return None

            # Return the json
            return result.json()
        except Exception as e:
            logger.warn(f"GetConfigApi failed: {e}")
        return None
