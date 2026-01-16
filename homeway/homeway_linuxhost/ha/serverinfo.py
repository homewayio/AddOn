import os
import time
import logging
import threading
from typing import Any, Optional, Dict

import requests

from homeway.compat import Compat
from homeway.sentry import Sentry
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
    def GetApiServerBaseUrl(self, protocol: str) -> str:
        return ServerInfo.GetApiServerBaseUrl(protocol)


    # Returns if this HA server is setup to allow X-Forwarded-For headers.
    # Ideally we want to set them, but if the server doesn't support them, HA will reject requests with a 400.
    def AllowXForwardedForHeader(self) -> bool:
        return ServerInfo.AllowXForwardedForHeader


# A common class for storing the Home Assistant server information
class ServerInfo:

    ServerIpOrHostname: str = ""
    ServerPort: int = 0
    ServerUseHttps: bool = False
    UserConfigAccessToken: Optional[str] = None
    AllowXForwardedForHeader: bool = False


    @staticmethod
    def SetServerInfo(serverIpOrHostname: str, serverPort: int, useHttps: bool, accessToken: Optional[str]):
        ServerInfo.ServerIpOrHostname = serverIpOrHostname
        ServerInfo.ServerPort = serverPort
        ServerInfo.ServerUseHttps = useHttps
        ServerInfo.UserConfigAccessToken = accessToken
        # Also make sure we set the compat class with the server info handler.
        Compat.SetServerInfoHandler(ServerInfoHandler())


    # Returns the full <protocol>://<host or ip>:<port> depending on the access token is setup, either in the docker container or running independently.
    # Takes a string that must be "http" or "ws" depending on the desired protocol. This can't be an enum since it's used over the compat handler API.
    # The protocol will automatically be converted to https or wss from the insecure mode as needed, determined by the server config.
    @staticmethod
    def GetApiServerBaseUrl(p: str) -> str:
        # If we got an access token from the env, we are running in an addon,
        # so we will use the docker core path. If we are using the docker hostname,
        # We never want to use https, so force it to false.
        localUseHttps = ServerInfo.ServerUseHttps
        specialAddonSupervisorAccessToken = ServerInfo.GetAddonDockerSupervisorAccessToken()
        if specialAddonSupervisorAccessToken is not None:
            localUseHttps = False

        # Figure out the protocol and apply https if needed.
        protocol = None
        if p == "http":
            protocol = "https" if localUseHttps is True else "http"
        elif p == "ws":
            protocol = "wss" if localUseHttps is True else "ws"
        else:
            raise Exception(
                "Unknown protocol passed to GetApiServerBaseUrl: "+str(p))

        # If we are running in an addon, return the docker hostname core path.
        # No https or port is needed, as it's a local docker hostname.
        if specialAddonSupervisorAccessToken is not None:
            return f"{protocol}://supervisor/core"

        # Otherwise, we are running in a plugin, so return the server ip and port
        return f"{protocol}://{ServerInfo.ServerIpOrHostname}:{ServerInfo.ServerPort}"


    # Returns the access token, either from the environment or passed from the config.
    @staticmethod
    def GetAccessToken() -> Optional[str]:
        # Always check for the special addon supervisor token first.
        # And always use it if it's there.
        envSupervisorAccessToken = ServerInfo.GetAddonDockerSupervisorAccessToken()
        if envSupervisorAccessToken is not None and len(envSupervisorAccessToken) > 0:
            return envSupervisorAccessToken
        # Otherwise, return the user config token, which could be None
        return ServerInfo.UserConfigAccessToken


    # If this addon is running in a Home Assistant addon docker container, get the supervisor token from the environment.
    # If this returns a token, it indicates the special state that we are running in an addon in the Home Assistant environment, and we have access to special APIs.
    # We have direct docker hostname bindings to http://supervisor/core for API calls and http://homeasssistant:8123 for http frontend calls.
    # If there is no token, this returns None
    @staticmethod
    def GetAddonDockerSupervisorAccessToken() -> Optional[str]:
        # Ensure that if we get a token, it's not an empty string.
        token = os.environ.get('SUPERVISOR_TOKEN', None)
        if token is not None and len(token) > 0:
            return token
        return None


    # Tries to call the /api/config api on Home Assistant.
    # Returns a json dict of the result, see: https://developers.home-assistant.io/docs/api/rest/
    # If it fails, it returns None.
    @staticmethod
    def GetConfigApi(logger: logging.Logger, timeoutSec: float = 1.0) -> Optional[Dict[str, Any]]:
        try:
            # Ensure we have an API key.
            accessToken = ServerInfo.GetAccessToken()
            if accessToken is None:
                logger.warning(
                    "GetConfigApi failed because we have no HA API token.")
                return None

            # Make the request.
            headers = {}
            headers["Authorization"] = "Bearer "+accessToken
            uri = f"{(ServerInfo.GetApiServerBaseUrl('http'))}/api/config"
            # It's important to set verify (verify SSL certs) to false, because if the connection is using https, we aren't using a hostname, so the connection will fail otherwise.
            # This is safe to do, because the connection is either going be over localhost or on the local LAN
            result = requests.get(uri, headers=headers,
                                  timeout=timeoutSec, verify=False)

            # Check the response.
            if result.status_code != 200:
                logger.warning(f"Get config API {result.status_code}")
                return None

            # Return the json
            return result.json()
        except Exception as e:
            logger.warning(f"GetConfigApi failed: {e}")
        return None


    # As long as the proxy setup is correct in the HA config, we can use X-Forwarded-For to get the real client IP.
    # The addon will set the http HA config values correctly, but HA might need to be restarted before they take effect.
    # If x-forwarded-for is not supported, HA will block the connection with a 400.
    # This will detect if we can use it and set the SupportsXForwardedForHeader flag accordingly.
    @staticmethod
    def DetectXForwardedForHeaderSupportAsync(logger:logging.Logger) -> None:
        # Run this in a separate thread so we don't block anything.
        def threadFunc():
            result = False
            try:
                result = ServerInfo._DetectXForwardedForHeaderSupport(logger)
            except Exception as e:
                Sentry.OnException("_DetectXForwardedForHeaderSupport exception", e)
            ServerInfo.AllowXForwardedForHeader = result
        thread = threading.Thread(target=threadFunc)
        thread.start()


    @staticmethod
    def _DetectXForwardedForHeaderSupport(logger:logging.Logger) -> bool:
        start = time.time()

        # Setup the message used when logging about this.
        notEnabledMsg = "X-Forwarded-For header is NOT enabled on this Home Assistant server, enabling it might improve Homeway functionality. Learn More: https://homeway.io/s/http-config-guide"
        if ServerInfo.GetAddonDockerSupervisorAccessToken() is not None:
            notEnabledMsg = "X-Forwarded-For header is not enabled on this Home Assistant server. The addon will automatically enabled this on the next server restart."

        # Test the API endpoint
        # Use the info from ServerInfo, which must be set by now.
        apiBaseUrl = ServerInfo.GetApiServerBaseUrl("http")
        url = f"{apiBaseUrl}/api/"
        headers = {
            "X-Forwarded-For" : "8.8.8.8",
            "Authorization" : f"Bearer {ServerInfo.GetAccessToken()}",
            "Content-Type" : "application/json",
        }
        logger.debug(f"Testing - X-Forwarded-For api support at {url}...")
        try:
            response = requests.get(url, headers=headers, timeout=0.5, verify=False)
            if response.status_code == 400:
                # 400 is the expected failure code when X-Forwarded-For is not supported.
                logger.info(notEnabledMsg)
                return False
            if response.status_code != 200:
                logger.info(f"X-Forwarded-For test unexpected status code {response.status_code} at {url}, took {time.time() - start:.3f} sec")
                return False
        except Exception as e:
            Sentry.OnException(f"X-Forwarded-For api test result at {url}", e)
            return False
        logger.debug(f"Success - X-Forwarded-For api support at {url}...")

        # Web frontend test.
        protocol = "https" if ServerInfo.ServerUseHttps is True else "http"
        baseUrl = f"{protocol}://{ServerInfo.ServerIpOrHostname}:{ServerInfo.ServerPort}"
        url = f"{baseUrl}/"
        headers = {
            "X-Forwarded-For" : "8.8.8.8",
        }
        logger.debug(f"Testing - X-Forwarded-For frontend support at {url}...")
        try:
            response = requests.get(url, headers=headers, timeout=0.5, verify=False)
            if response.status_code == 400:
                # 400 is the expected failure code when X-Forwarded-For is not supported.
                logger.info(notEnabledMsg)
                return False
            if response.status_code != 200:
                logger.info(f"X-Forwarded-For test unexpected status code {response.status_code} at {url}, took {time.time() - start:.3f} sec")
                return False
            # If we get here, both tests passed!
            logger.info(f"X-Forwarded-For supported by this server and will be used. Took {time.time() - start:.3f} sec")
            return True
        except Exception as e:
            Sentry.OnException(f"X-Forwarded-For frontend test result at {url}", e)
            return False
