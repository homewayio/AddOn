import os
import json
from typing import Any, Dict, Optional

from homeway.sentry import Sentry

# Helps manage the Home Assistant config file, that's exposed to the user.
# This is only used for the addon version, the other versions use the config file.
# If the options file doesn't exist, the Get function will return the default value.
class Options:

    # These are the keys from the options file, as defined in our config.yml file.
    LoggerLevel = "debug_level"

    # This is the location where HA writes the options file, which is auto generated from the
    # config.yml settings and shows up in the HA UI.
    c_HomeAssistantOptionsConfigFilepath = "/data/options.json"

    # Get an option from the options file, if possible.
    @staticmethod
    def GetOption(key:str, default:Optional[str]=None) -> Optional[str]:
        try:
            # Try to load the options file, if it doesn't exist or fails to load, we'll just return the default value.
            options = Options._LoadOptions()
            if options is None:
                return default
            # Try to get the option value.
            val = options.get(key, default)
            if val is None:
                return None
            return str(val)
        except Exception as e:
            Sentry.OnException("Failed to get Ha Options key", e)
        return default


    @staticmethod
    def _LoadOptions() -> Optional[Dict[str, Any]]:
        # The file might not exist if we aren't running as the addon or other cases.
        # This is loaded before the logger.
        try:
            if not os.path.exists(Options.c_HomeAssistantOptionsConfigFilepath):
                return None
            with open(Options.c_HomeAssistantOptionsConfigFilepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            Sentry.OnException("Failed to load the options file", e)
        return None
