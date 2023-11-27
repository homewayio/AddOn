import os
import threading
import logging

import configparser

from .config import Config

# This class is very similar to the config class, but since the config files are often backup
# in public places, the secrets are stored else where.
class Secrets:

    # These must stay the same because our installer script requires on the format being as is!
    _SecretsSection = "secrets"
    _PluginIdKey = "plugin_id"
    _PrivateKeyKey = "private_key"


    # This allows us to add comments into our config.
    # The objects must have two parts, first, a string they target. If the string is found, the comment will be inserted above the target string. This can be a section or value.
    # A string, which is the comment to be inserted.
    c_SecretsConfigComments = [
        { "Target": _PluginIdKey,  "Comment": "Uniquely identifies your plugin. Don't change or will have to re-link your plugin with the service."},
        { "Target": _PrivateKeyKey, "Comment": "A private key linked to your plugin ID. NEVER share this and also don't change it."},
    ]


    def __init__(self, logger:logging.Logger, localStoragePath:str, config:Config) -> None:
        self.Logger = logger

        # Note this path and name MUST STAY THE SAME because the installer PY script looks for this file.
        self.SecretFilePath = os.path.join(localStoragePath, "homeway.secrets")

        # A lock to keep file access super safe
        self.ConfigLock = threading.Lock()
        self.Config = None

        # Load the secret config on init, to ensure it exists.
        # This will throw if there's an error reading the config.
        self._LoadConfigIfNeeded_UnderLock()


    # Returns the plugin id if one exists, otherwise None.
    def GetPluginId(self) -> str:
        return self._GetStr(Secrets._SecretsSection, Secrets._PluginIdKey)


    # Sets the plugin id and saves the file.
    def SetPluginId(self, pluginId):
        self._SetStr(Secrets._SecretsSection, Secrets._PluginIdKey, pluginId)


    # Returns the private key if one exists, otherwise None.
    def GetPrivateKey(self) -> str:
        return self._GetStr(Secrets._SecretsSection, Secrets._PrivateKeyKey)


    # Sets the plugin id and saves the file.
    def SetPrivateKey(self, privateKey):
        self._SetStr(Secrets._SecretsSection, Secrets._PrivateKeyKey, privateKey)


    # Gets a value from the config given the header and key.
    # If the value doesn't exist, None is returned.
    def _GetStr(self, section, key) -> str:
        with self.ConfigLock:
            # Ensure we have the config.
            self._LoadConfigIfNeeded_UnderLock()
            # Check if the section and key exists
            if self.Config.has_section(section):
                if key in self.Config[section].keys():
                    return self.Config[section][key]
        return None


    # Sets the value into the config and saves it.
    def _SetStr(self, section, key, value) -> None:
        # Ensure the value is a string.
        if value is not None:
            value = str(value)
        with self.ConfigLock:
            self._LoadConfigIfNeeded_UnderLock()
            # Ensure the section exists
            if self.Config.has_section(section) is False:
                self.Config.add_section(section)
            if value is None:
                # If we are setting to None, delete the key if it exists.
                if key in self.Config[section].keys():
                    del self.Config[section][key]
            else:
                # If not none, set the key
                self.Config[section][key] = value
            self._SaveConfig_UnderLock()


    def _LoadConfigIfNeeded_UnderLock(self, forceRead = False) -> None:
        if self.Config is not None and forceRead is False:
            return

        # Always create a new object.
        # For our config, we use strict and such, so we know the config is valid.
        self.Config = configparser.ConfigParser()

        # If a config exists, read it.
        # This will throw on failure.
        if os.path.exists(self.SecretFilePath):
            self.Config.read(self.SecretFilePath)
        else:
            # If no config exists, create a new file by writing the empty config now.
            print("Secrets file doesn't exist. Creating a new file now!")
            self._SaveConfig_UnderLock()


    def _SaveConfig_UnderLock(self) -> None:
        if self.Config is None:
            return

        # Write the current settings to the file.
        # This lets the config lib format everything how it wants.
        with open(self.SecretFilePath, 'w', encoding="utf-8") as f:
            self.Config.write(f)

        # After writing, read the file and insert any comments we have.
        finalOutput = ""
        with open(self.SecretFilePath, 'r', encoding="utf-8") as f:
            # Read all lines
            lines = f.readlines()
            for line in lines:
                lineLower = line.lower()
                # If anything in the line matches the target, add the comment just before this line.
                for cObj in Secrets.c_SecretsConfigComments:
                    if cObj["Target"] in lineLower:
                        # Add the comment.
                        finalOutput += "# " + cObj["Comment"] + os.linesep
                        break
                finalOutput += line

        # Finally, write the file back one more time.
        with open(self.SecretFilePath, 'w', encoding="utf-8") as f:
            f.write(finalOutput)
