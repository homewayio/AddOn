from typing import Optional, Tuple

from homeway.homeway_linuxhost.config import Config

from .Logging import Logger
from .Context import Context

class ConfigFile:

    # Returns the (ip:str, port:str, accessToken:str) if the config can be parsed. Otherwise (None, None, None)
    @staticmethod
    def TryToParseConfig(addonFolder:str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        try:
            config = Config(addonFolder)
            ip = config.GetStr(Config.HomeAssistantSection, Config.HaIpOrHostnameKey, None)
            port = config.GetStr(Config.HomeAssistantSection, Config.HaPortKey, None)
            accessToken = config.GetStr(Config.HomeAssistantSection, Config.HaAccessTokenKey, None)
            if ip is None or port is None or accessToken is None:
                return (None, None, None)
            return (ip, port, accessToken)
        except Exception as e:
            Logger.Debug(f"Failed to parse config: {addonFolder}; " + str(e))
        return (None, None, None)


    # Creates or uses an existing config, updates the ip and port.
    @staticmethod
    def UpdateConfig(context:Context, ip:str, port:str, accessToken:str) -> bool:
        try:
            config = Config(context.AddonFolder)
            config.SetStr(Config.HomeAssistantSection, Config.HaIpOrHostnameKey, ip)
            config.SetStr(Config.HomeAssistantSection, Config.HaPortKey, port)
            config.SetStr(Config.HomeAssistantSection, Config.HaAccessTokenKey, accessToken)
            return True
        except Exception as e:
            Logger.Error("Failed to write config. "+str(e))
        return False
