import json
import time
import logging
import threading

from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional

from homeway.hostcommon import HostCommon
from homeway.commandhandler import CommandHandler
from homeway.interfaces import IAccountLinkStatusUpdateHandler
from homeway.sentry import Sentry
from homeway.httprequest import HttpRequest
from homeway.Proto.AddonTypes import AddonTypes

from .config import Config


# Creates a simple web server for users to interact with the plugin from the Home Assistant UI.
class WebServer(IAccountLinkStatusUpdateHandler):

    # A static instance var for the handler class to access this class.
    Instance:"WebServer" = None # type: ignore[reportClassAttributeMissing]

    def __init__(self, logger:logging.Logger, pluginId:str, config:Config, devConfig:Optional[Dict[str,Any]]) -> None:
        WebServer.Instance = self
        self.Logger = logger
        self.PluginId = pluginId
        self.Config = config
        self.AccountConnected = False
        self.IsPendingStartup = True
        self.webServerThread:Optional[threading.Thread] = None

        # This indicates if we are running in dev mode.
        self.RunDevWebServer = self.GetDevConfigContains(devConfig, "RunWebServer")

        # We bind to the default docker ips and use port 45120.
        # The default port for Home Assistant is 8099, but that's used already by some more major software.
        self.HostName = "0.0.0.0"
        self.Port = 45120


    def Start(self, addonType:int) -> None:
        # If we aren't running as an addon and we aren't in dev mode, don't start the web server.
        if addonType != AddonTypes.HaAddon and self.RunDevWebServer is False:
            self.Logger.info("Web server not started, not running in HA addon mode.")
            return

        # Start the web server worker thread.
        self.webServerThread = threading.Thread(target=self._WebServerWorker)
        self.webServerThread.start()


    def RegisterForAccountStatusUpdates(self) -> None:
        # Register for account link callbacks.
        # This is called after startup, because the command handler isn't created until after the web server.
        CommandHandler.Get().RegisterAccountLinkStatusUpdateHandler(self)


    # Called when we are connected and we know if there's an account setup with this addon
    def OnPrimaryConnectionEstablished(self, hasConnectedAccount:bool) -> None:
        self.AccountConnected = hasConnectedAccount
        self.IsPendingStartup = False


    # Interface function
    # Called from the command handler the account link status changes.
    def OnAccountLinkStatusUpdate(self, isLinked:bool) -> None:
        self.AccountConnected = isLinked


    def _WebServerWorker(self) -> None:
        backoff:int = 0
        while True:
            # Try to run the webserver forever.
            webServer:Optional[HTTPServer] = None
            try:
                self.Logger.info(f"Web Server Starting {self.HostName}:{self.Port}")
                webServer = HTTPServer((self.HostName, self.Port), WebServer.WebServerHandler)
                self.Logger.info(f"Web Server Started {self.HostName}:{self.Port}")
                webServer.serve_forever()
            except Exception as e:
                self.Logger.error("Web server exception. "+str(e))

            # If we fail, close it.
            try:
                if webServer is not None:
                    webServer.server_close()
            except Exception as e:
                Sentry.OnException("Failed to close the addon webserver.", e)

            # Try again after some time.
            backoff = min(backoff + 1, 20)
            time.sleep(backoff * 0.5)


    class WebServerHandler(BaseHTTPRequestHandler):

        def _isAllowedIp(self) -> bool:
            if WebServer.Instance.RunDevWebServer:
                return True
            # Check if the IP is the authenticated IP from home assistant. If not, deny it.
            # This IP is brokered by Home Assistant, and it does auth checks before forwarding the requests.
            # Requests must come from 172.30.32.2 IP, they are authenticated by Home Assistant atomically, cool!
            if len(self.client_address) == 0:
                WebServer.Instance.Logger.error("Webserver got a request but we can't find the ip. Denying")
                self.send_response(401)
                self.end_headers()
                return False
            if self.client_address[0] != "172.30.32.2":
                WebServer.Instance.Logger.error(f"Webserver got a request from an invalid ip [{self.client_address[0]}]. Denying")
                self.send_response(401)
                self.end_headers()
                return False
            return True


        def do_POST(self):
            # Check if the IP is allowed.
            if not self._isAllowedIp():
                self.send_response(401)
                self.end_headers()
                return

            # Handle the request.
            try:
                # Handle by path
                pathLower = self.path.lower()
                if pathLower == "/api/remote_access_enabled":
                    # Read the post data.
                    enabled = None
                    try:
                        contentLength = int(self.headers['Content-Length'])
                        postData = self.rfile.read(contentLength)
                        jsonData = json.loads(postData)
                        enabled = jsonData.get("enabled", None)
                        if enabled is None:
                            raise Exception("Missing enabled field")
                        enabled = bool(enabled)
                    except Exception as e:
                        WebServer.Instance.Logger.error("Failed to parse remote access enabled post data. "+str(e))
                        self.send_response(400)
                        self.end_headers()
                        return

                    # Update the remote access enabled setting.
                    WebServer.Instance.Logger.info(f"Setting remote access enabled via API to: {str(enabled)}")
                    HttpRequest.SetRemoteAccessEnabled(enabled)
                    WebServer.Instance.Config.SetBool(Config.HomeAssistantSection, Config.HaEnableRemoteAccess, enabled)

                    # Return success.
                    self.send_response(200)
                    self.end_headers()
                    return
                # If we get here, the path isn't found.
                self.send_response(404)
                self.end_headers()

            except Exception as e:
                WebServer.Instance.Logger.error("Webserver POST exception: "+str(e))
                self.send_response(500)
                self.end_headers()
                return


        def do_GET(self):

            # Check if the IP is allowed.
            if not self._isAllowedIp():
                self.send_response(401)
                self.end_headers()
                return

            # Get if remote access is enabled.
            remoteAccessEnabledChecked = ""
            if HttpRequest.GetRemoteAccessEnabled():
                remoteAccessEnabledChecked = "checked"

            # Send the basic HTML
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            connectingBlockDisplay = "none"
            connectingTimerBool = "false"
            linkAccountBlockDisplay = "none"
            connectedAndReadyBlockDisplay = "none"
            pluginLinkUrl = HostCommon.GetAddPluginUrl(WebServer.Instance.PluginId)
            if WebServer.Instance.IsPendingStartup:
                connectingBlockDisplay = "block"
                connectingTimerBool = "true"
            else:
                if WebServer.Instance.AccountConnected:
                    connectedAndReadyBlockDisplay = "block"
                else:
                    linkAccountBlockDisplay = "block"
                    # Use the timer, so the page will refresh and check if the account is linked.
                    connectingTimerBool = "true"
            html = """
<html>
<head><title>Homeway Control</title>
<style>
    .whiteLink {
        color: white;
        text-decoration: none;
    }
    .whiteLink:hover {
        text-decoration: underline;
    }
    .blueLink {
        color: #0C7BFF;
        text-decoration: none;
        font-weight: bold;
    }
    .subtleText {
        color: #939BA6;
    }
    .featureHolder {
        display: flex;
        flex-direction: column;
        background-color: #282828;
        border-radius: 5px;
        padding: 15px;
        margin-bottom: 20px;
    }
    .featureHeader {
        font-size: 18px;
        font-weight: bold;
        margin-bottom: 5px;
    }
    .featureDetails {
        color: #b5becc;
        margin-bottom: 5px;
    }
    .featureButton {
        font-weight: bold;
        margin-top:10px;
        background-color: #3B82F6;
        color: white;
        border-radius: 5px;
        font-weight: bold; /* Needed for iOS, so the button text isn't bold. */
        transition: 0.5s;
        padding: 20px;
        padding-top:13px;
        padding-bottom:13px;
        text-align: center;
        /* Disable select for all buttons */
        user-select: none; /* supported by Chrome and Opera */
        -webkit-user-select: none; /* Safari */
        -khtml-user-select: none; /* Konqueror HTML */
        -moz-user-select: none; /* Firefox */
        -ms-user-select: none; /* Internet Explorer/Edge */
    }
    .featureButton:hover {
        background-color: #547DEB;
        cursor:pointer;
    }
    .pinkFeatureButton {
        background-color: #A855F7;
    }
    .featureButton:hover {
        background-color: #c689ff;
    }
    .switch {
        position: relative;
        display: inline-block;
        width: 50px;
        height: 27px;
        margin-bottom: 0px;
        margin-left: 10px;
    }
        .switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }
    .slider {
        position: absolute;
        cursor: pointer;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background-color: #6F6F6F;
        -webkit-transition: .4s;
        transition: .4s;
        border-radius: 34px;
    }
        .slider:before {
            position: absolute;
            content: "";
            height: 19px;
            width: 19px;
            left: 5px;
            bottom: 4px;
            background-color: white;
            -webkit-transition: .4s;
            transition: .4s;
            border-radius: 50%;
        }
    /* Can be applied to the "slider" span to show a disable state. */
    .sliderDisabled:before {
        background-color: #2A2C30;
        cursor:not-allowed;
    }
    input:checked + .slider {
        background-color: #7299ff;
    }
    input:focus + .slider {
        box-shadow: 0 0 1px #7299ff;
    }
    input:checked + .slider:before {
        -webkit-transform: translateX(21px);
        -ms-transform: translateX(21px);
        transform: translateX(21px);
    }
</style>
</head>
<body style="background-color: black; color: white; font-family: Roboto,Noto,Noto Sans,sans-serif;">
<div style="display: flex; align-content: center; justify-content: center; margin-top: 30px">
    <div style="background-color:#1C1C1C; border-radius: 5px; padding: 25px; min-width: 300px; max-width:450px">
        <div style="display: flex; justify-content: center; margin-bottom: 20px;">
            <div style="display: flex; flex-direction: column; align-items: center;">
                <!-- Important! Set the height so the page doesn't shift when the image loads on the refresh loop -->
                <img src="https://homeway.io/img/logo_maskable.png" height="70" width="70" style="width: 70px; height: 70px;    border-radius: 10px">
                <div style="display: flex; justify-content: center; font-size: 28px; margin-bottom:10px; margin-top:10px">
                    <!-- this must target open blank or it won't open properly! -->
                    <a href="https://homeway.io/dashboard?ha=1&source=addon_web_ui_link" target="_blank" class="whiteLink">Homeway</a>
                </div>
            </div>
        </div>

        <div style="display: """+ connectingBlockDisplay +""";">
            <div style="display: flex; justify-content: center; align-items: baseline; margin-bottom:5px;">
                <div style="width:10px; height:10px; background-color:#bcdf5c; border-radius:50%; margin-right:5px;"></div>
                <div style="margin-bottom:5px; text-align: center; color:#c0dd72; font-weight: bold;">
                    Connecting To Homeway.io...
                </div>
            </div>
        </div>
        <div style="display: """+linkAccountBlockDisplay+""";">
            <div style="display: flex; justify-content: center; align-items: baseline; margin-bottom:4px;">
                <div style="width:10px; height:10px; background-color:#df5c5c; border-radius:50%; margin-right:5px;"></div>
                <div style="margin-bottom:5px; text-align: center; color:#df5c5c; font-weight: bold;">
                    Addon No Linked To Account
                </div>
            </div>
            <div style="margin-bottom:8px; text-align: center;">
                <b>This addon isn't linked to a Homeway account.</b>
            </div>
            <div style="margin-bottom:10px; text-align: center;">
                Use the button below to finish the addon setup.
            </div>
            <div style="display: flex; justify-content: center;" id="linkAccountButton">
                <div class="featureButton pinkFeatureButton" style="width: 250px;">
                    Link Your Account Now
                </div>
            </div>
        </div>
        <div style="display: """+connectedAndReadyBlockDisplay+""";">
            <div style="display: flex; justify-content: center; align-items: baseline; margin-bottom:5px;">
                <div style="width:10px; height:10px; background-color:#31C591; border-radius:50%; margin-right:5px;"></div>
                <div style="margin-bottom:5px; text-align: center; color:#31C591; font-weight: bold;">
                    Securely Connected
                </div>
            </div>

            <div style="margin-bottom:30px; text-align: center;" class="subtleText">
                <!-- this must target open blank or it won't open properly! -->
                Visit <a href="https://homeway.io/dashboard?ha=1&source=addon_web_ui_link" target="_blank" class="blueLink">Homeway.io</a> for secure and private remote access.
            </div>

            <div class="featureHolder">
               <div style="display: flex; flex-direction: row; justify-content: space-between; align-items: center;">
                    <div>
                        <div class="featureHeader">Enable Remote Access</div>
                        <div class="featureDetails">Disabling remote access still allows Sage, Google Home, &amp; Alexa, and other Homeway features to work.</div>
                    </div>
                    <div style="padding-right:10px">
                        <label class="switch switchClass" >
                            <input type="checkbox" id="enable-remote-access-switch" """+remoteAccessEnabledChecked+""">
                            <span class="slider"></span>
                        </label>
                    </div>
               </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">
                        Sage AI
                    </div>
                    <div class="featureDetails">
                        Free, private, lifelike AI Home Assistant Assist. Including text-to-speech, speech-to-text, and LLM conversational chat integrations.
                    </div>
                </div>
                <div class="pinkFeatureButton featureButton" id="goToSageSetup">
                    Setup Sage AI Now
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">
                        Alexa &amp; Google Assistant
                    </div>
                    <div class="featureDetails">
                        Secure, reliable, no-hassle Alexa &amp; Google Assistant integrations for Home Assistant. Set up in 10 seconds.
                    </div>
                </div>
                <div class="pinkButton featureButton" id="goToAssistantSetup">
                    Setup Alexa &amp; Google Assistant Now
                </div>
                <div class="pinkButton featureButton" id="goToAssistantSetup" style="margin-top:15px;">
                    Control Exposed Devices
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">
                        Home Assistant App
                    </div>
                    <div class="featureDetails">
                        Free, secure, &amp; private remote access for the iPhone and Android Home Assistant apps.
                    </div>
                </div>
                <div class="pinkButton featureButton" id="goToAppSetup">
                    Setup Your Home Assistant App
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">
                        Local Access
                    </div>
                    <div class="featureDetails">
                        Remote access to local LAN services like Node-RED, Unraid, Proxmax, AdGuard, PiHole, &amp; More.
                    </div>
                </div>
                <div class="pinkButton featureButton" id="goToLocalAccessSetup">
                    Setup Local Access
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">
                        Remote Access
                    </div>
                    <div class="featureDetails">
                        Free, secure, &amp; private remote access to your Home Assistant instance from anywhere in the world.
                    </div>
                </div>
                <div class="pinkButton featureButton" id="goToDashboardButton">
                    Go To Your Homeway Dashboard
                </div>
            </div>
        </div>
    </div>
</div>
<script>
    // Wait for the document to be ready.
    (function() {
        document.getElementById("linkAccountButton").onclick = (event) => { window.open('"""+pluginLinkUrl+"""', '_blank').focus(); };
        document.getElementById("goToDashboardButton").onclick = (event) => { window.open("https://homeway.io/dashboard?source=addon_control", '_blank').focus(); };
        document.getElementById("goToAssistantSetup").onclick = (event) => { window.open("https://homeway.io/assistant?source=addon_control", '_blank').focus(); };
        document.getElementById("goToSageSetup").onclick = (event) => { window.open("https://homeway.io/sage?source=addon_control", '_blank').focus(); };
        document.getElementById("goToAppSetup").onclick = (event) => { window.open("https://homeway.io/app?source=addon_control", '_blank').focus(); };
        document.getElementById("goToLocalAccessSetup").onclick = (event) => { window.open("https://homeway.io/localaccess?source=addon_control", '_blank').focus(); };
        const remoteSwitch = document.getElementById('enable-remote-access-switch');
        remoteSwitch.onchange = (event) => {
            const enabled = remoteSwitch.checked;
            fetch("/api/remote_access_enabled", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ enabled: enabled })
            }).then(response => {
                if (!response.ok) {
                    alert("Failed to update remote access setting.");
                    // Revert the switch state
                    remoteSwitch.checked = !enabled;
                }
            }).catch(error => {
                alert("Error updating remote access setting.");
                // Revert the switch state
                remoteSwitch.checked = !enabled;
            });
        };
        if("""+connectingTimerBool+""")
        {
            setInterval(()=> {location.reload();}, 1000)
        }
    })();
</script>
</body>
</html>
"""
            self.wfile.write(bytes(html, 'utf-8'))

    # Tries to load a dev config option as a string.
    # If not found or it fails, this return None
    def GetDevConfigContains(self, devConfig:Optional[Dict[str, str]], value:str) -> bool:
        if devConfig is None:
            return False
        if value in devConfig:
            return True
        return False
