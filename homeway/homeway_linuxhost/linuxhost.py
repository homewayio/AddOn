import logging
import traceback

from homeway.mdns import MDns
from homeway.sentry import Sentry
from homeway.hostcommon import HostCommon
from homeway.telemetry import Telemetry
from homeway.pingpong import PingPong
from homeway.commandhandler import CommandHandler
from homeway.homewaycore import Homeway
from homeway.httprequest import HttpRequest

from .config import Config
from .secrets import Secrets
from .version import Version
from .logger import LoggerInit
from .webserver import WebServer
from .webrequestresponsehandler import WebRequestResponseHandler
from .homeassistantconfigmanager import HomeAssistantConfigManager


# This file is the main host for the moonraker service.
class LinuxHost:

    def __init__(self, storageDir:str, isRunningInAddonContext:bool, devConfig_CanBeNone) -> None:
        # When we create our class, make sure all of our core requirements are created.
        self.Secrets = None
        self.WebServer = None

        # Indicates if we are running in the docker addon container.
        self.IsRunningInAddonContext = isRunningInAddonContext

        try:
            # First, we need to load our config.
            # Note that the config MUST BE WRITTEN into this folder, that's where the setup installer is going to look for it.
            # If this fails, it will throw.
            self.Config = Config(storageDir)

            # Next, setup the logger.
            logLevelOverride_CanBeNone = self.GetDevConfigStr(devConfig_CanBeNone, "LogLevel")
            self.Logger = LoggerInit.GetLogger(self.Config, storageDir, logLevelOverride_CanBeNone)
            self.Config.SetLogger(self.Logger)

            # Init sentry, since it's needed for Exceptions.
            Sentry.Init(self.Logger, "homeway", True)

        except Exception as e:
            tb = traceback.format_exc()
            print("Failed to init Linux Host! "+str(e) + "; "+str(tb))
            # Raise the exception so we don't continue.
            raise


    def RunBlocking(self, storageDir, repoRoot, devConfig_CanBeNone):
        # Do all of this in a try catch, so we can log any issues before exiting
        try:
            self.Logger.info("##################################")
            self.Logger.info("#### Homeway Plugin Starting #####")
            self.Logger.info("##################################")

            # Find the version of the plugin, this is required and it will throw if it fails.
            pluginVersionStr = Version.GetPluginVersion(repoRoot)
            self.Logger.info("Plugin Version: %s", pluginVersionStr)

            # We don't store any sensitive things in teh config file, since all config files are sometimes backed up publicly.
            self.Secrets = Secrets(self.Logger, storageDir, self.Config)

            # Now, detect if this is a new instance and we need to init our global vars. If so, the setup script will be waiting on this.
            self.DoFirstTimeSetupIfNeeded()

            # Get our required vars
            pluginId = self.GetPluginId()
            privateKey = self.GetPrivateKey()

            # Start the web server, which allows the user to interact with the plugin.
            # We start it as early as possible so the user can load the web page ASAP.
            self.WebServer = WebServer(self.Logger, pluginId, devConfig_CanBeNone)
            self.WebServer.Start()

            # Unpack any dev vars that might exist
            devLocalHomewayServerAddress_CanBeNone = self.GetDevConfigStr(devConfig_CanBeNone, "LocalHomewayServerAddress")
            if devLocalHomewayServerAddress_CanBeNone is not None:
                self.Logger.warning("~~~ Using Local Dev Server Address: %s ~~~", devLocalHomewayServerAddress_CanBeNone)
            devHomeAssistantServerAddress_CanBeNone = self.GetDevConfigStr(devConfig_CanBeNone, "HomeAssistantAddress")
            devHomeAssistantServerPortStr_CanBeNone = self.GetDevConfigStr(devConfig_CanBeNone, "HomeAssistantPort")
            # This is mostly just used to not allow the dev plugin to fallback to port 80
            if self.GetDevConfigStr(devConfig_CanBeNone, "HomeAssistantProxyPort") is not None:
                HttpRequest.SetLocalHttpProxyPort(int(self.GetDevConfigStr(devConfig_CanBeNone, "HomeAssistantProxyPort")))

            # Init Sentry, but it won't report since we are in dev mode.
            Telemetry.Init(self.Logger)
            if devLocalHomewayServerAddress_CanBeNone is not None:
                Telemetry.SetServerProtocolAndDomain("http://"+devLocalHomewayServerAddress_CanBeNone)

            # Init the mdns client
            MDns.Init(self.Logger, storageDir)

            # Setup the default http port. We default to 8123, assuming HA is running there. We fallback to 80 if it's not.
            servicePort = 8123
            serviceAddress = "127.0.0.1"
            if devHomeAssistantServerPortStr_CanBeNone is not None:
                servicePort = int(devHomeAssistantServerPortStr_CanBeNone)
            if devHomeAssistantServerAddress_CanBeNone is not None:
                serviceAddress = devHomeAssistantServerAddress_CanBeNone
            self.Logger.info("Setting up relay with address %s:%s", serviceAddress, str(servicePort))
            HttpRequest.SetDirectServicePort(servicePort)
            HttpRequest.SetDirectServiceAddress(serviceAddress)

            # Init the ping pong helper.
            PingPong.Init(self.Logger, storageDir)
            if devLocalHomewayServerAddress_CanBeNone is not None:
                PingPong.Get().DisablePrimaryOverride()

            # Setup the command handler
            CommandHandler.Init(self.Logger)

            # Setup the moonraker config handler
            WebRequestResponseHandler.Init(self.Logger)

            # Setup the Home Assistant config manager
            configManager = HomeAssistantConfigManager(self.Logger)
            if self.IsRunningInAddonContext:
                # We only try to update the config if we are running in the docker addon mode.
                configManager.UpdateConfigIfNeeded()

            # Now start the main runner!
            pluginConnectUrl = HostCommon.GetPluginConnectionUrl()
            if devLocalHomewayServerAddress_CanBeNone is not None:
                pluginConnectUrl = HostCommon.GetPluginConnectionUrl(fullHostString="ws://"+devLocalHomewayServerAddress_CanBeNone)
            oe = Homeway(pluginConnectUrl, pluginId, privateKey, self.Logger, self, pluginVersionStr)
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
        # TODO
        #self.Logger.error("!!! Please use the update manager          !!!")
