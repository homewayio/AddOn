import logging
import traceback

from homeway.mdns import MDns
from homeway.sentry import Sentry
from homeway.hostcommon import HostCommon
from homeway.telemetry import Telemetry
from homeway.pingpong import PingPong
from homeway.homewaycore import Homeway
from homeway.httprequest import HttpRequest
from homeway.compression import Compression
from homeway.httpsessions import HttpSessions
from homeway.Proto.AddonTypes import AddonTypes
from homeway.commandhandler import CommandHandler
from homeway.customfileserver import CustomFileServer


from .config import Config
from .secrets import Secrets
from .version import Version
from .logger import LoggerInit
from .webserver import WebServer
from .webrequestresponsehandler import WebRequestResponseHandler
from .ha.configmanager import ConfigManager
from .ha.connection import Connection
from .ha.eventhandler import EventHandler
from .ha.serverinfo import ServerInfo
from .ha.serverdiscovery import ServerDiscovery
from .sage.sagehost import SageHost


# This file is the main host for the linux service.
class LinuxHost:

    def __init__(self, addonDataRootDir:str, logsDir:str, addonType:AddonTypes, devConfig_CanBeNone) -> None:
        # When we create our class, make sure all of our core requirements are created.
        self.Secrets = None
        self.WebServer = None
        self.HaEventHandler = None
        self.Sage:SageHost = None

        # Indicates if we are running as the Home Assistant addon, or standalone docker or cli.
        self.AddonType = addonType

        try:
            # First, we need to load our config.
            # Note that the config MUST BE WRITTEN into this folder, that's where the setup installer is going to look for it.
            # If this fails, it will throw.
            self.Config = Config(addonDataRootDir)

            # Next, setup the logger.
            logLevelOverride_CanBeNone = self.GetDevConfigStr(devConfig_CanBeNone, "LogLevel")
            self.Logger = LoggerInit.GetLogger(self.Config, logsDir, logLevelOverride_CanBeNone)
            self.Config.SetLogger(self.Logger)

            # Give Sentry the logger ASAP, since it's used for exceptions.
            Sentry.SetLogger(self.Logger)

        except Exception as e:
            tb = traceback.format_exc()
            print("Failed to init Linux Host! "+str(e) + "; "+str(tb))
            # Raise the exception so we don't continue.
            raise


    def RunBlocking(self, storageDir, versionFileDir, devConfig_CanBeNone):
        # Do all of this in a try catch, so we can log any issues before exiting
        try:
            self.Logger.info("##################################")
            self.Logger.info("#### Homeway Plugin Starting #####")
            self.Logger.info("##################################")

            # Find the version of the plugin, this is required and it will throw if it fails.
            pluginVersionStr = Version.GetPluginVersion(versionFileDir)
            self.Logger.info("Plugin Version: %s", pluginVersionStr)

            # Setup the HttpSession cache early, so it can be used whenever
            HttpSessions.Init(self.Logger)

            # Setup Sentry as soon as we know the plugin version.
            addonTypeStr = "HomeAssistantAddon"
            if self.AddonType is AddonTypes.StandaloneDocker:
                addonTypeStr = "StandaloneDocker"
            elif self.AddonType is AddonTypes.StandaloneCli:
                addonTypeStr = "StandaloneCli"
            self.Logger.info("Plugin Type: %s", addonTypeStr)
            Sentry.Setup(pluginVersionStr, addonTypeStr, devConfig_CanBeNone is not None)

            # We don't store any sensitive things in teh config file, since all config files are sometimes backed up publicly.
            self.Secrets = Secrets(self.Logger, storageDir, self.Config)

            # Now, detect if this is a new instance and we need to init our global vars. If so, the setup script will be waiting on this.
            self.DoFirstTimeSetupIfNeeded()

            # Get our required vars
            pluginId = self.GetPluginId()
            privateKey = self.GetPrivateKey()

            # Set the plugin id when we know it.
            Sentry.SetPluginId(pluginId)

            # Start the web server, which allows the user to interact with the plugin.
            # We start it as early as possible so the user can load the web page ASAP.
            # We always create the class, but only start the server for the in HA addon.
            self.WebServer = WebServer(self.Logger, pluginId, devConfig_CanBeNone)
            if self.AddonType is AddonTypes.HaAddon:
                self.WebServer.Start()

            # Unpack any dev vars that might exist
            devLocalHomewayServerAddress_CanBeNone = self.GetDevConfigStr(devConfig_CanBeNone, "LocalHomewayServerAddress")
            if devLocalHomewayServerAddress_CanBeNone is not None:
                self.Logger.warning("~~~ Using Local Dev Server Address: %s ~~~", devLocalHomewayServerAddress_CanBeNone)
            # This is mostly just used to not allow the dev plugin to fallback to port 80
            if self.GetDevConfigStr(devConfig_CanBeNone, "HomeAssistantProxyPort") is not None:
                HttpRequest.SetLocalHttpProxyPort(int(self.GetDevConfigStr(devConfig_CanBeNone, "HomeAssistantProxyPort")))

            # Init Sentry, but it won't report since we are in dev mode.
            Telemetry.Init(self.Logger)
            if devLocalHomewayServerAddress_CanBeNone is not None:
                Telemetry.SetServerProtocolAndDomain("http://"+devLocalHomewayServerAddress_CanBeNone)

            # Init compression
            Compression.Init(self.Logger, storageDir)

            # Init the mdns client
            MDns.Init(self.Logger, storageDir)

            # Setup the command handler
            # This must be setup before the config manager.
            CommandHandler.Init(self.Logger)

            # Setup the custom file server
            CustomFileServer.Init(self.Logger)

            # Setup the Home Assistant config manager
            configManager = ConfigManager(self.Logger)
            self.WebServer.RegisterForAccountStatusUpdates()

            # Get the Home Assistant server details from the config.
            homeAssistantIpOrHostname = self.Config.GetStr(Config.HomeAssistantSection, Config.HaIpOrHostnameKey, "127.0.0.1")
            homeAssistantPort = self.Config.GetInt(Config.HomeAssistantSection, Config.HaPortKey, 8123)
            homeAssistantUseHttps = self.Config.GetBool(Config.HomeAssistantSection, Config.HaUseHttps, False)
            accessToken_CanBeNone = self.Config.GetStr(Config.HomeAssistantSection, Config.HaAccessTokenKey, None)

            # For port discovery, it's ideal to have the access token, to ensure we found the exact right server.
            discoveryAccessToken = accessToken_CanBeNone
            if discoveryAccessToken is None:
                # Try to get the access token from the env which will work if we are running in a container.
                discoveryAccessToken = ServerInfo.GetAccessToken()
                if discoveryAccessToken is None:
                    # This shouldn't really happen.
                    self.Logger.warning("No access token was found in the config or env.")

            # Use the discovery class to find the correct port for Home Assistant.
            # For standalone plugin installs, the installer will get the port set correctly with the user's help.
            # In that case, the discovery will use the hint port and instantly find the correct server.
            # For addon installs, the user might have a custom setup that requires some searching to find the right port.
            serverDiscovery = ServerDiscovery(self.Logger, configManager)
            result = serverDiscovery.SearchForServerPort(homeAssistantIpOrHostname, discoveryAccessToken, homeAssistantPort)
            if result is not None:
                homeAssistantPort = result.Port
                homeAssistantUseHttps = result.IsHttps
            else:
                self.Logger.warning("Server discovery failed to find a port %s, we will just use the default [%s]", homeAssistantIpOrHostname, str(homeAssistantPort))

            # Set the final ips, port, and access token.
            self.Logger.info("Setting up Home Assistant connection to [%s:%s] https:%s", homeAssistantIpOrHostname, str(homeAssistantPort), str(homeAssistantUseHttps))
            HttpRequest.SetDirectServicePort(homeAssistantPort)
            HttpRequest.SetDirectServiceAddress(homeAssistantIpOrHostname)
            HttpRequest.SetDirectServiceUseHttps(homeAssistantUseHttps)
            ServerInfo.SetServerInfo(homeAssistantIpOrHostname, homeAssistantPort, homeAssistantUseHttps, accessToken_CanBeNone)

            # Init the ping pong helper.
            PingPong.Init(self.Logger, storageDir)
            if devLocalHomewayServerAddress_CanBeNone is not None:
                PingPong.Get().DisablePrimaryOverride()

            # Setup the web response handler
            WebRequestResponseHandler.Init(self.Logger)

            # Setup the HA state change handler
            self.HaEventHandler = EventHandler(self.Logger, pluginId, devLocalHomewayServerAddress_CanBeNone)

            # Setup the HA connection object
            haConnection = Connection(self.Logger, self.HaEventHandler)
            haConnection.Start()

            # Set the ha connection object and try to update the config if needed.
            configManager.SetHaConnection(haConnection)
            configManager.UpdateConfigIfNeeded()

            # Setup the sage sub system, it won't be started until the primary connection is established.
            self.Sage = SageHost(self.Logger, pluginVersionStr, devLocalHomewayServerAddress_CanBeNone)

            # Now start the main runner!
            pluginConnectUrl = HostCommon.GetPluginConnectionUrl()
            if devLocalHomewayServerAddress_CanBeNone is not None:
                pluginConnectUrl = HostCommon.GetPluginConnectionUrl(fullHostString="ws://"+devLocalHomewayServerAddress_CanBeNone)
            oe = Homeway(pluginConnectUrl, pluginId, privateKey, self.Logger, self, pluginVersionStr, self.AddonType)
            oe.RunBlocking()
        except Exception as e:
            Sentry.Exception("!! Exception thrown out of main host run function.", e)

        # Allow the loggers to flush before we exit
        try:
            self.Logger.info("##################################")
            self.Logger.info("#### Homeway Exiting ######")
            self.Logger.info("##################################")
            logging.shutdown()
        except Exception as e:
            print("Exception in logging.shutdown "+str(e))


    # Ensures all required values are setup and valid before starting.
    def DoFirstTimeSetupIfNeeded(self):
        # Try to get the plugin id from the config.
        pluginId = self.GetPluginId()
        if HostCommon.IsPluginIdValid(pluginId) is False:
            if pluginId is None:
                self.Logger.info("No plugin id was found, generating one now!")
            else:
                self.Logger.info("An invalid pluginId id was found [%s], regenerating!", str(pluginId))

            # Make a new, valid, key
            pluginId = HostCommon.GeneratePluginId()

            # Save it
            self.Secrets.SetPluginId(pluginId)
            self.Logger.info("New plugin id created: %s", pluginId)

        privateKey = self.GetPrivateKey()
        if HostCommon.IsPrivateKeyValid(privateKey) is False:
            if privateKey is None:
                self.Logger.info("No private key was found, generating one now!")
            else:
                self.Logger.info("An invalid private key was found [%s], regenerating!", str(privateKey))

            # Make a new, valid, key
            privateKey = HostCommon.GeneratePrivateKey()

            # Save it
            self.Secrets.SetPrivateKey(privateKey)
            self.Logger.info("New private key created.")


    # Returns None if no plugin id has been set.
    def GetPluginId(self):
        return self.Secrets.GetPluginId()


    # Returns None if no private id has been set.
    def GetPrivateKey(self):
        return self.Secrets.GetPrivateKey()


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


    #
    # StatusChangeHandler Interface - Called by the Homeway logic when the server connection has been established.
    #
    def OnPrimaryConnectionEstablished(self, apiKey, connectedAccounts):
        self.Logger.info("Primary Connection To Homeway Established - We Are Ready To Go!")

        # Set the current API key to the event handler
        self.HaEventHandler.SetHomewayApiKey(apiKey)

        # Once we have the API key, we can start or refresh the Sage system.
        self.Sage.StartOrRefresh(self.GetPluginId(), apiKey)

        # Set the current API key to the custom file server
        CustomFileServer.Get().UpdateAddonConfig(self.GetPluginId(), apiKey)

        # Tell the web server if there's a connect user or not.
        hasConnectedAccount = connectedAccounts is not None and len(connectedAccounts) > 0
        self.WebServer.OnPrimaryConnectionEstablished(hasConnectedAccount)

        # Check if this plugin is unlinked, if so add a message to the log to help the user setup the plugin if desired.
        # This would be if the skipped the plugin link or missed it in the setup script.
        if hasConnectedAccount is False:
            self.Logger.info("")
            self.Logger.info("")
            self.Logger.info("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            self.Logger.info("              This Add-On Isn't Connected To Homeway!             ")
            self.Logger.info("             Use this link to finish the add-on setup:            ")
            self.Logger.info(" %s", HostCommon.GetAddPluginUrl(self.GetPluginId()))
            self.Logger.info("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            self.Logger.info("")
            self.Logger.info("")


    #
    # StatusChangeHandler Interface - Called by the Homeway logic when a plugin update is required for this client.
    #
    def OnPluginUpdateRequired(self):
        self.Logger.error("!!! A Plugin Update Is Required -- If This Plugin Isn't Updated It Might Stop Working !!!")
