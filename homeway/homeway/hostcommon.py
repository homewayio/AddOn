import random
import string
from typing import Optional

# Common functions that the hosts might need to use.
class HostCommon:

    # The length the plugin ID should be.
    # Note that the max length for a subdomain part (strings between . ) is 63 chars!
    # Making this a max of 60 chars allows for the service to use 3 chars prefixes for inter-service calls.
    c_PluginIdMaxLength = 60

    # The required length of the private key.
    c_PrivateKeyLength = 80

    # The url for the add plugin process.
    c_AddPluginUrl = "https://homeway.io/getstarted"


    # Returns a new plugin Id. This needs to be crypo-random to make sure it's not predictable.
    @staticmethod
    def GeneratePluginId() -> str:
        return ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.digits) for _ in range(HostCommon.c_PluginIdMaxLength))


    # Returns a new private key. This needs to be crypo-random to make sure it's not predictable.
    @staticmethod
    def GeneratePrivateKey() -> str:
        return ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(HostCommon.c_PrivateKeyLength))


    @staticmethod
    def IsPluginIdValid(pluginId:Optional[str]) -> bool:
        return pluginId is not None and len(pluginId) == HostCommon.c_PluginIdMaxLength


    @staticmethod
    def IsPrivateKeyValid(privateKey:Optional[str]) -> bool:
        return privateKey is not None and len(privateKey) == HostCommon.c_PrivateKeyLength


    @staticmethod
    def GetAddPluginUrl(pluginId:str) -> str:
        return f"{HostCommon.c_AddPluginUrl}?id=" + pluginId


    @staticmethod
    def GetPluginConnectionUrl(subdomain:Optional[str]=None, fullHostString:Optional[str]=None) -> str:
        if subdomain is None:
            subdomain = "starport-v1"
        if fullHostString is not None:
            return f"{fullHostString}/PluginWebsocketConnection"
        return f"wss://{subdomain}.homeway.io/PluginWebsocketConnection"
