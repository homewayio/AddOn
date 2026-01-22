import os
import time
import json
import logging
import threading
from typing import Dict, List, Any, Optional

from homeway.httpsessions import HttpSessions
from homeway.interfaces import IConfigManager

from ..config import Config


# Helps manage WebRTC connections and related configurations.
class WebRtcManager():

    c_ConfigUsernameKey = "Username"
    c_ConfigPasswordKey = "Password"
    c_ConfigStunServersKey = "StunServers"
    c_ConfigTurnServersKey = "TurnServers"
    c_ConfigCacheTimeKey = "CacheTime"

    c_MaxCacheAgeSec = 604800  # 7 days


    def __init__(self, logger:logging.Logger, pluginId:str, pluginDataFolderPath:str, config:Config, haConfigManager:IConfigManager) -> None:
        self.Logger = logger
        self.PluginId = pluginId
        self.Config = config
        self.HaConfigManager = haConfigManager

        self.CacheLock = threading.Lock()
        self.CacheFilePath = os.path.join(pluginDataFolderPath, "webrtc_cache.json")


    def OnPrimaryConnectionEstablished(self, apiKey:str) -> None:
        # Start a background thread to update the cache if needed.
        threading.Thread(target=self._UpdateCacheIfNeeded, args=(apiKey,), daemon=True).start()


    def _UpdateCacheIfNeeded(self, apiKey:str) -> None:

        with self.CacheLock:
            try:
                # Load existing cache if it exists.
                existingCache:Dict[str, Any] = {}
                if os.path.exists(self.CacheFilePath):
                    with open(self.CacheFilePath, "r",encoding="utf-8") as f:
                        existingCache = json.load(f)

                # Validate the cache has all of tha values we need, otherwise we need to pull the API again.
                username = existingCache.get(self.c_ConfigUsernameKey, None)
                password = existingCache.get(self.c_ConfigPasswordKey, None)
                stunServers = existingCache.get(self.c_ConfigStunServersKey, None)
                turnServers = existingCache.get(self.c_ConfigTurnServersKey, None)
                cacheTime = existingCache.get(self.c_ConfigCacheTimeKey, 0)
                if (time.time() - cacheTime) < self.c_MaxCacheAgeSec and username is not None and password is not None and isinstance(stunServers, list) and isinstance(turnServers, list):
                    self.Logger.debug("WebRTC cache file is valid; no update needed.")
                    self._ProcessConfig(username, password, stunServers, turnServers)
                    return

                # We need to get new info from the server.
                self.Logger.info("Fetching WebRTC configuration from server.")
                request = {
                    "PluginId": self.PluginId,
                    "ApiKey": apiKey
                }
                url = "https://homeway.io/api/webrtc/config"
                result = HttpSessions.GetSession(url).post(url, json=request, timeout=15.0)
                if result.status_code != 200:
                    raise ValueError(f"Failed to get WebRTC config from server; status code: {result.status_code}")
                resultJson = result.json()
                resultObj = resultJson.get("Result", None)
                if resultObj is None:
                    raise ValueError("Invalid response from WebRTC config server; missing Result.")

                # Save the new cache file.
                with open(self.CacheFilePath, "w", encoding="utf-8") as f:
                    resultObj[self.c_ConfigCacheTimeKey] = time.time()
                    f.write(json.dumps(resultObj, indent=4))

                # Get the new values.
                username = resultObj.get("Username", "")
                password = resultObj.get("Password", "")
                stunServers = resultObj.get("StunServers", [])
                turnServers = resultObj.get("TurnServers", [])
                self._ProcessConfig(username, password, stunServers, turnServers)

            except Exception as e:
                self.Logger.error(f"Error updating WebRTC cache file: {e}")


    def _ProcessConfig(self, username:str, password:str, stunServers:List[str], turnServers:List[str]) -> None:

        # Write the values into the config so the user can find them easily.
        self.Config.SetStr(Config.WebRtcSection, Config.WebRtcUsernameKey, username)
        self.Config.SetStr(Config.WebRtcSection, Config.WebRtcPasswordKey, password)
        self.Config.SetStr(Config.WebRtcSection, Config.WebRtcStunServersKey, json.dumps(stunServers))
        self.Config.SetStr(Config.WebRtcSection, Config.WebRtcTurnServersKey, json.dumps(turnServers))

        if not self.HaConfigManager.CanEditConfig():
            self.Logger.info("WebRTC enables secure and low-latency remote camera and video streaming.\nYour WebRTC username and password are:\nUsername: %s\nPassword: %s\nTo configure WebRTC, follow this guide: https://homeway.io/s/webrtc-config\n", username, password)
            return

        # Try to update the Home Assistant config file with web_rtc settings
        # Uses the HA web_rtc integration format: https://www.home-assistant.io/integrations/web_rtc/
        self._UpdateWebRtcConfig(username, password, stunServers, turnServers)


    # Comment marker to identify Homeway-managed web_rtc config sections
    # Flag keyword that users can set to false in the comment to stop Homeway from auto-updating
    # This must remain as a one line comment!
    c_ConfigLineEnding       = "\r\n"
    c_HomewayAutoUpdateFlag  = "homeway_auto_update"
    c_HomewayCommentMarker   = "# Added by Homeway"
    c_HomewayCommentFullLine = f"{c_HomewayCommentMarker} to enable webrtc video streaming. Prevent Homeway from updating web_rtc by setting following to false: {c_HomewayAutoUpdateFlag}:true{c_ConfigLineEnding}" # this ending must remain "c_HomewayAutoUpdateFlag:true"

    def _UpdateWebRtcConfig(self, username:str, password:str, stunServers:List[str], turnServers:List[str]) -> None:
        try:
            # Behavior
            #  - If there is no web_rtc section in the config, it will be added with a comment that it's from Homeway.
            #  - If there is a web_rtc section.
            #       - If the section was generated by Homeway, and the auto update flag is true, update it with the new values.
            #       - If the section was created by the user, add the homeway comment to allow the user to set auto update to true, but don't mess with that's currently there.

            # Get the config file path.
            configFilePath = self.HaConfigManager.GetConfigFilePath()
            if configFilePath is None:
                self.Logger.warning("WebRTC: Cannot update web_rtc config - config file path not found.")
                return

            # Read the entire file.
            lines:List[str] = []
            with open(configFilePath, 'r', encoding="utf-8") as f:
                lines = f.readlines()

            # First, check if there is an existing webrtc section and homeway comment.
            webRtcSectionLineNumber:Optional[int] = None
            homewayCommentLineNumber:Optional[int] = None
            autoUpdateEnabled:bool = True
            lineNumber = 0
            while lineNumber < len(lines):
                line = lines[lineNumber]
                lineLower = line.lower()

                # Look for web_rtc section (the HA integration key)
                if lineLower.startswith("web_rtc:"):
                    self.Logger.debug("WebRTC: Found existing web_rtc config section at line %d.", lineNumber + 1)
                    webRtcSectionLineNumber = lineNumber
                    # Check the lines immediately before web_rtc for Homeway comment.
                    # The Homeway comment will always be one line.
                    checkLine = lineNumber - 1
                    while checkLine >= 0:
                        checkLineLower = lines[checkLine].lower()
                        # Only check comment lines.
                        if not checkLineLower.strip().startswith("#"):
                            break
                        if self.c_HomewayCommentMarker.lower() in checkLineLower:
                            homewayCommentLineNumber = checkLine
                            self.Logger.debug("WebRTC: Found Homeway comment marker at line %d.", checkLine + 1)
                            # Check the value of the auto_update flag.
                            flagPosition = checkLineLower.find(self.c_HomewayAutoUpdateFlag.lower())
                            if flagPosition != -1:
                                flagPosition += len(self.c_HomewayAutoUpdateFlag)
                                # Get the rest of the line after the flag, check if the value false is in there.
                                flagValuePartLower = checkLineLower[flagPosition:]
                                self.Logger.debug(f"WebRTC: Found auto update flag in Homeway comment. `{flagValuePartLower}`")
                                if "false" in flagValuePartLower:
                                    autoUpdateEnabled = False
                        break
                    break  # Stop searching after finding web_rtc section
                lineNumber += 1

            # Case 1: No web_rtc section exists - add new one with Homeway comment
            if webRtcSectionLineNumber is None:
                self.Logger.info("WebRTC: Adding new web_rtc config section to Home Assistant configuration.")
                webRtcConfig = self._BuildWebRtcConfig(username, password, stunServers, turnServers)
                with open(configFilePath, 'a', encoding="utf-8") as f:
                    f.write(self.c_ConfigLineEnding)
                    f.write(self.c_HomewayCommentFullLine)
                    f.write(webRtcConfig)
                    f.write(self.c_ConfigLineEnding)
                return

            # Case 2: web_rtc section exists but no Homeway comment - add comment with auto_update=false to preserve user config
            if homewayCommentLineNumber is None:
                self.Logger.info("WebRTC: Found existing web_rtc config without Homeway marker. Adding marker with auto_update=false to preserve user config.")
                # Insert the comment line before the web_rtc section
                fullCommentLine = self.c_HomewayCommentFullLine
                # Replace the c_HomewayAutoUpdateFlag to set it to false
                fullCommentLine = fullCommentLine.replace(f"{self.c_HomewayAutoUpdateFlag}:true", f"{self.c_HomewayAutoUpdateFlag}:false")
                lines.insert(webRtcSectionLineNumber, fullCommentLine)
                with open(configFilePath, 'w', encoding="utf-8") as f:
                    f.writelines(lines)
                return

            # Case 3: web_rtc section exists with Homeway comment but auto_update=false
            if not autoUpdateEnabled:
                self.Logger.debug("WebRTC: web_rtc config exists with auto_update=false. Skipping update.")
                return

            # Case 4: web_rtc section exists with Homeway comment and auto_update is not disabled - update the config
            self.Logger.debug("WebRTC: Updating existing web_rtc config section.")
            self._ReplaceWebRtcSection(lines, webRtcSectionLineNumber, homewayCommentLineNumber, username, password, stunServers, turnServers, configFilePath)

        except Exception as e:
            self.Logger.error(f"WebRTC: Error updating web_rtc config: {e}")


    def _BuildWebRtcConfig(self, username:str, password:str, stunServers:List[str], turnServers:List[str]) -> str:
        """Build the web_rtc YAML config content following HA web_rtc integration format.
        See: https://www.home-assistant.io/integrations/web_rtc/
        """
        config = f"web_rtc:{self.c_ConfigLineEnding}"
        config += f"  ice_servers:{self.c_ConfigLineEnding}"

        # Add STUN servers as a single entry with multiple URLs
        if len(stunServers) > 0:
            config += f"    - url:{self.c_ConfigLineEnding}"
            for server in stunServers:
                config += f"        - \"{server}\"{self.c_ConfigLineEnding}"

        # Add TURN servers as a single entry with multiple URLs sharing the same credentials
        if len(turnServers) > 0:
            config += f"    - url:{self.c_ConfigLineEnding}"
            for server in turnServers:
                config += f"        - \"{server}\"{self.c_ConfigLineEnding}"
            config += f"      username: \"{username}\"{self.c_ConfigLineEnding}"
            config += f"      credential: \"{password}\"{self.c_ConfigLineEnding}"

        return config


    def _ReplaceWebRtcSection(self, lines:List[str], webRtcLineNumber:int, commentLineNumber:int, username:str, password:str, stunServers:List[str], turnServers:List[str], configFilePath:str) -> None:
        # Start from the comment line number, which is before the web_rtc section
        if commentLineNumber >= webRtcLineNumber:
            raise ValueError("Comment line number must be before web_rtc section line number.")
        if commentLineNumber + 1 != webRtcLineNumber:
            raise ValueError("Comment line must be immediately before web_rtc section line.")

        # Find the end of the web_rtc section (next top-level key or EOF)
        endLineNumber = webRtcLineNumber + 1
        while endLineNumber < len(lines):
            line = lines[endLineNumber]
            # If line starts with a non-space character and is not empty/comment, it's a new section
            if len(line) > 0 and (line[0].isalnum() or line.startswith("#")):
                break
            endLineNumber += 1

        # Build the new config section
        newConfig = self._BuildWebRtcConfig(username, password, stunServers, turnServers)

        # Remove old lines
        del lines[commentLineNumber:endLineNumber]

        # Insert new content
        lines.insert(commentLineNumber, self.c_HomewayCommentFullLine + newConfig)

        # Write back
        with open(configFilePath, 'w', encoding="utf-8") as f:
            f.writelines(lines)
