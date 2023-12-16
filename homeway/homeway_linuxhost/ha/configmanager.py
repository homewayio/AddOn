import os
import logging
import threading
import time

from homeway.sentry import Sentry
from homeway.commandhandler import CommandHandler

from .connection import Connection

# Helps manage the Home Assistant config
class ConfigManager:

    # This should be mapped into our docker container due to homeassistant_config in the addon config.
    c_ConfigFilePath = "/homeassistant/configuration.yaml"

    # The time we will wait for idle until we restart.
    # Since we will ping the plugin to force the restart before we setup an assistant, this can be a while.
    c_TimeToIdleSec = 60 * 60 * 5


    def __init__(self, logger:logging.Logger, haCon:Connection) -> None:
        self.Logger = logger
        self.HaConnection = haCon
        self.RestartRequired = False
        CommandHandler.Get().RegisterConfigManager(self)


    # Interface function - Called by the CommandHandler.
    # If we need a restart, return true and do it
    # Otherwise, return false.
    def NeedsRestart(self):
        if self.RestartRequired is False:
            return False
        # Kick off a restart on a new thread.
        # We want to give this command time to return back the http response and then restart.
        self.RestartHomeAssistant(2.0)
        return True


    def UpdateConfigIfNeeded(self) -> None:
        try:
            # Ensure we can find the config file.
            if os.path.exists(ConfigManager.c_ConfigFilePath) is False:
                self.Logger.warn(f"Failed to find Home Assistant config file at {ConfigManager.c_ConfigFilePath}")
                for _, dirnames, _ in os.walk("/"):
                    for d in dirnames:
                        self.Logger.info(f"Dir In Root: {d}")
                return

            # Open the file and read.
            foundGoogleAssistantConfig = False
            foundAlexaConfig = False
            with open(ConfigManager.c_ConfigFilePath, 'r', encoding="utf-8") as f:
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
            with open(ConfigManager.c_ConfigFilePath, 'a', encoding="utf-8") as f:
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

            self.Logger.info("Trying to restart Home Assistant to apply the config change.")
            self.HaConnection.RestartHa()
        except Exception as e:
            Sentry.Exception("TryToRestartHomeAssistant exception.", e)
