import os
import threading
import socket
import json

from homeway.homeway.websocketimpl import Client

from .Util import Util
from .Paths import Paths
from .Logging import Logger
from .Context import Context
from .ConfigFile import ConfigFile

# The goal of this class is the take the context object from the Discovery Gen2 phase to the Phase 3.
class Configure:

    # This is the common service prefix (or word used in the file name) we use for all of our service file names.
    # This MUST be used for all instances running on this device, both local addons.
    # This also MUST NOT CHANGE, as it's used by the Updater logic to find all of the locally running services.
    c_ServiceCommonName = "homeway"

    # This is the default port that Home Assistant runs on.
    c_DefaultHomeAssistantPortStr = "8123"

    # The IP address for a local host server.
    c_LocalHostIpAddressStr = "127.0.0.1"

    def Run(self, context:Context):

        Logger.Header("Starting configuration...")

        # For any non-primary instances, the service will have the number appended.
        serviceSuffixStr = ""
        if context.IsPrimaryInstance is False:
            serviceSuffixStr = context.InstanceId

        # Setup the service name
        context.ServiceName = Configure.c_ServiceCommonName + serviceSuffixStr
        context.ServiceFilePath = os.path.join(Paths.SystemdServiceFilePath, context.ServiceName+".service")

        # Setup the log folder and make sure it exists.
        context.LogFolder = os.path.join(context.AddonFolder, "logs")
        Util.EnsureDirExists(context.LogFolder, context, True)

        # Setup the data folder and make sure it exists.
        context.LocalDataFolder = os.path.join(context.AddonFolder, "data")
        Util.EnsureDirExists(context.LocalDataFolder, context, True)

        # Finally, do the setup of the Home Assistant instance.
        self._EnsureHomeAssistantSetup(context)

        # Report
        Logger.Info(f'Configured. Service: {context.ServiceName}, Path: {context.ServiceFilePath}, LocalStorage: {context.LocalDataFolder}, Logs: {context.LogFolder}')


    def _EnsureHomeAssistantSetup(self, context:Context):
        Logger.Debug("Running ensure config logic.")

        # See if there's a valid config already.
        ip, port, accessToken = ConfigFile.TryToParseConfig(context.AddonFolder)
        if ip is not None and port is not None or accessToken is not None:
            # Check if we can still connect. This can happen if the IP address changes, the API token expires, etc.
            # the user might need to setup the addon again.
            Logger.Info(f"Existing config file found. IP: {ip}:{port}")
            Logger.Info("Checking if we can connect to Home Assistant...")
            success, exception, failedDueToAuth = self._CheckForHomeAssistantConnection(ip, port, accessToken, 10.0)
            if success and exception is None and failedDueToAuth is False:
                Logger.Info("Successfully connected to Home Assistant!")
                return
            else:
                # Let the user keep this connection setup, or try to set it up again.
                Logger.Blank()
                if failedDueToAuth:
                    Logger.Warn(f"We were able to connect to Home Assistant but the Access Token is unauthorized. [{ip}:{port}]")
                else:
                    Logger.Warn(f"No Home Assistant connection found at {ip}:{port}.")
                if Util.AskYesOrNoQuestion("Do you want to setup the Home Assistant connection again?") is False:
                    Logger.Info(f"Keeping the existing Home Assistant connection setup. [{ip}:{port}]")
                    return

        # This will get an ip and port, but not auth.
        ip, port = self._SetupNewHomeAssistantConnection()

        # Get the auth token from the user
        accessToken = self._GetAccessToken(ip, port)

        # Write it out.
        if ConfigFile.UpdateConfig(context, ip, port, accessToken) is False:
            raise Exception("Failed to write config.")
        Logger.Blank()
        Logger.Header("Home Assistant connection successful!")
        Logger.Blank()

    # Helps the user setup a home assistant connection via auto scanning or manual setup.
    # Returns (ip:str, port:str, apiToken:str)
    def _SetupNewHomeAssistantConnection(self):
        Logger.Blank()
        Logger.Blank()
        Logger.Header("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        Logger.Header("        Home Assistant Setup")
        Logger.Header("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        Logger.Blank()
        Logger.Info("The Homeway add-on will now try to search for your Home Assistant server.")
        Logger.Info("If you have any trouble, we are happy to help! Contact us at support@homeway.io")
        Logger.Blank()
        Logger.Info("Searching for local Home Assistant servers... please wait... (about 5 seconds)")

        # Since most users will be installing this on the same device as home assistant, do a special check for local host first.
        # This is the ideal setup for local host severs, since it's faster and the IP will never change.
        success, exception, _ = self._CheckForHomeAssistantConnection(Configure.c_LocalHostIpAddressStr, Configure.c_DefaultHomeAssistantPortStr, None, 10.0)
        if success and exception is None:
            Logger.Blank()
            Logger.Header("We found Home Assistant running on this device.")
            if Util.AskYesOrNoQuestion("Is this the Home Assistant server you want to use?"):
                return (Configure.c_LocalHostIpAddressStr, Configure.c_DefaultHomeAssistantPortStr)

        # If no local host was found, then scan the local LAN for instances.
        foundIps = self._ScanForHomeAssistantInstances()
        if len(foundIps) > 0:
            # Sort them so they present better.
            foundIps = sorted(foundIps)
            # "homeassistant.local", if found, will be sorted to the bottom, put it at the top of the list.
            if foundIps[len(foundIps) -1].startswith("homeassistant"):
                foundIps.insert(0, foundIps.pop())
            Logger.Blank()
            Logger.Info("Home Assistant was found at the following:")
            count = 0
            for ip in foundIps:
                count += 1
                Logger.Info(f"  {count}) {ip}:{Configure.c_DefaultHomeAssistantPortStr}")
            Logger.Info(f"  m) Manually enter the server information")
            Logger.Blank()
            while True:
                response = input("Enter the number next to the Home Assistant server you want to use. Or enter `m` to manually specify the connection details: ")
                response = response.lower().strip()
                if response == "m":
                    # Break to fall through to the manual setup.
                    break
                try:
                    # Parse the input and -1 it, so it aligns with the array length.
                    tempInt = int(response.lower().strip()) - 1
                    if tempInt >= 0 and tempInt < len(foundIps):
                        return (foundIps[tempInt], Configure.c_DefaultHomeAssistantPortStr)
                except Exception as _:
                    pass
                Logger.Warn("Invalid input, try again.")
        else:
            Logger.Info("No local Home Assistant server could be automatically found.")

        # Do the manual setup process.
        ipOrHostname = ""
        port = Configure.c_DefaultHomeAssistantPortStr
        while True:
            try:
                Logger.Blank()
                Logger.Blank()
                Logger.Header("Enter the Hostname or IP Address of your Home Assistant server.")
                Logger.Info(  "The Hostname might look something like `homeassistant.local` or an IP address might look something like `192.168.1.5`")
                ipOrHostname = input("Enter the Hostname or IP: ")
                # Clean up what the user entered. Remove the protocol, trailing GET paths, or port numbers.
                ipOrHostname = ipOrHostname.lower().strip()
                if ipOrHostname.find("://") != -1:
                    ipOrHostname = ipOrHostname[ipOrHostname.find("://")+3:]
                if ipOrHostname.find("/") != -1:
                    ipOrHostname = ipOrHostname[:ipOrHostname.find("/")]
                if ipOrHostname.find(":") != -1:
                    ipOrHostname = ipOrHostname[:ipOrHostname.find(":")]

                Logger.Blank()
                Logger.Header( "Enter the port Home Assistant is running on.")
                Logger.Info  (f"If you don't know the port or want to use the default port ({Configure.c_DefaultHomeAssistantPortStr}), press enter.")
                port = input("Enter Home Assistant Port: ")
                if len(port) == 0:
                    port = Configure.c_DefaultHomeAssistantPortStr

                Logger.Blank()
                Logger.Info(f"Trying to connect to Home Assistant via {ipOrHostname}:{port}...")
                success, exception, _ = self._CheckForHomeAssistantConnection(ipOrHostname, port, None, 10.0)

                # Handle the result.
                if success:
                    return (ipOrHostname, port)
                else:
                    if exception is not None:
                        Logger.Error("Home Assistant connection failed.")
                    else:
                        Logger.Error("Home Assistant connection timed out.")
                    Logger.Warn("Make sure the device is powered on, has an network connection, and the Hostname or IP is correct.")
                    if exception is not None:
                        Logger.Warn(f"Error {str(exception)}")
            except Exception as e:
                Logger.Warn("Failed to setup Home Assistant, try again. "+str(e))


    # Given an ip or hostname and port, this will try to detect if there's a Home Assistant instance.
    # If accessToken is None, then this function will not test auth. And simply return success if the websocket can be opened and HA is detected.
    # If accessToken is not None, then this function will check the connection and also the auth. It will only return success is everything checks out.
    # Returns (success:bool, exception|None, failedDueToBadAuth:bool)
    def _CheckForHomeAssistantConnection(self, ip:str, port:str, accessToken:str = None, timeoutSec:float = 5.0):
        doneEvent = threading.Event()
        lock = threading.Lock()
        result = {}

        # Create the URL
        url = f"ws://{ip}:{port}/api/websocket"

        # States for auth based logins
        # 1 - Connecting
        # 2 - First msg received / auth message sent
        # 3 - Auth success
        result["state"] = 1
        result["failedDueToAuth"] = False

        # Setup the callback functions
        def OnOpened(ws):
            Logger.Debug(f"[{url}] - WS Opened")
        def OnMsg(ws:Client, msg):
            with lock:
                if "success" in result:
                    return
                try:
                    # Try to see if the message looks like the fires home assistant ws message.
                    msgStr = msg.decode('utf-8')
                    Logger.Debug(f"Test [{url}] - WS message `{msgStr}`")
                    msg = json.loads(msgStr)

                    if accessToken is None:
                        # If there is no access token, we consider this a success, that we connected.
                        if "ha_version" in msg:
                            Logger.Debug(f"Test [{url}] - Found Home Assistant message, success!")
                            result["success"] = True
                            doneEvent.set()
                    else:
                        if "type" in msg and msg["type"] == "auth_required":
                            # This is the first HA message, we need to respond with the access token.
                            if result["state"] == 1:
                                Logger.Debug(f"[{url}] - Got HA message, sending auth token.")
                                authMsg= json.dumps({
                                    "type": "auth",
                                    "access_token": accessToken
                                })
                                ws.Send(authMsg.encode('utf-8'), False)
                                result["state"] = 2
                        elif "type" in msg and msg["type"] == "auth_ok":
                            # Auth success!
                            if result["state"] == 2:
                                Logger.Debug(f"[{url}] - Auth success!")
                                result["success"] = True
                                result["state"] = 3
                                doneEvent.set()
                        elif "type" in msg and msg["type"] == "auth_invalid":
                            # Auth failed!
                            result["failedDueToAuth"] = True
                            doneEvent.set()
                except Exception as e:
                    Logger.Debug(f"[{url}] - Exception in message parsing. {str(e)}")
        def OnClosed(ws):
            Logger.Debug(f"Test [{url}] - Closed")
            doneEvent.set()
        def OnError(ws, exception):
            Logger.Debug(f"Test [{url}] - Error: {str(exception)}")
            with lock:
                result["exception"] = exception
            doneEvent.set()

        # Create the websocket
        Logger.Debug(f"Checking for home assistant using the address: `{url}`")
        ws = Client(url, onWsOpen=OnOpened, onWsMsg=OnMsg, onWsError=OnError, onWsClose=OnClosed)
        # It's important that we disable cert checks since the server might have a self signed cert or cert for a hostname that we aren't using.
        # This is safe to do, since the connection will be localhost or on the local LAN
        ws.SetDisableCertCheck(True)
        ws.RunAsync()

        # Wait for the event or a timeout.
        doneEvent.wait(timeoutSec)

        # Get the results before we close the websocket, so that doesn't cause any errors.
        capturedSuccess = False
        capturedEx = None
        failedDueToAuth = False
        with lock:
            # If success doesn't exist, it's failed.
            if "success" in result:
                capturedSuccess = result["success"]
            if "exception" in result:
                capturedEx = result["exception"]
            if "failedDueToAuth" in result:
                failedDueToAuth = result["failedDueToAuth"]

        # If there is an access token but auth failed, don't return success overall.
        if accessToken is not None and failedDueToAuth:
            capturedSuccess = False

        # Ensure the ws is closed
        try:
            ws.Close()
        except Exception:
            pass

        return (capturedSuccess, capturedEx, failedDueToAuth)


    # Scans the subnet for Home Assistant instances.
    # Returns a list of IPs or Hostnames where Home Assistant was found.
    def _ScanForHomeAssistantInstances(self):
        foundIps = []
        try:
            localIp = self._TryToGetLocalIp()
            if localIp is None or len(localIp) == 0:
                Logger.Debug("Failed to get local IP")
                return foundIps
            Logger.Debug(f"Local IP found as: {localIp}")
            if ":" in localIp:
                Logger.Info("IPv6 addresses aren't supported for local discovery.")
                return foundIps
            lastDot = localIp.rfind(".")
            if lastDot == -1:
                Logger.Info("Failed to find last dot in local IP?")
                return foundIps
            ipPrefix = localIp[:lastDot+1]

            counter = 0
            doneThreads = [0]
            totalThreads = 256
            threadLock = threading.Lock()
            doneEvent = threading.Event()
            while counter <= totalThreads:
                # For the first try, we will use the local dns name.
                if counter == 0:
                    fullIp = "homeassistant.local"
                else:
                    fullIp = ipPrefix + str(counter)
                def threadFunc(ip):
                    try:
                        success, _, _ = self._CheckForHomeAssistantConnection(ip, "8123", None, 5.0)
                        with threadLock:
                            if success:
                                foundIps.append(ip)
                            doneThreads[0] += 1
                            if doneThreads[0] == totalThreads:
                                doneEvent.set()
                    except Exception as e:
                        Logger.Error(f"Home Assistant scan failed for {ip} "+str(e))
                t = threading.Thread(target=threadFunc, args=[fullIp])
                t.start()
                counter += 1
            doneEvent.wait()
            return foundIps
        except Exception as e:
            Logger.Error("Failed to scan for Home Assistant instances. "+str(e))
        return foundIps


    def _TryToGetLocalIp(self) -> str:
        # Find the local IP. Works on Windows and Linux. Always gets the correct routable IP.
        # https://stackoverflow.com/a/28950776
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ip = None
        try:
            # doesn't even have to be reachable
            s.connect(('1.1.1.1', 1))
            ip = s.getsockname()[0]
        except Exception:
            pass
        finally:
            s.close()
        return ip


    # Helps the user get an API token for this addon.
    # Returns a string on success, or throws on failure.
    def _GetAccessToken(self, ip:str, port:str) -> str:
        # If the ip is local host, the user can't use it to open the profile page.
        # In that case, replace it with the local IP of this device.
        displayIp = ip
        if displayIp == Configure.c_LocalHostIpAddressStr:
            displayIp = self._TryToGetLocalIp()
            if displayIp is None:
                displayIp = ip
        while True:
            Logger.Blank()
            Logger.Blank()
            Logger.Header("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            Logger.Header("     Home Assistant Access Token")
            Logger.Header("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            Logger.Blank()
            Logger.Header("For Homeway to access your Home Assistant server, you need to create a 'Long-Lived Access Token'.")
            Logger.Blank()
            # Note this text is also duplicated in the docker host homeway_standalone_docker.__main__.py
            Logger.Info("To create a Long-Lived Access Token in Home Assistant, follow these steps:")
            Logger.Info("  1) Open the Home Assistant web UI and go to your user profile:")
            Logger.Header(f"       http://{displayIp}:{port}/profile")
            Logger.Info("  2) On your profile page, click the 'Security' tab at the top.")
            Logger.Info("  3) Scroll down to the bottom, to the 'Long-lived access tokens' box.")
            Logger.Info("  4) Click 'CREATE TOKEN'")
            Logger.Info("  5) Enter any name, something 'Homeway Addon' works just fine.")
            Logger.Info("  6) Copy the access token and paste it into this terminal.")
            Logger.Blank()
            Logger.Warn("Hint: Right-clicking anywhere on the terminal screen will paste text in most terminals.")
            Logger.Blank()
            apiToken = input("Enter your long-lived access token: ")
            apiToken = apiToken.strip()
            Logger.Blank()
            Logger.Info(f"Connecting to your Home Assistant server [{ip}:{port}] and trying to log in...")
            success, exception, failedDueToAuth = self._CheckForHomeAssistantConnection(ip, port, apiToken, 10.0)
            if success and exception is None and failedDueToAuth is False:
                return apiToken
            Logger.Blank()
            if failedDueToAuth:
                Logger.Error("The add-on was able to connect to your Home Assistant server, but the Access Token was invalid.")
            else:
                Logger.Error("The add-on was unable to connect to your Home Assistant server.")
            Logger.Error("Try again. If you need any help contact us at support@homeway.io")
