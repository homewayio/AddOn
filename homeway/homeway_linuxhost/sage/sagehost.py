import time
import asyncio
import logging
import threading
from functools import partial

from wyoming.server import AsyncServer
from wyoming.zeroconf import register_server

from homeway.sentry import Sentry
from ..ha.homecontext import HomeContext

from .fabric import Fabric
from .sagehandler import SageHandler
from .sagehistory import SageHistory
from .fibermanager import FiberManager

# The main root host for Sage
class SageHost:

    # This is the server port we will use to run the wyoming server.
    # Maybe this should be dynamic to support multiple instances, but it can't change after it's been discovered.
    c_ServerPort = 11027

    def __init__(self, logger:logging.Logger, addonVersion:str, homeContext:HomeContext, sagePrefix_CanBeNone:str, devLocalHomewayServerAddress_CanBeNone:str):
        self.Logger = logger
        self.AddonVersion = addonVersion
        self.HomeContext = homeContext
        self.SagePrefix_CanBeNone = sagePrefix_CanBeNone
        self.DevLocalHomewayServerAddress_CanBeNone = devLocalHomewayServerAddress_CanBeNone
        self.PluginId:str = None
        self.ApiKey:str = None
        self.Fabric:Fabric = None
        self.FiberManager:FiberManager = None
        self.SageHistory:SageHistory = SageHistory(logger)


    # Once the api key is known, we can start.
    # Note this is called every time the main WS connection to Homeway is reset, which happens about every day.
    def StartOrRefresh(self, pluginId:str, apiKey:str):

        # Set or update these values.
        self.PluginId = pluginId
        self.ApiKey = apiKey

        # After the first run, we just do a restart to refresh.
        if self.Fabric is not None:
            # Set the new key into the fabric and refresh the fabric if needed.
            self.Fabric.UpdateApiKeyAndRefreshIfNeeded(self.ApiKey)
            return

        self.Logger.info("Starting Sage Fabric connection.")

        # This is the first run, get things going.
        self.FiberManager = FiberManager(self.Logger)
        self.Fabric = Fabric(self.Logger, self.FiberManager, self.PluginId, self.ApiKey, self.AddonVersion, self.DevLocalHomewayServerAddress_CanBeNone)
        self.FiberManager.SetFabric(self.Fabric)
        self.Fabric.Start()

        # Start an independent thread to run asyncio.
        threading.Thread(target=self._run).start()


    def _run(self):
        # A main protector for the asyncio loop.
        while True:
            try:
                asyncio.run(self._ServerThread())
            except Exception as e:
                Sentry.OnException("SageHost Asyncio Error", e)
            self.Logger.error("Sage exited the asyncio loop. Restarting in 30 seconds.")
            time.sleep(30)


    # The main asyncio loop for the server.
    async def _ServerThread(self):

        # Setup the server
        self.Logger.info(f"Starting wyoming server on port {SageHost.c_ServerPort}")
        server = AsyncServer.from_uri(f"tcp://0.0.0.0:{SageHost.c_ServerPort}")

        # Setup zeroconf for Home Assistant discovery.
        try:
            # The name seems to be anything with no spaces, usually using _
            # The port is the port the server is running on.
            # We don't set the host, which makes the function get the system's IP.
            serverName = "Homeway_Zeroconf"
            if self.SagePrefix_CanBeNone is not None:
                serverName = f"{self.SagePrefix_CanBeNone}_Homeway_Zeroconf"
                serverName = serverName.replace(" ", "_")
            await register_server(
                name=serverName,
                port=server.port,
            )
        except Exception as e:
            Sentry.OnException("Zeroconf Error", e)

        # Run!
        await server.run(
            partial(
                SageHandler,
                self.Logger,
                self.Fabric,
                self.FiberManager,
                self.HomeContext,
                self.SageHistory,
                self.SagePrefix_CanBeNone,
                self.DevLocalHomewayServerAddress_CanBeNone
            )
        )
