import random
import string
import logging
from typing import Optional

from .memorymanager import MemoryManager
from .compression import Compression
from .mdns import MDns
from .telemetry import Telemetry
from .pingpong import PingPong

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


    # Inits stuff that's common to ALL hosts.
    @staticmethod
    def Init(logging:logging.Logger, printerId:str, localStorageDir:str, devLocalServerAddress:Optional[str]=None) -> None:
        # Init the memory manager and allow it to setup the memory limits.
        MemoryManager.Init(logging)

        # Enable to enable memory debugging
        # self.MemoryDebugger = MemoryDebug(logging)

        # Init compression
        Compression.Init(logging, localStorageDir)

        # Init the mdns client
        MDns.Init(logging, localStorageDir)

        # Init telemetry
        Telemetry.Init(logging)
        if devLocalServerAddress is not None:
            Telemetry.SetServerProtocolAndDomain("http://"+devLocalServerAddress)

        # Init the ping pong helper.
        PingPong.Init(logging, localStorageDir, printerId)
        if devLocalServerAddress is not None:
            PingPong.Get().DisablePrimaryOverride()


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
            return f"{fullHostString}/PluginWebsocketConnectionV2"
        return f"wss://{subdomain}.homeway.io/PluginWebsocketConnectionV2"
