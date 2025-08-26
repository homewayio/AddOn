import os
import logging
import threading
import time

from homeway.sentry import Sentry
from homeway.commandhandler import CommandHandler

from .serverinfo import ServerInfo
from .connection import Connection

# Helps manage the Home Assistant config
class ConfigManager:

    # This should be mapped into our docker container due to homeassistant_config in the addon config.
    c_ContainerConfigFilePath = "/homeassistant/configuration.yaml"

    # If the user followed the default install for Home Assistant Core, which is PY running directly on the device,
    # this is the default path that will be created for the config.
    # https://www.home-assistant.io/installation/linux#install-home-assistant-core
    c_HomeAssistantCoreInstallConfigFilePath = "/home/homeassistant/.homeassistant/configuration.yaml"

    # The time we will wait for idle until we restart.
    # Since we will ping the plugin to force the restart before we setup an assistant, this can be a while.
    c_TimeToIdleSec = 60 * 60 * 5


    def __init__(self, logger:logging.Logger) -> None:
        self.Logger = logger
        self.HaConnection = None
        self.RestartRequired = False
        CommandHandler.Get().RegisterConfigManager(self)


    # Sets the HA con object when it's ready, this should always be set after startup.
    def SetHaConnection(self, haCon:Connection) -> None:
        self.HaConnection = haCon


    # Interface function - Called by the CommandHandler.
    # If we need a restart, return true and do it
    # Otherwise, return false.
    def NeedsRestart(self) -> bool:
        if self.RestartRequired is False:
            return False
        # Kick off a restart on a new thread.
        # We want to give this command time to return back the http response and then restart.
        self.RestartHomeAssistant(2.0)
        return True


    # Interface function - Called by the CommandHandler.
    # Returns true if the config file can be edited by this addon, either in the container context or standalone.
    # Returns false if this addon can't edit the config.
    def CanEditConfig(self) -> bool:
        # This will only return a path if there's a known location and file on disk.
        configPath = self._GetConfigFilePath(True)
        if configPath is None:
            return False
        # Make sure we can open it, assume we can write it.
        try:
            with open(configPath, encoding="utf-8") as f:
                f.readline()
                return True
        except Exception:
            pass
        return False


    # Reads the http port out of the config, if there is one.
    def ReadHttpPort(self) -> int:
        try:
            # Try to get the config path, if we can find it. Don't try to use the HA API,
            # since the point of getting the http port is to do server discovery.
            configFilePath = self._GetConfigFilePath(False)
            if configFilePath is None:
                return None

            # Look for the http port
            # https://www.home-assistant.io/integrations/http/
            with open(configFilePath, encoding="utf-8") as f:
                # We tired to use the yaml library for parsing, but there's uncommon syntax in the HA config that will break it.
                foundHttpSection = False
                lines = f.readlines()
                for l in lines:
                    # Skip empty lines
                    if len(l) == 0:
                        continue
                    lineLower = l.lower()
                    # Basic idea:
                    #   Find the "http:" section
                    #   After we find the http section, if we find a line matching the server port, try to parse it out.
                    #   After we find the http section, if we see any line that starts with a char or number it's a new section, so we are done.
                    # If we found the http section, any line that starts with a letter or number is a new section, so we are done.
                    if foundHttpSection and lineLower[0].isalnum():
                        return None
                    # Search for the http section.
                    if lineLower.startswith("http:"):
                        self.Logger.debug("ConfigManager.ReadHttpPort Found the http section. "+lineLower)
                        foundHttpSection = True
                    # Search for the line with the port number.
                    if foundHttpSection and lineLower.find("server_port") != -1:
                        self.Logger.debug("ConfigManager.ReadHttpPort Found the server_port %s", l)
                        # We found the line, find the separator
                        if lineLower.find(":") == -1:
                            self.Logger.warn(f"We found the server_port line, but it's not formatted correctly. We can't parse it. {l}")
                            return None
                        # After the : should only be an int.
                        parts = lineLower.split(":")
                        return int(parts[1].strip())
        except Exception as e:
            self.Logger.error(f"Exception in ConfigManager.ReadHttpPort. {e}")
        return None


    # Adds the Homeway required config settings if needed.
    def UpdateConfigIfNeeded(self) -> None:
        try:
            # Ensure we can find the config file.
            # This will use the HA API to get the file path and see if it can be found locally.
            configFilePath = self._GetConfigFilePath(True)
            if configFilePath is None:
                self.Logger.warn("UpdateConfigIfNeeded failed to get a config file path.")
                return

            # Open the file and read.
            foundGoogleAssistantConfig = False
            foundAlexaConfig = False
            with open(configFilePath, 'r', encoding="utf-8") as f:
                # Look for the starting lines of the configs, since they must be exact.
                # But remember they will have line endings, so we use startwith.
                lines = f.readlines()
                for l in lines:
                    lineLower = l.lower()
                    if lineLower.startswith("google_assistant:"):
                        foundGoogleAssistantConfig = True
                    if lineLower.startswith("alexa:"):
                        foundAlexaConfig = True

            if foundGoogleAssistantConfig and foundAlexaConfig:
                self.Logger.info("Google Assistant and Alexa configs found, no need to add them.")
                return

            # Add which ever is needed.
            # It's important to get the indents correct, or we will break the config.
            linesToAppend = []
            lineEnding = "\r\n"

            # Add a new line to start
            linesToAppend.append(lineEnding)

            if foundAlexaConfig is False:
                linesToAppend.append("# Added By Homeway to enable Alexa support."+lineEnding)
                linesToAppend.append("alexa:"+lineEnding)
                linesToAppend.append("  smart_home:"+lineEnding)

            if foundGoogleAssistantConfig is False:
                # If we added the alexa config, add a new line to separate them.
                if foundAlexaConfig is False:
                    linesToAppend.append(lineEnding)
                linesToAppend.append("# Added By Homeway to enable Google Assistant support."+lineEnding)
                linesToAppend.append("google_assistant:"+lineEnding)
                linesToAppend.append("  project_id: homewayio"+lineEnding)
                linesToAppend.append("  service_account:"+lineEnding)
                linesToAppend.append("    private_key: \"nokey\""+lineEnding)
                linesToAppend.append("    client_email: \"support@homeway.io\""+lineEnding)

            # Add a new line to the end
            linesToAppend.append(lineEnding)

            # Add the config lines.
            with open(configFilePath, 'a', encoding="utf-8") as f:
                f.writelines(linesToAppend)

            self.Logger.info(f"Config file updated with assistant configs. Alexa: {str(foundAlexaConfig is False)}, Google Assistant: {str(foundGoogleAssistantConfig is False)}")

            # Start a refresh thread.
            self.RestartRequired = True
            self.RestartHomeAssistant(ConfigManager.c_TimeToIdleSec)
        except Exception as e:
            Sentry.Exception("HomeAssistantConfigManager exception.", e)


    def RestartHomeAssistant(self, restartInSec:float):
        t = threading.Thread(target=self._RestartHomeAssistant_Thread, args=(restartInSec,))
        t.daemon = True
        t.start()


    def _RestartHomeAssistant_Thread(self, restartInSec:float):
        try:
            self.Logger.info(f"Waiting to restart HA for {restartInSec}...")
            time.sleep(restartInSec)

            # Ensure we still need the restart, there might have been another thread started that did it while we were waiting.
            if self.RestartRequired is False:
                self.Logger.info("No need to restart any longer. Not taking action.")
                return
            self.RestartRequired = False

            # Ensure we have a con object.
            if self.HaConnection is None:
                self.Logger.error("We wanted to restart Home Assistant but we don't have a ha connection object.")

            self.Logger.info("Trying to restart Home Assistant to apply the config change.")
            self.HaConnection.RestartHa()
        except Exception as e:
            Sentry.Exception("TryToRestartHomeAssistant exception.", e)


    # Returns the config file path for the Home Assistant config.
    # This will try a few paths on disk and also try the HA API to get it, if possible.
    # If the config path can't be found, None is returned.
    # If a string is returned, it will always be a valid file path.
    def _GetConfigFilePath(self, useApiIfUnknown:bool = False) -> str:
        # First, try the path where the config will be if we are running on a container.
        if os.path.exists(ConfigManager.c_ContainerConfigFilePath) and os.path.isfile(ConfigManager.c_ContainerConfigFilePath):
            self.Logger.debug("HA config path found in expected container location.")
            return ConfigManager.c_ContainerConfigFilePath

        # Next, try to use the API if we were asked to.
        # We do this before the local path, because we know the server we should be connected to,
        # and if it returns a valid config path it's the correct one. Otherwise, we just look for one on disk.
        if useApiIfUnknown:
            for _ in range(1):
                # Home Assistant has an API we can use to try to get the config file path.
                try:
                    # Ensure we have an API key.
                    # Note this will also fail if the plugin lost auth to HA.
                    configApiJson = ServerInfo.GetConfigApi(self.Logger)
                    if configApiJson is None:
                        self.Logger.warn("We tried to get the HA config file path from the HA API, but the config api failed.")
                        break

                    # Try to get the config file path from the API.
                    configDir = configApiJson.get("config_dir", None)
                    if configDir is None:
                        self.Logger.warn("Failed to get the config_dir from the HA config API.")
                        break

                    # See if the path exists.
                    configFilePath = os.path.join(configDir, "configuration.yaml")
                    if os.path.exists(configFilePath) and os.path.isfile(configFilePath):
                        self.Logger.debug(f"HA config path found in from API and is on the local disk {configFilePath}.")
                        return configFilePath
                    self.Logger.warn(f"We got a config file path from the HA config API [{configFilePath}] but it doesn't exist on this device.")

                except Exception as e:
                    Sentry.Exception("ConfigManager._GetConfigFilePath failed.", e)

        # Finally, see if the default config path exists on disk default Home Assistant Core installs.
        # We do this last, because it could be wrong, there could be a standalone addon running on a device with HA, but connected to a different device.
        if os.path.exists(ConfigManager.c_HomeAssistantCoreInstallConfigFilePath) and os.path.isfile(ConfigManager.c_HomeAssistantCoreInstallConfigFilePath):
            self.Logger.debug("HA config path found expected core install file path.")
            return ConfigManager.c_HomeAssistantCoreInstallConfigFilePath

        self.Logger.info("Failed to find a config path on disk or from the API.")
        return None
