import logging
import os
import threading
from typing import Optional, List

import configparser

# This is what we use as our important settings config.
# It's a bit heavy handed with the lock and aggressive saving, but these
# settings are important, and not accessed much.
class Config:

    # This can't change or all past plugins will fail. It's also used by the installer.
    ConfigFileName = "homeway.conf"

    # We allow strings to be set as None, because then it keeps then in the config with the comment about the key.
    # We use an empty value for None, to indicate that the key is not set.
    c_NoneStringValue = ""

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

    def __init__(self, configDirPath:str) -> None:
        self.Logger:logging.Logger = None #pyright: ignore[reportAttributeAccessIssue]
        # Define our config path
        # Note this path and name MUST STAY THE SAME because the installer PY script looks for this file.
        self.HwConfigFilePath = Config.GetConfigFilePath(configDirPath)
        # A lock to keep file access super safe
        self.ConfigLock = threading.Lock()
        self.Config:configparser.ConfigParser = None #pyright: ignore[reportAttributeAccessIssue]
        # Load the config on init, to ensure it exists.
        # This will throw if there's an error reading the config.
        self._LoadConfigIfNeeded_UnderLock()


    # Returns the config file path given the config folder
    @staticmethod
    def GetConfigFilePath(configDirPath:str) -> str:
        return os.path.join(configDirPath, Config.ConfigFileName)


    # Allows the logger to be set when it's created.
    def SetLogger(self, logger:logging.Logger) -> None:
        self.Logger = logger


    # Forces a full config read & parse from the file.
    def ReloadFromFile(self) -> None:
        # Lock and force a read.
        with self.ConfigLock:
            self._LoadConfigIfNeeded_UnderLock(forceRead=True)


    # Gets a value from the config given the header and key.
    # If the value isn't set, the default value is returned and the default value is saved into the config.
    # If the default value is None, the default will not be written into the config.
    def GetStr(self, section:str, key:str, defaultValue:Optional[str], keepInConfigIfNone=False) -> Optional[str]:
        with self.ConfigLock:
            # Ensure we have the config.
            self._LoadConfigIfNeeded_UnderLock()
            # Check if the section and key exists
            if self.Config.has_section(section):
                if key in self.Config[section].keys():
                    value = self.Config[section][key]
                    # If None or empty string written consider it not a valid value so use the default value.
                    # The default value logic will handle the keepInConfigIfNone case.
                    # Use lower, to accept user generated errors.
                    if value.lower() != "none" and len(value) > 0:
                        # Reverse any possible string replaces we had to add.
                        value = value.replace(Config.PercentageStringReplaceString, "%")
                        return value
        # The value wasn't set, create it using the default.
        self.SetStr(section, key, defaultValue, keepInConfigIfNone)
        return defaultValue


    # Just like GetStr, but it will always return a str and never none.
    def GetStrRequired(self, section:str, key:str, defaultValue:str) -> str:
        r = self.GetStr(section, key, defaultValue)
        if r is None:
            return defaultValue
        return r


    # Gets a value from the config given the header and key.
    # If the value isn't set, the default value is returned and the default value is saved into the config.
    # If the default value is None, the default will not be written into the config.
    def GetInt(self, section:str, key:str, defaultValue:Optional[int]) -> Optional[int]:
        # Use a try catch, so if a user sets an invalid value, it doesn't crash us.
        result = None

        # Convert the default value to a string, if it's not None.
        defaultValueAsStr:Optional[str] = None
        if defaultValue is not None:
            defaultValueAsStr = str(defaultValue)

        try:
            result = self.GetStr(section, key, defaultValueAsStr)
            # If None is returned, don't int it, return None.
            if result is None:
                return defaultValue
            return int(result)

        except Exception as e:
            self.Logger.error(f"Config settings error! {key} failed to get as int. Value was `{result}`. Resetting to default. "+str(e))
            self.SetStr(section, key, defaultValueAsStr)
            return defaultValue


    # Just like GetInt, but it will always return a int and never none.
    def GetIntRequired(self, section:str, key:str, defaultValue:int) -> int:
        r = self.GetInt(section, key, defaultValue)
        if r is None:
            return defaultValue
        return r


    def SetInt(self, section:str, key:str, value:Optional[int], keepInConfigIfNone=False) -> None:
        # Ensure the value is a string, unless it's None.
        s:Optional[str] = None
        if value is not None:
            s = str(value)
        self.SetStr(section, key, s, keepInConfigIfNone)


    # Gets a value from the config given the header and key.
    # If the value isn't set, the default value is returned and the default value is saved into the config.
    # If the default value is None, the default will not be written into the config.
    def GetFloat(self, section:str, key:str, defaultValue:Optional[float]) -> Optional[float]:
        # Use a try catch, so if a user sets an invalid value, it doesn't crash us.
        result = None

        # Convert the default value to a string, if it's not None.
        defaultValueAsStr:Optional[str] = None
        if defaultValue is not None:
            defaultValueAsStr = str(defaultValue)

        try:
            result = self.GetStr(section, key, defaultValueAsStr)
            # If None is returned, don't int it, return the default value, which might be None.
            if result is None:
                return defaultValue
            return float(result)

        except Exception as e:
            self.Logger.error(f"Config settings error! {key} failed to get as float. Value was `{result}`. Resetting to default. "+str(e))
            self.SetStr(section, key, defaultValueAsStr)
            return defaultValue


    # Just like GetFloat, but it will always return a float and never none.
    def GetFloatRequired(self, section:str, key:str, defaultValue:float) -> float:
        r = self.GetFloat(section, key, defaultValue)
        if r is None:
            return defaultValue
        return r


    def SetFloat(self, section:str, key:str, value:Optional[float], keepInConfigIfNone=False) -> None:
        # Ensure the value is a string, unless it's None.
        s:Optional[str] = None
        if value is not None:
            s = str(value)
        self.SetStr(section, key, s, keepInConfigIfNone)


    # Gets a value from the config given the header and key.
    # If the value isn't set, the default value is returned and the default value is saved into the config.
    # If the default value is None, the default will not be written into the config.
    def GetBool(self, section:str, key:str, defaultValue:Optional[bool]) -> Optional[bool]:
        # Use a try catch, so if a user sets an invalid value, it doesn't crash us.
        result = None

        # Convert the default value to a string, if it's not None.
        defaultValueAsStr:Optional[str] = None
        if defaultValue is not None:
            defaultValueAsStr = str(defaultValue)

        try:
            strValue = self.GetStr(section, key, defaultValueAsStr)
            # If None is returned, don't bool it, return the default value, which might be None
            if strValue is None:
                return defaultValue
            # Match it to a bool value.
            strValue = strValue.lower()
            if strValue == "false":
                return False
            elif strValue == "true":
                return True
            raise Exception("Invalid bool value, value was: "+strValue)
        except Exception as e:
            self.Logger.error(f"Config settings error! {key} failed to get as bool. Value was `{result}`. Resetting to default. "+str(e))
            self.SetStr(section, key, defaultValueAsStr)
            return defaultValue


    # Just like GetBool, but it will always return a bool and never none.
    def GetBoolRequired(self, section:str, key:str, defaultValue:bool) -> bool:
        r = self.GetBool(section, key, defaultValue)
        if r is None:
            return defaultValue
        return r


    def SetBool(self, section:str, key:str, value:Optional[bool], keepInConfigIfNone=False) -> None:
        # Ensure the value is a string, unless it's None.
        s:Optional[str] = None
        if value is not None:
            s = str(value)
        self.SetStr(section, key, s, keepInConfigIfNone)


    # The same as Get, but this version ensures that the value matches a case insensitive value in the
    # acceptable value list. If it's not, the default value is used.
    def GetStrIfInAcceptableList(self, section:str, key:str, defaultValue:str, acceptableValueList:List[str]) -> str:
        existing = self.GetStr(section, key, defaultValue)

        if existing is not None:
            # Check the acceptable values
            for v in acceptableValueList:
                # If we match, this is a good value, return it.
                if v.lower() == existing.lower():
                    return existing

        # The acceptable was not found. Set they key back to default.
        self.SetStr(section, key, defaultValue)
        return defaultValue


    # The same as Get, but it makes sure the value is in a range.
    def GetIntIfInRange(self, section:str, key:str, defaultValue:Optional[int], lowerBoundInclusive:int, upperBoundInclusive:int) -> int:
        # A default value of None is not allowed here.
        if defaultValue is None:
            raise Exception(f"A default value of none is not valid for int ranges. {section}:{key}")

        existingStr = self.GetStr(section, key, str(defaultValue))
        if existingStr is not None:
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
    def SetStr(self, section:str, key:str, value:Optional[str], keepInConfigIfNone=False) -> None:
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
            # If the value is None but we want to keep it in the config, set it to the None string.
            if value is None and keepInConfigIfNone is True:
                value = Config.c_NoneStringValue
            # If the value is still None, we will make sure the key is deleted.
            if value is None:
                # None is a special case, if we are setting it, delete the key if it exists.
                if key in self.Config[section].keys():
                    del self.Config[section][key]
                else:
                    # If there was no key, return early, since we did nothing.
                    # This is a common case, since we use GetStr(..., ..., None) often to get the value if it exists.
                    return
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
        if os.path.exists(self.HwConfigFilePath):
            self.Config.read(self.HwConfigFilePath)
        else:
            # If no config exists, create a new file by writing the empty config now.
            #print("Config file doesn't exist. Creating a new file now!")
            self._SaveConfig_UnderLock()


    def _SaveConfig_UnderLock(self) -> None:
        if self.Config is None:
            return

        # Write the current settings to the file.
        # This lets the config lib format everything how it wants.
        with open(self.HwConfigFilePath, 'w', encoding="utf-8") as f:
            self.Config.write(f)

        # After writing, read the file and insert any comments we have.
        finalOutput = ""
        with open(self.HwConfigFilePath, 'r', encoding="utf-8") as f:
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
        with open(self.HwConfigFilePath, 'w', encoding="utf-8") as f:
            f.write(finalOutput)
