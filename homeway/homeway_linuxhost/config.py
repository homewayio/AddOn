import os
import threading
from pathlib import Path

import configparser

# This is what we use as our important settings config.
# It's a bit heavy handed with the lock and aggressive saving, but these
# settings are important, and not accessed much.
class Config:

    # This can't change or all past plugins will fail. It's also used by the installer.
    ConfigFileName = "homeway.conf"

    # Vars relating to how the addon connects to Home Assistant
    # These vars are also used by the linux installer.
    HomeAssistantSection = "home_assistant"
    HaIpOrHostnameKey = "hostname_or_ip"
    HaPortKey = "port"
    HaUseHttps = "use_https"
    HaAccessTokenKey = "access_token"

    # Logging stuff.
    LoggingSection = "logging"
    LogLevelKey = "log_level"
    LogFileMaxSizeMbKey = "max_file_size_mb"
    LogFileMaxCountKey = "max_file_count"

    # Sage stuff.
    SageSection = "sage"
    SagePrefixStringKey = "sage_prefix"

    # This allows us to add comments into our config.
    # The objects must have two parts, first, a string they target. If the string is found, the comment will be inserted above the target string. This can be a section or value.
    # A string, which is the comment to be inserted.
    c_ConfigComments = [
        { "Target": HaIpOrHostnameKey,  "Comment": "This is the IP or hostname used to connect to Home Assistant."},
        { "Target": HaPortKey,  "Comment": "This is the port used to connect to Home Assistant."},
        { "Target": HaUseHttps,  "Comment": "True or false if the ip/port requires https."},
        { "Target": HaAccessTokenKey,  "Comment": "Required for standalone addon installs, not required for addon installs. This is the long lived access token used to connect to Home Assistant."},
        { "Target": LogLevelKey,  "Comment": "The active logging level. Valid values include: DEBUG, INFO, WARNING, or ERROR."},
        { "Target": SagePrefixStringKey,  "Comment": "If set, this will prefix the Sage services names with the given string, which is helpful if you run multiple instances of Homeway. This should not have spaces!."},
    ]

    # The config lib we use doesn't support the % sign, even though it's valid .cfg syntax.
    # Since we save URLs into the config for the webcam, it's valid syntax to use a %20 and such, thus we should support it.
    PercentageStringReplaceString = "~~~PercentageSignPlaceholder~~~"

    def __init__(self, storageDir) -> None:
        self.Logger = None
        # Define our config path
        # Note this path and name MUST STAY THE SAME because the installer PY script looks for this file.
        # Ensure the dir exists
        configDir = os.path.join(storageDir, "config")
        Path(configDir).mkdir(parents=True, exist_ok=True)
        self.ConfigFilePath = os.path.join(configDir, Config.ConfigFileName)
        # A lock to keep file access super safe
        self.ConfigLock = threading.Lock()
        self.Config = None
        # Load the config on init, to ensure it exists.
        # This will throw if there's an error reading the config.
        self._LoadConfigIfNeeded_UnderLock()


    # Allows the logger to be set when it's created.
    def SetLogger(self, logger):
        self.Logger = logger


    # Forces a full config read & parse from the file.
    def ReloadFromFile(self) -> None:
        # Lock and force a read.
        with self.ConfigLock:
            self._LoadConfigIfNeeded_UnderLock(forceRead=True)


    # Gets a value from the config given the header and key.
    # If the value isn't set, the default value is returned and the default value is saved into the config.
    def GetStr(self, section, key, defaultValue) -> str:
        with self.ConfigLock:
            # Ensure we have the config.
            self._LoadConfigIfNeeded_UnderLock()
            # Check if the section and key exists
            if self.Config.has_section(section):
                if key in self.Config[section].keys():
                    # If the value of None was written, it was an accidental serialized None value to string.
                    # Consider it not a valid value, and use the default value.
                    value = self.Config[section][key]
                    if value != "None":
                        # Reverse any possible string replaces we had to add.
                        value = value.replace(Config.PercentageStringReplaceString, "%")
                        return value
        # The value wasn't set, create it using the default.
        self.SetStr(section, key, defaultValue)
        return defaultValue


    # Gets a value from the config given the header and key.
    # If the value isn't set, the default value is returned and the default value is saved into the config.
    def GetInt(self, section, key, defaultValue) -> int:
        # Use a try catch, so if a user sets an invalid value, it doesn't crash us.
        try:
            return int(self.GetStr(section, key, str(defaultValue)))
        except Exception as e:
            if self.Logger is not None:
                self.Logger.error("Config settings error! "+key+" failed to get as int. Resetting to default. "+str(e))
            self.SetStr(section, key, str(defaultValue))
            return int(defaultValue)


    # Gets a value from the config given the header and key.
    # If the value isn't set, the default value is returned and the default value is saved into the config.
    def GetBool(self, section, key, defaultValue) -> bool:
        # Use a try catch, so if a user sets an invalid value, it doesn't crash us.
        try:
            strValue = self.GetStr(section, key, str(defaultValue)).lower()
            if strValue == "false":
                return False
            elif strValue == "true":
                return True
            raise Exception("Invalid bool value, value was: "+strValue)
        except Exception as e:
            if self.Logger is not None:
                self.Logger.error("Config settings error! "+key+" failed to get as bool. Resetting to default. "+str(e))
            self.SetStr(section, key, str(defaultValue))
            return bool(defaultValue)


    # The same as Get, but this version ensures that the value matches a case insensitive value in the
    # acceptable value list. If it's not, the default value is used.
    def GetStrIfInAcceptableList(self, section, key, defaultValue, acceptableValueList) -> str:
        existing = self.GetStr(section, key, defaultValue)
        # Check the acceptable values
        for v in acceptableValueList:
            # If we match, this is a good value, return it.
            if v.lower() == existing.lower():
                return existing

        # The acceptable was not found. Set they key back to default.
        self.SetStr(section, key, defaultValue)
        return defaultValue


    # The same as Get, but it makes sure the value is in a range.
    def GetIntIfInRange(self, section, key, defaultValue, lowerBoundInclusive, upperBoundInclusive) -> int:
        existingStr = self.GetStr(section, key, str(defaultValue))

        # Make sure the value is in range.
        try:
            existing = int(existingStr)
            if existing >= lowerBoundInclusive and existing <= upperBoundInclusive:
                return existing
        except Exception:
            pass

        # The acceptable was not found. Set they key back to default.
        self.SetStr(section, key, str(defaultValue))
        return defaultValue


    # Sets the value into the config and saves it.
    # Setting a value of None will delete the key from the config.
    def SetStr(self, section, key, value) -> None:
        # Ensure the value is a string, unless it's None
        if value is not None:
            value = str(value)
            # The config library we use doesn't allow for % to be used in strings, even though it should be legal.
            value = value.replace("%", Config.PercentageStringReplaceString)
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
        if os.path.exists(self.ConfigFilePath):
            self.Config.read(self.ConfigFilePath)
        else:
            # If no config exists, create a new file by writing the empty config now.
            print("Config file doesn't exist. Creating a new file now!")
            self._SaveConfig_UnderLock()


    def _SaveConfig_UnderLock(self) -> None:
        if self.Config is None:
            return

        # Write the current settings to the file.
        # This lets the config lib format everything how it wants.
        with open(self.ConfigFilePath, 'w', encoding="utf-8") as f:
            self.Config.write(f)

        # After writing, read the file and insert any comments we have.
        finalOutput = ""
        with open(self.ConfigFilePath, 'r', encoding="utf-8") as f:
            # Read all lines
            lines = f.readlines()
            for line in lines:
                lineLower = line.lower()
                # If anything in the line matches the target, add the comment just before this line.
                for cObj in Config.c_ConfigComments:
                    if cObj["Target"] in lineLower:
                        # Add the comment.
                        finalOutput += "# " + cObj["Comment"] + os.linesep
                        break
                finalOutput += line

        # Finally, write the file back one more time.
        with open(self.ConfigFilePath, 'w', encoding="utf-8") as f:
            f.write(finalOutput)
