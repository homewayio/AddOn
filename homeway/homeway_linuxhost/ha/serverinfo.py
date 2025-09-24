import os
import logging
from typing import Any, Optional, Dict

import requests

from homeway.compat import Compat
from homeway.interfaces import IServerInfoHandler

# Since ServerInfo is a static class, we need to create a handler for it for the compat class.
class ServerInfoHandler(IServerInfoHandler):

    #
    # Interface Function!!
    # This must remain the same since it's called by the compat class.
    #
    # Returns the access token, either from the environment or passed from the config.
    def GetAccessToken(self) -> Optional[str]:
        return ServerInfo.GetAccessToken()

    #
    # Interface Function!!
    # This must remain the same since it's called by the compat class.
    #
    # Returns the full <protocol>://<host or ip>:<port> depending on how the access token is setup, either in the docker container or running independently.
    # Takes a string that must be "http" or "ws" depending on the desired protocol. This can't be an enum since it's used over the compat handler API.
    # The protocol will automatically be converted to https or wss from the insecure mode as needed, determined by the server config.
    def GetServerBaseUrl(self, protocol:str) -> str:
        return ServerInfo.GetServerBaseUrl(protocol)


# A common class for storing the Home Assistant server information
class ServerInfo:

    ServerIpOrHostname:str = ""
    ServerPort:int = 0
    ServerUseHttps:bool = False
    AccessToken:Optional[str] = None


    @staticmethod
    def SetServerInfo(serverIpOrHostname:str, serverPort:int, useHttps:bool, accessToken:Optional[str]=None):
        ServerInfo.ServerIpOrHostname = serverIpOrHostname
        ServerInfo.ServerPort = serverPort
        ServerInfo.ServerUseHttps = useHttps
        ServerInfo.AccessToken = accessToken
        # Also make sure we set the compat class with the server info handler.
        Compat.SetServerInfoHandler(ServerInfoHandler())


    # Returns the full <protocol>://<host or ip>:<port> depending on how the access token is setup, either in the docker container or running independently.
    # Takes a string that must be "http" or "ws" depending on the desired protocol. This can't be an enum since it's used over the compat handler API.
    # The protocol will automatically be converted to https or wss from the insecure mode as needed, determined by the server config.
    @staticmethod
    def GetServerBaseUrl(p:str) -> str:
        # If we got an access token from the env, we are running in an addon,
        # so we will use the docker core path. If we are using the docker hostname,
        # We never want to use https, so force it to false.
        localUseHttps = ServerInfo.ServerUseHttps
        envToken = ServerInfo._GetEnvAccessToken()
        if envToken is not None:
            localUseHttps = False

        # Figure out the protocol and apply https if needed.
        protocol = None
        if p == "http":
            protocol = "https" if localUseHttps is True else "http"
        elif p == "ws":
            protocol = "wss" if localUseHttps is True else "ws"
        else:
            raise Exception("Unknown protocol passed to GetServerBaseUrl: "+str(p))

        # If we are running in an addon, return the docker hostname core path.
        # No https or port is needed, as it's a local docker hostname.
        if envToken is not None:
            return f"{protocol}://supervisor/core"

        # Otherwise, we are running in a plugin, so return the server ip and port
        return f"{protocol}://{ServerInfo.ServerIpOrHostname}:{ServerInfo.ServerPort}"


    # Returns the access token, either from the environment or passed from the config.
    @staticmethod
    def GetAccessToken() -> Optional[str]:
        envToken = ServerInfo._GetEnvAccessToken()
        if envToken is not None and len(envToken) > 0:
            return envToken
        return ServerInfo.AccessToken


    # Returns the access token from the environment.
    # If there is no token, this returns None
    @staticmethod
    def _GetEnvAccessToken() -> Optional[str]:
        # Ensure that if we get a token, it's not an empty string.
        token = os.environ.get('SUPERVISOR_TOKEN', None)
        if token is not None and len(token) > 0:
            return token
        return None


    # Tries to call the /api/config api on Home Assistant.
    # Returns a json dict of the result, see: https://developers.home-assistant.io/docs/api/rest/
    # If it fails, it returns None.
    @staticmethod
    def GetConfigApi(logger:logging.Logger, timeoutSec:float=1.0) -> Optional[Dict[str, Any]]:
        try:
            # Ensure we have an API key.
            accessToken = ServerInfo.GetAccessToken()
            if accessToken is None:
                logger.warning("GetConfigApi failed because we have no HA API token.")
                return None

            # Make the request.
            headers = {}
            headers["Authorization"] = "Bearer "+accessToken
            uri = f"{(ServerInfo.GetServerBaseUrl('http'))}/api/config"
            # It's important to set verify (verify SSL certs) to false, because if the connection is using https, we aren't using a hostname, so the connection will fail otherwise.
            # This is safe to do, because the connection is either going be over localhost or on the local LAN
            result = requests.get(uri, headers=headers, timeout=timeoutSec, verify=False)

            # Check the response.
            if result.status_code != 200:
                logger.warning(f"Get config API {result.status_code}")
                return None

            # Return the json
            return result.json()
        except Exception as e:
            logger.warning(f"GetConfigApi failed: {e}")
        return None
