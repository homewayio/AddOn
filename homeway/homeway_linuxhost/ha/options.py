import json

from homeway.sentry import Sentry

# Helps manage the Home Assistant config file, that's exposed to the user.
# This is only used for the addon version, the other versions use the config file.\
# If the options file doesn't exist, the Get function will return the default value.
class Options:

    # These are the keys from the options file, as defined in our config.yml file.
    LoggerLevel = "debug_level"

    # This is the location where HA writes the options file, which is auto generated from the
    # config.yml settings and shows up in the HA UI.
    c_HomeAssistantOptionsConfigFilepath = "/data/options.json"

    # Static instance
    _Instance = None


    def __init__(self) -> None:
        self._Options = {}
        self._LoadOptions()


    # Get the static instance of the class.
    @staticmethod
    def Get() -> 'Options':
        if Options._Instance is None:
            Options._Instance = Options()
        return Options._Instance


    # Get an option from the options file.
    def GetOption(self, key: str, default: str = None) -> str:
        try:
            return str(self._Options.get(key, default))
        except Exception as e:
            Sentry.Exception("Failed to get Ha Options key", e)
            return default


    def _LoadOptions(self) -> dict:
        # The file might not exist if we aren't running as the addon or other cases.
        # This is loaded before the logger.
        try:
            with open(Options.c_HomeAssistantOptionsConfigFilepath, "r", encoding="utf-8") as f:
                self._Options = json.load(f)
        except Exception as e:
            Sentry.Exception("Failed to load the options file", e)
