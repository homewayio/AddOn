import os
import logging
import threading
import time
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Any, Dict, List, Optional, Tuple, cast

from homeway.sentry import Sentry
from homeway.interfaces import IConfigManager
from homeway.commandhandler import CommandHandler

from .serverinfo import ServerInfo
from .connection import Connection


# Helps manage the Home Assistant config
class ConfigManager(IConfigManager):

    # This should be mapped into our docker container due to homeassistant_config in the addon config.
    c_ContainerConfigFilePath = "/homeassistant/configuration.yaml"

    # If the user followed the default install for Home Assistant Core, which is PY running directly on the device,
    # this is the default path that will be created for the config.
    # https://www.home-assistant.io/installation/linux#install-home-assistant-core
    c_HomeAssistantCoreInstallConfigFilePath = (
        "/home/homeassistant/.homeassistant/configuration.yaml"
    )

    # The time we will wait for idle until we restart.
    # Since we will ping the plugin to force the restart before we setup an assistant, this can be a while.
    c_TimeToIdleSec = 60 * 60 * 5

    # Home Assistant 2026.8 moved HTTP configuration from YAML to a WebSocket API.
    c_MinHomeAssistantHttpConfigApiVersion: Tuple[int, int] = (2026, 8)
    c_HttpConfigUpdateRetryCount = 12
    c_HttpConfigUpdateRetryDelaySec = 5.0
    c_RequiredTrustedProxies = (
        "172.30.32.0/23",
        "127.0.0.1",
        "::1",
    )
    c_HttpConfigMetaKeys = (
        "created_at",
        "error",
        "error_message",
    )


    def __init__(self, logger: logging.Logger) -> None:
        self.Logger = logger
        self.HaConnection: Optional[Connection] = None
        self.RestartRequired: bool = False
        self.HttpConfigUpdateStateLock = threading.Lock()
        self.HttpConfigUpdateThreadRunning = False
        self.HttpConfigUpdateRequested = False
        CommandHandler.Get().RegisterConfigManager(self)


    # Sets the HA con object when it's ready, this should always be set after startup.
    def SetHaConnection(self, haCon: Connection) -> None:
        self.HaConnection = haCon
        haCon.RegisterOnConnectedCallback(self._OnHaConnected)


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


    # Interface function - Returns the path to the Home Assistant config file if it can be found.
    # Returns None if the config file cannot be found.
    def GetConfigFilePath(self) -> Optional[str]:
        return self._GetConfigFilePath(True)


    # Reads the http port out of the config, if there is one.
    def ReadHttpPort(self) -> Optional[int]:
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
                for line in lines:
                    # Skip empty lines
                    if len(line) == 0:
                        continue
                    lineLower = line.lower()
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
                        self.Logger.debug("ConfigManager.ReadHttpPort Found the server_port %s", line)
                        # We found the line, find the separator
                        if lineLower.find(":") == -1:
                            self.Logger.warning(f"We found the server_port line, but it's not formatted correctly. We can't parse it. {line}")
                            return None
                        # After the : should only be an int.
                        # Use split(":", 1) to only split on the first colon in case there's an inline comment with a colon.
                        parts = lineLower.split(":", 1)
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
                self.Logger.warning("UpdateConfigIfNeeded failed to get a config file path.")
                return

            # HTTP config is managed separately through Home Assistant's WebSocket API once connected.
            assistantConfigUpdated = self._UpdateAssistantConfigIfNeeded(configFilePath)
            if not assistantConfigUpdated:
                self.Logger.info("No config updates were needed.")
                return

            # Start a refresh thread.
            self.RestartRequired = True
            self.RestartHomeAssistant(ConfigManager.c_TimeToIdleSec)
        except Exception as e:
            Sentry.OnException("HomeAssistantConfigManager exception.", e)


    def _UpdateAssistantConfigIfNeeded(self, configFilePath: str) -> bool:
        # Open the file and read.
        foundGoogleAssistantConfig: bool = False
        foundAlexaConfig: bool = False
        with open(configFilePath, "r", encoding="utf-8") as f:
            # Look for the starting lines of the configs, since they must be exact.
            # But remember they will have line endings, so we use startwith.
            lines = f.readlines()
            for line in lines:
                lineLower = line.lower()
                if lineLower.startswith("google_assistant:"):
                    foundGoogleAssistantConfig = True
                if lineLower.startswith("alexa:"):
                    foundAlexaConfig = True

        if foundGoogleAssistantConfig and foundAlexaConfig:
            self.Logger.info("Google Assistant and Alexa configs found, no need to add them.")
            return False

        # Add which ever is needed.
        # It's important to get the indents correct, or we will break the config.
        linesToAppend: List[str] = []
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
        with open(configFilePath, "a", encoding="utf-8") as f:
            f.writelines(linesToAppend)

        self.Logger.info(f"Config file updated with assistant configs. Alexa: {str(foundAlexaConfig is False)}, Google Assistant: {str(foundGoogleAssistantConfig is False)}")
        return True


    # Called on the HA websocket thread. Start a worker because SendAndReceiveMsg can't block that thread.
    def _OnHaConnected(self) -> None:
        with self.HttpConfigUpdateStateLock:
            self.HttpConfigUpdateRequested = True
            if self.HttpConfigUpdateThreadRunning:
                return
            self.HttpConfigUpdateThreadRunning = True
        t = threading.Thread(target=self._UpdateHttpConfig_Thread)
        t.daemon = True
        t.start()


    def _UpdateHttpConfig_Thread(self) -> None:
        try:
            retryCount = 0
            while True:
                with self.HttpConfigUpdateStateLock:
                    self.HttpConfigUpdateRequested = False

                shouldRetry = self._UpdateHttpConfigViaApiIfNeeded()
                if (shouldRetry and retryCount < ConfigManager.c_HttpConfigUpdateRetryCount):
                    retryCount += 1
                    time.sleep(ConfigManager.c_HttpConfigUpdateRetryDelaySec)
                    continue

                with self.HttpConfigUpdateStateLock:
                    if self.HttpConfigUpdateRequested:
                        retryCount = 0
                        continue
                    return
        except Exception as e:
            Sentry.OnException("Home Assistant HTTP config update exception.", e)
        finally:
            with self.HttpConfigUpdateStateLock:
                updateRequested = self.HttpConfigUpdateRequested
                self.HttpConfigUpdateThreadRunning = False
            if updateRequested:
                self._OnHaConnected()


    # Returns true when the operation should be retried because HA isn't ready yet.
    def _UpdateHttpConfigViaApiIfNeeded(self) -> bool:
        # Ensure we have a connection.
        haConnection = self.HaConnection
        if haConnection is None:
            self.Logger.warning("HTTP config update skipped because there is no Home Assistant connection.")
            return True

        # Ensure the version is new enough to use this API.
        haVersion = haConnection.GetHomeAssistantVersionString()
        if not self._HomeAssistantVersionSupportsHttpConfigApi(haVersion):
            self.Logger.info(f"Home Assistant {haVersion} does not support the HTTP config API; leaving its HTTP config unchanged.")
            return False

        # Get the current config.
        response = haConnection.SendAndReceiveMsg({"type": "http/config"})
        shouldRetry, result = self._GetHttpConfigApiResult("query", response)
        if result is None:
            return shouldRetry

        activeConfigType = result.get("active_config_type", "stable")
        activePendingConfig = activeConfigType == "pending"
        sourceConfig = result.get("pending" if activePendingConfig else "stable")
        if activeConfigType == "default" or activeConfigType == "default_legacy_port":
            sourceConfig = result.get("default")
        if not isinstance(sourceConfig, dict):
            self.Logger.warning("Home Assistant HTTP config API returned no usable active config.")
            return False

        config: Dict[str, Any] = dict(sourceConfig)
        for key in ConfigManager.c_HttpConfigMetaKeys:
            config.pop(key, None)
        if activeConfigType == "default_legacy_port":
            # HA fell back because the new default port couldn't be bound. Preserve the actual
            # discovered port instead of retrying the same default that already failed.
            config["server_port"] = ServerInfo.ServerPort

        hasUpdates = config.get("use_x_forwarded_for") is not True
        config["use_x_forwarded_for"] = True

        trustedProxiesValue = config.get("trusted_proxies", [])
        trustedProxies: List[Any]
        if not isinstance(trustedProxiesValue, list):
            trustedProxies = [trustedProxiesValue]
        else:
            trustedProxies = list(trustedProxiesValue)
        for requiredProxy in ConfigManager.c_RequiredTrustedProxies:
            if not self._TrustedProxyListCovers(trustedProxies, requiredProxy):
                trustedProxies.append(requiredProxy)
                hasUpdates = True
        config["trusted_proxies"] = trustedProxies

        if not hasUpdates:
            if activePendingConfig:
                return self._PromotePendingHttpConfig(haConnection)
            self.Logger.info("Home Assistant HTTP trusted proxy config is already correct.")
            return False

        self.Logger.info("Updating Home Assistant HTTP trusted proxy config through the WebSocket API.")
        response = haConnection.SendAndReceiveMsg(
            {"type": "http/config/configure", "config": config}
        )
        shouldRetry, configureResult = self._GetHttpConfigApiResult("update", response)
        if configureResult is None:
            return shouldRetry

        if configureResult.get("restart", False):
            # The API restart also applies any assistant YAML changes waiting for a restart.
            self.RestartRequired = False
            self.Logger.info("Home Assistant is restarting to apply the HTTP trusted proxy config.")
        else:
            self.Logger.info("Home Assistant accepted the HTTP trusted proxy config without requiring a restart.")
        return False


    def _PromotePendingHttpConfig(self, haConnection: Connection) -> bool:
        self.Logger.info("Confirming the pending Home Assistant HTTP trusted proxy config.")
        response = haConnection.SendAndReceiveMsg({"type": "http/config/promote"})
        shouldRetry, result = self._GetHttpConfigApiResult("confirm", response, allowEmptyResult=True)
        if result is not None:
            self.Logger.info("Home Assistant HTTP trusted proxy config confirmed.")
        return shouldRetry


    def _GetHttpConfigApiResult(
        self,
        operation: str,
        response: Optional[Dict[str, Any]],
        allowEmptyResult: bool = False,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if response is None:
            self.Logger.warning(f"Home Assistant HTTP config {operation} did not return a response.")
            return True, None
        if response.get("success", False) is not True:
            error = response.get("error", {})
            errorCode = (
                cast(Dict[str, Any], error).get("code", "unknown")
                if isinstance(error, dict)
                else "unknown"
            )
            if errorCode == "not_running":
                self.Logger.info(
                    "Home Assistant is still starting; the HTTP config update will be retried."
                )
                return True, None
            self.Logger.warning(
                f"Home Assistant HTTP config {operation} failed: {error}"
            )
            return False, None
        result = response.get("result")
        if allowEmptyResult and result is None:
            return False, {}
        if not isinstance(result, dict):
            self.Logger.warning(
                f"Home Assistant HTTP config {operation} returned an invalid result."
            )
            return False, None
        return False, result


    @staticmethod
    def _HomeAssistantVersionSupportsHttpConfigApi(version: Optional[str]) -> bool:
        if version is None:
            return True
        try:
            versionParts = version.split(".")
            versionTuple = (int(versionParts[0]), int(versionParts[1]))
            return versionTuple >= ConfigManager.c_MinHomeAssistantHttpConfigApiVersion
        except (IndexError, ValueError):
            # Development versions can use a non-standard version string; query the API and let HA decide.
            return True


    @staticmethod
    def _TrustedProxyListCovers(trustedProxies: List[Any], requiredProxy: str) -> bool:
        requiredNetwork = ip_network(requiredProxy)
        for trustedProxy in trustedProxies:
            try:
                trustedNetwork = ip_network(trustedProxy)
                if (
                    isinstance(requiredNetwork, IPv4Network)
                    and isinstance(trustedNetwork, IPv4Network)
                    and requiredNetwork.subnet_of(trustedNetwork)
                ):
                    return True
                if (
                    isinstance(requiredNetwork, IPv6Network)
                    and isinstance(trustedNetwork, IPv6Network)
                    and requiredNetwork.subnet_of(trustedNetwork)
                ):
                    return True
            except ValueError:
                continue
        return False


    def RestartHomeAssistant(self, restartInSec:float) -> None:
        t = threading.Thread(target=self._RestartHomeAssistant_Thread, args=(restartInSec,))
        t.daemon = True
        t.start()


    def _RestartHomeAssistant_Thread(self, restartInSec: float) -> None:
        try:
            self.Logger.info(f"Waiting to restart HA for {restartInSec}...")
            time.sleep(restartInSec)

            # Ensure we still need the restart, there might have been another thread started that did it while we were waiting.
            if self.RestartRequired is False:
                self.Logger.info("No need to restart any longer. Not taking action.")
                return
            self.RestartRequired = False

            # Ensure we have a connection object.
            if self.HaConnection is None:
                self.Logger.error(
                    "We wanted to restart Home Assistant but we don't have a ha connection object."
                )
                return

            self.Logger.info(
                "Trying to restart Home Assistant to apply the config change."
            )
            self.HaConnection.RestartHa()
        except Exception as e:
            Sentry.OnException("TryToRestartHomeAssistant exception.", e)


    # Returns the config file path for the Home Assistant config.
    # This will try a few paths on disk and also try the HA API to get it, if possible.
    # If the config path can't be found, None is returned.
    # If a string is returned, it will always be a valid file path.
    def _GetConfigFilePath(self, useApiIfUnknown:bool=False) -> Optional[str]:
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
                        self.Logger.warning("We tried to get the HA config file path from the HA API, but the config api failed.")
                        break

                    # Try to get the config file path from the API.
                    configDir = configApiJson.get("config_dir", None)
                    if configDir is None:
                        self.Logger.warning("Failed to get the config_dir from the HA config API.")
                        break

                    # See if the path exists.
                    configFilePath = os.path.join(configDir, "configuration.yaml")
                    if os.path.exists(configFilePath) and os.path.isfile(configFilePath):
                        self.Logger.debug(f"HA config path found in from API and is on the local disk {configFilePath}.")
                        return configFilePath
                    self.Logger.warning(f"We got a config file path from the HA config API [{configFilePath}] but it doesn't exist on this device.")

                except Exception as e:
                    Sentry.OnException("ConfigManager._GetConfigFilePath failed.", e)

        # Finally, see if the default config path exists on disk default Home Assistant Core installs.
        # We do this last, because it could be wrong, there could be a standalone addon running on a device with HA, but connected to a different device.
        if os.path.exists(ConfigManager.c_HomeAssistantCoreInstallConfigFilePath) and os.path.isfile(ConfigManager.c_HomeAssistantCoreInstallConfigFilePath):
            self.Logger.debug("HA config path found expected core install file path.")
            return ConfigManager.c_HomeAssistantCoreInstallConfigFilePath

        self.Logger.info("Failed to find a config path on disk or from the API.")
        return None
