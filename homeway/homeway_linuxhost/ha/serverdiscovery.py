import logging
import requests

from .configmanager import ConfigManager

class ServerDiscoveryResponse:
    def __init__(self, port:int, isHttps:bool) -> None:
        self.Port = port
        self.IsHttps = isHttps


# Home Assistant can be setup on a number of different ports, it's usually 8123, but it can be different.
# This class will scan known local ports for the given IP to see if we can find a Home Assistant server.
class ServerDiscovery:


    def __init__(self, logger:logging.Logger, configManager:ConfigManager) -> None:
        self.Logger = logger
        self.HaConfig = configManager


    # This will search for a Home Assistant server on the given IP or hostname.
    # Providing an access code is ideal, because it allows us to ensure that we are connected to the correct server, by using an authed API call.
    # If a port hint is provided, it will be checked first.
    def SearchForServerPort(self, ipOrHostname:str, accessCode:str = None, portHint:int = None) -> ServerDiscoveryResponse:

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

            # Look for an http server
            for port in ports:
                if self._CheckForHaServer(ipOrHostname, port, accessCode):
                    return ServerDiscoveryResponse(port, False)
            # Look for an https server
            for port in ports:
                if self._CheckForHaServer(ipOrHostname, port, accessCode, True):
                    return ServerDiscoveryResponse(port, True)

        # No server found on the given ports.
        return None


    def _CheckForHaServer(self, ipOrHostname:str, port:int, accessCode:str, useHttps:bool = False, timeoutSec:float = 1.0) -> bool:
        try:
            # Create the base URL for testing
            protocol = "https" if useHttps is True else "http"
            baseUrl = f"{protocol}://{ipOrHostname}:{port}"

            # To start, if we have an access code, we will first try to find the API.
            # If the access code matches, we know this is the right server and it's Home Assistant.
            if accessCode is not None:
                # Use the basic API path to test if it's there.
                # This will return a 200 with a simple json response on success.
                url = f"{baseUrl}/api/"
                headers = {
                    "Authorization" : f"Bearer {accessCode}",
                    "Content-Type" : "application/json",
                }
                self.Logger.debug(f"Searching for the Home Assistant API at {url}")
                try:
                    # It's important to set verify (verify SSL certs) to false, because if the connection is using https, we aren't using a hostname, so the connection will fail otherwise.
                    # This is safe to do, because the connection is either going be over localhost or on the local LAN
                    response = requests.get(url, headers=headers, timeout=timeoutSec, verify=False)
                    # If we know we failed, report false.
                    # On success, we want to try the HTML check next.
                    if response.status_code != 200:
                        return False
                except Exception:
                    # Assume the exception is because there's no server on the port.
                    return False

            # If we get here, the API either was successful or there was no access code.
            # In either case, we want to make sure we can get the html page from the found port as well.
            headers = {
                "Content-Type" : "text/html",
            }
            self.Logger.debug(f"Searching for Home Assistant HTTP at {baseUrl}")
            try:
                # It's important to set verify (verify SSL certs) to false, because if the connection is using https, we aren't using a hostname, so the connection will fail otherwise.
                # This is safe to do, because the connection is either going be over localhost or on the local LAN
                response = requests.get(baseUrl, headers=headers, timeout=timeoutSec, verify=False)
                # On success, we found a good candidate!
                if response.status_code == 200:
                    return True
            except Exception:
                # Assume the exception is because there's no server on the port.
                return False

        except Exception as e:
            self.Logger.error(f"Exception while checking for Home Assistant server at port {port}. Error: {str(e)}")
        return False
