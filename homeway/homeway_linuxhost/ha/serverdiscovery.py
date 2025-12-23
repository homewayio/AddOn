import logging
from typing import Optional
import requests

from .serverinfo import ServerInfo
from .configmanager import ConfigManager
from ..config import Config


class ServerDiscoveryResponse:
    def __init__(self, hostnameOrIp:str, port:int, isHttps:bool, accessToken:Optional[str]) -> None:
        self.HostnameOrIp = hostnameOrIp
        self.Port = port
        self.IsHttps = isHttps
        self.AccessToken: Optional[str] = accessToken


# Home Assistant can be setup on a number of different ports, it's usually 8123, but it can be different.
# This class will scan known local ports for the given IP to see if we can find a Home Assistant server.
class ServerDiscovery:


    def __init__(self, logger:logging.Logger, configManager:ConfigManager) -> None:
        self.Logger = logger
        self.HaConfig = configManager


    # This logic is important, it inits all of the hostnames we use to the correct values depending on the addon install type.
    # This must return a config for the addon to continue starting.
    def GetHomeAssistantServerInfo(self, config:Config) -> ServerDiscoveryResponse:

        # One very special case is if we are running in the Home Assistant addon environment.
        # When this is true, we have special docker hostnames we can use to connect directly to the Home Assistant core.
        # This is much more reliable than trying to use the network IP or hostname.
        # BUT - In some instances it seems the direct docker access via the `homeassistant` hostname can fail or be blocked by configs.
        # So we still test to make sure we can reach it first.
        specialAddonSupervisorAccessToken = ServerInfo.GetAddonDockerSupervisorAccessToken()
        if specialAddonSupervisorAccessToken is not None:
            # In this case, the correct config is:
            #   API & WS -> http://supervisor/core/api/ (with access token from the environment)
            #   HTTP Frontend -> http://homeassistant:8123/
            # The API is handled automatically by the GetApiServerBaseUrl class.
            # So for here, we need to return the HA frontend server info.
            if self._CheckForHaWebAndAPIAccess(
                # Use the expected docker hostnames and ports.
                webIpOrHostname="homeassistant", webPort=8123,
                # Force checking using the supervisor API path.
                apiIpOrHostname=None, apiPort=None, apiFullUrlOverride="supervisor/core/api/",
                accessCode=specialAddonSupervisorAccessToken,
                useHttps=False):
                    self.Logger.info("Detected Home Assistant Addon Environment, using direct docker access to Home Assistant core.")
                    return ServerDiscoveryResponse("homeassistant", 8123, False, specialAddonSupervisorAccessToken)
            else:
                self.Logger.warning("Home Assistant Addon Environment detected, but failed to connect to Home Assistant core via direct docker access. Falling back to config-based discovery.")

        # If we are here, we are running standalone, so we need to get the server info from the config.
        configHomeAssistantIpOrHostname = config.GetStrRequired(Config.HomeAssistantSection, Config.HaIpOrHostnameKey, "127.0.0.1")
        configHomeAssistantPort = config.GetInt(Config.HomeAssistantSection, Config.HaPortKey, 8123)
        configHomeAssistantUseHttps = config.GetBool(Config.HomeAssistantSection, Config.HaUseHttps, False)
        configAccessToken = config.GetStr(Config.HomeAssistantSection, Config.HaAccessTokenKey, None)
        if configAccessToken is None:
            self.Logger.info("No Home Assistant access token found in config or environment.")
        else:
            self.Logger.info("Using Home Assistant access token from config.")

        # Try to find the server on the given IP or hostname.
        discoveryResult = self._SearchForServerDetails(configHomeAssistantIpOrHostname, configHomeAssistantPort, configHomeAssistantUseHttps, configAccessToken)
        if discoveryResult is not None:
            self.Logger.info(f"Discovered Home Assistant server at {discoveryResult.HostnameOrIp}:{discoveryResult.Port}, HTTPS: {discoveryResult.IsHttps}")
            return discoveryResult

        # Report and set defaults.
        self.Logger.error("Failed to discover Home Assistant server on the given IP or hostname and known ports from the config. We will attempt to use what's in the config.")
        if configHomeAssistantPort is None:
            configHomeAssistantPort = 8123
            self.Logger.info(f"No Home Assistant port specified in config, defaulting to {configHomeAssistantPort}")
        if configHomeAssistantUseHttps is None:
            configHomeAssistantUseHttps = False
            self.Logger.info(f"No Home Assistant HTTPS setting specified in config, defaulting to {configHomeAssistantUseHttps}")
        return ServerDiscoveryResponse(configHomeAssistantIpOrHostname, configHomeAssistantPort, configHomeAssistantUseHttps, configAccessToken)


    # This will search for a Home Assistant server on the given IP or hostname.
    # Providing an access code is ideal, because it allows us to ensure that we are connected to the correct server, by using an authed API call.
    # If a port hint is provided, it will be checked first.
    def _SearchForServerDetails(self, ipOrHostname:str, portHint:Optional[int], forceHttpsOnly:Optional[bool], accessCode:Optional[str]) -> Optional[ServerDiscoveryResponse]:

        # Note! We need to be careful with this discovery process, because if we hit a Home Assistant server valid API but have
        # no or bad auth, Home Assistant will show a notification to the user, which is annoying.
        # Since we don't use this path for in HA addon installs, it's less of a concern, but still something to be aware of.

        # We will try all of these ports to see if we can find anything.
        # These are listed in the order of ideal to least ideal.
        # Note that HA doesn't support running behind a proxy thats a base path, so we don't need to worry about that.
        ports = [
            8123,  # The default Home Assistant port, this is what the port will be for most users.
            80,    # Some users might have setup HA to run on the default http port.
            7123,  # This port is used by some dev setups.
            443,   # The default port for HTTPs.
        ]

        # If there is a port we expect to find it on, try that first.
        # Unless it's already the default port.
        if portHint is not None and portHint != ports[0]:
            ports.insert(0, portHint)

        # Try to read the port from the config.
        # On standalone instances, this will always return None.
        # On addons running in HA, if there's a port in the config, this will return it.
        # If there's a point, there's a really good chance this is going to be the right port.
        # Note - We don't read the https status, because we would rather try to find a non https port first if possible,
        # then we will fall back to https if needed.
        configPort = self.HaConfig.ReadHttpPort()
        if configPort is not None:
            self.Logger.info(f"Server discovery found the HTTP server port in the HA config: {configPort}")
            ports.insert(0, configPort)

        # We will try a few different combinations.
        # The most ideal is that we can use the access code to find an http port.
        #   That's the most ideal, because we don't want to waste time with https when using a localhost connection.
        #   We can also use the access code to make sure this is the server we are looking for.
        # If that fails, look for an https server using the access code.
        # If that fails, clear the access code and try again.
        i = 0
        while i < 2:
            i += 1
            if i > 1:
                # One the second pass, we try again with no access code.
                # If there was no access code to begin with, there's no reason to try again.
                if accessCode is None:
                    return None
                accessCode = None

            # Check if the config allows http, if so, try it first.
            if forceHttpsOnly is None or forceHttpsOnly is False:
                # Look for an http server
                # For these searches, the web and api hostname and port are the same.
                for port in ports:
                    if self._CheckForHaWebAndAPIAccess(
                        webIpOrHostname=ipOrHostname, webPort=port,
                        apiIpOrHostname=ipOrHostname, apiPort=port,
                        apiFullUrlOverride=None,
                        accessCode=accessCode, useHttps=False
                        ):
                        return ServerDiscoveryResponse(ipOrHostname, port, False, accessCode)
            # Look for an https server
            for port in ports:
                if self._CheckForHaWebAndAPIAccess(
                    webIpOrHostname=ipOrHostname, webPort=port,
                    apiIpOrHostname=ipOrHostname, apiPort=port,
                    apiFullUrlOverride=None,
                    accessCode=accessCode, useHttps=True
                    ):
                    return ServerDiscoveryResponse(ipOrHostname, port, True, accessCode)

        # No server found on the given ports.
        return None


    def _CheckForHaWebAndAPIAccess(self,
                                   webIpOrHostname:str, webPort:int,
                                   apiIpOrHostname:Optional[str], apiPort:Optional[int],
                                   apiFullUrlOverride:Optional[str],
                                   accessCode:Optional[str], useHttps:bool=False, timeoutSec:float=1.0
                                   ) -> bool:
        try:
            protocol = "https" if useHttps is True else "http"

            # To start, if we have an access code, we will first try to find the API.
            # If the access code matches, we know this is the right server and it's Home Assistant.
            if accessCode is not None:
                # Create the base URL for testing
                apiBaseUrl = f"{protocol}://{apiIpOrHostname}:{apiPort}"

                # Use the basic API path to test if it's there.
                # This will return a 200 with a simple json response on success.
                url = f"{apiBaseUrl}/api/"
                if apiFullUrlOverride is not None:
                    url = apiFullUrlOverride

                headers = {
                    "Authorization" : f"Bearer {accessCode}",
                    "Content-Type" : "application/json",
                }
                self.Logger.info(f"Searching for the Home Assistant API at {url}...")
                try:
                    # It's important to set verify (verify SSL certs) to false, because if the connection is using https, we aren't using a hostname, so the connection will fail otherwise.
                    # This is safe to do, because the connection is either going be over localhost or on the local LAN
                    response = requests.get(url, headers=headers, timeout=timeoutSec, verify=False)
                    # If we know we failed, report false.
                    # On success, we want to try the HTML check next.
                    self.Logger.info(f"Query result for Home Assistant API at {url}, status code {response.status_code}")
                    if response.status_code != 200:
                        return False
                except Exception as e:
                    # Assume the exception is because there's no server on the port.
                    self.Logger.info(f"Query result for Home Assistant API at {url}, exception {str(e)}")
                    return False

            # If we get here, the API either was successful or there was no access code.
            # In either case, we want to make sure we can get the html page from the found port as well.
            webBaseUrl = f"{protocol}://{webIpOrHostname}:{webPort}"
            headers = {
                "Content-Type" : "text/html",
            }
            self.Logger.info(f"Searching for Home Assistant HTTP at {webBaseUrl}...")
            try:
                # It's important to set verify (verify SSL certs) to false, because if the connection is using https, we aren't using a hostname, so the connection will fail otherwise.
                # This is safe to do, because the connection is either going be over localhost or on the local LAN
                response = requests.get(webBaseUrl, headers=headers, timeout=timeoutSec, verify=False)
                self.Logger.info(f"Query result for Home Assistant HTTP at {webBaseUrl}, status code {response.status_code}")
                # On success, we found a good candidate!
                if response.status_code == 200:
                    return True
            except Exception as e:
                self.Logger.info(f"Query result for Home Assistant HTTP at {webBaseUrl}, exception {str(e)}")
                # Assume the exception is because there's no server on the port.
                return False

        except Exception as e:
            self.Logger.error(f"Exception while checking for Home Assistant server at port {apiPort}. Error: {str(e)}")
        return False
