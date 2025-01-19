import time
import logging
import threading

from http.server import HTTPServer, BaseHTTPRequestHandler

from homeway.hostcommon import HostCommon
from homeway.commandhandler import CommandHandler
from homeway.sentry import Sentry

# Creates a simple web server for users to interact with the plugin from the Home Assistant UI.
class WebServer:

    # A static instance var for the handler class to access this class.
    Instance = None

    def __init__(self, logger:logging.Logger, pluginId, devConfig_CanBeNone) -> None:
        WebServer.Instance = self
        self.Logger = logger
        self.PluginId = pluginId
        self.AccountConnected = False
        self.IsPendingStartup = True
        self.webServerThread = None

        # Requests must come from 172.30.32.2 IP, they are authenticated by Home Assistant atomically, cool!
        self.AllowAllIps = self.GetDevConfigStr(devConfig_CanBeNone, "WebServerAllowAllIps") is not None
        # We bind to the default docker ips and use port 45120.
        # The default port for Home Assistant is 8099, but that's used already by some more major software.
        self.HostName = "0.0.0.0"
        self.Port = 45120


    def Start(self):
        # Start the web server worker thread.
        self.webServerThread = threading.Thread(target=self._WebServerWorker)
        self.webServerThread.start()


    def RegisterForAccountStatusUpdates(self):
        # Register for account link callbacks.
        # This is called after startup, because the command handler isn't created until after the web server.
        CommandHandler.Get().RegisterAccountLinkStatusUpdateHandler(self)


    # Called when we are connected and we know if there's an account setup with this addon
    def OnPrimaryConnectionEstablished(self, hasConnectedAccount):
        self.AccountConnected = hasConnectedAccount
        self.IsPendingStartup = False


    # Interface function
    # Called from the command handler the account link status changes.
    def OnAccountLinkStatusUpdate(self, isLinked:bool):
        self.AccountConnected = isLinked


    def _WebServerWorker(self):
        backoff = 0
        while True:
            # Try to run the webserver forever.
            webServer = None
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
                Sentry.Exception("Failed to close the addon webserver.", e)

            # Try again after some time.
            backoff = min(backoff + 1, 20)
            time.sleep(backoff * 0.5)


    class WebServerHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Check if the IP is the authenticated IP from home assistant. If not, deny it.
            # This IP is brokered by Home Assistant, and it does auth checks before forwarding the requests.
            if WebServer.Instance.AllowAllIps is False:
                if len(self.client_address) == 0:
                    WebServer.Instance.Logger.error("Webserver got a request but we can't find the ip. Denying")
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.client_address[0] != "172.30.32.2":
                    WebServer.Instance.Logger.error(f"Webserver got a request from an invalid ip [{self.client_address[0]}]. Denying")
                    self.send_response(401)
                    self.end_headers()
                    return

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
    .pinkButton {
        background-color: #d870e8;
        color: white;
        border-radius: 5px;
        font-weight: normal; /* Needed for iOS, so the button text isn't bold. */
        transition: 0.5s;
        padding: 20px;
        padding-top: 10px;
        padding-bottom: 10px;
        text-align: center;
        /* Disable select for all buttons */
        user-select: none; /* supported by Chrome and Opera */
        -webkit-user-select: none; /* Safari */
        -khtml-user-select: none; /* Konqueror HTML */
        -moz-user-select: none; /* Firefox */
        -ms-user-select: none; /* Internet Explorer/Edge */
    }
    .pinkButton:hover {
        background-color: #C342D7;
        cursor: pointer;
    }

    .blueButton {
        background-color: #78a4fa;
        color: white;
        border-radius: 5px;
        font-weight: normal; /* Needed for iOS, so the button text isn't bold. */
        transition: 0.5s;
        padding: 20px;
        padding-top: 10px;
        padding-bottom: 10px;
        text-align: center;
        /* Disable select for all buttons */
        user-select: none; /* supported by Chrome and Opera */
        -webkit-user-select: none; /* Safari */
        -khtml-user-select: none; /* Konqueror HTML */
        -moz-user-select: none; /* Firefox */
        -ms-user-select: none; /* Internet Explorer/Edge */
    }
    .blueButton:hover {
        background-color: #547DEB;
        cursor:pointer;
    }
</style>
</head>
<body style="background-color: black; color: white; font-family: Roboto,Noto,Noto Sans,sans-serif;">
<div style="display: flex; align-content: center; justify-content: center; margin-top: 30px">
    <div style="background-color:#2A2C30; border-radius: 5px; padding: 25px; min-width: 300px; max-width:450px">
        <div style="display: flex; justify-content: center; font-size: 28px; margin-bottom:30px;">Homeway</div>
        <div style="display: """+ connectingBlockDisplay +""";">
            <div style="margin-bottom:20px; text-align: center; color:#78a4fa; font-weight: bold;">
                Connecting To Homeway.io...
            </div>
        </div>
        <div style="display: """+linkAccountBlockDisplay+""";">
            <div style="margin-bottom:30px">
                <b>This addon isn't linked to a Homeway account.</b> Click the button below to finish the addon setup.
            </div>
            <div style="display: flex; justify-content: center;" id="linkAccountButton">
                <div class="pinkButton">
                    Link Your Account Now
                </div>
            </div>
        </div>
        <div style="display: """+connectedAndReadyBlockDisplay+""";">
            <div style="margin-bottom:20px; text-align: center; color:#78a4fa; font-weight: bold;">
                Securely Connected To Homeway.io
            </div>
            <div style="margin-bottom:30px">
                Your secure and private access is ready. You can access your Home Assistant from anywhere via the Homeway dashboard.
            </div>
            <div style="display: flex; justify-content: center;" id="goToSageSetup">
                <div class="pinkButton" style="width:300px">
                    Setup Sage - Your Free ChatGPT Assist
                </div>
            </div>
            <div style="display: flex; justify-content: center; margin-top:20px;" id="goToAssistantSetup">
                <div class="pinkButton" style="width:300px">
                    Setup Alexa Or Google Assistant
                </div>
            </div>
            <div style="display: flex; justify-content: center; margin-top:20px;" id="goToAppSetup">
                <div class="pinkButton" style="width:300px">
                    Setup Your Home Assistant App
                </div>
            </div>
            <div style="display: flex; justify-content: center; margin-top:20px;" id="goToLocalAccessSetup">
                <div class="pinkButton" style="width:300px">
                    Node-RED, Unraid, AdGuard &amp; More<br/>Remote Access
                </div>
            </div>
            <div style="display: flex; justify-content: center; margin-top:20px;" id="goToDashboardButton">
                <div class="blueButton" style="width:300px">
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
    def GetDevConfigStr(self, devConfig, value):
        if devConfig is None:
            return None
        if value in devConfig:
            v = devConfig[value]
            if v is not None and len(v) > 0 and v != "None":
                return v
        return None
