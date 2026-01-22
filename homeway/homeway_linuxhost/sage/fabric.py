import time
import random
import logging
import threading
from typing import Dict, Optional

from homeway.buffer import Buffer
from homeway.interfaces import IWebSocketClient, WebSocketOpCode
from homeway.sentry import Sentry
from homeway.websocketimpl import Client
from homeway.pingpong import PingPong

from .interfaces import IFiberManager

# Manages the Sage Fabric connection with the Homeway service.
class Fabric:

    # For debugging, it's too chatty to enable always.
    c_LogWsMessages = False

    def __init__(self, logger:logging.Logger, fiberManager:IFiberManager, pluginId:str, apiKey:str, addonVersion:str, devLocalHomewayServerAddress:Optional[str]) -> None:
        self.Logger = logger
        self.FiberManager = fiberManager
        self.PrinterId = pluginId
        self.ApiKey = apiKey
        self.AddonVersion = addonVersion
        self.DevLocalHomewayServerAddress = devLocalHomewayServerAddress

        # The current websocket connection and Id
        self.ConId:int = 0
        self.BackoffCounter:int = 0
        self.Ws:Optional[Client] = None

        # Indicates if the connection is connection and authed.
        self.IsConnected = False
        self.LastSuccessfulConnectSec:float = 0.0
        self.StateLock = threading.Lock()


    # Returns if the socket is currently connected.
    def GetIsConnected(self) -> bool:
        return self.IsConnected


    # Updates the API key if we get a new one from the server.
    # This will also refresh the connection if the key changed or it hasn't been refreshed recently.
    def UpdateApiKeyAndRefreshIfNeeded(self, apiKey:str) -> None:
        # Always update the key.
        # Even if the key changed we don't need to refresh, because if we are successfully connected, they key was already accepted.
        self.ApiKey = apiKey

        # Due to timing differences between the random back off on the main WS and this WS, it can be possible that both WS disconnect from the server
        # and then auto re-connect. This sage socket might reconnect first, but then the main WS will call refresh on this socket.
        # To prevent us from reconnecting twice, we need to check the last successful connect time. If it's been within a minute, we don't need to refresh.
        if time.time() - self.LastSuccessfulConnectSec < 60.0:
            self.Logger.info(f"{self._getLogTag()} Not refreshing the connection because we recently connected.")
            return
        self.Logger.info(f"{self._getLogTag()} Refreshing the connection due to new main WS connection.")
        self.Close()


    # Starts the connection thread.
    def Start(self) -> None:
        t = threading.Thread(target=self._ConnectionThread)
        t.daemon = True
        t.start()


    # Closes the connection, if it's open.
    def Close(self) -> None:
        # Use lock to safely read Ws and IsConnected together
        with self.StateLock:
            ws = self.Ws
            isConnected = self.IsConnected
        # If there's an active websocket.
        if ws is not None:
            # And it's connected (don't interrupt the connection process)
            if isConnected is True:
                ws.Close()


    # Sends a message using fabric.
    # Returns True on success.
    def SendMsg(self, data:Buffer, dataStartOffsetBytes:int, dataLength:int) -> bool:
        # Use lock to safely read Ws and IsConnected together to prevent race conditions
        with self.StateLock:
            ws = self.Ws
            isConnected = self.IsConnected
        # Capture and check the websocket.
        if ws is None:
            self.Logger.error(f"{self._getLogTag()} message tried to be sent while we have no active socket.")
            return False
        # Check the connection state.
        if isConnected is False:
            self.Logger.error(f"{self._getLogTag()} message tried to be sent while we weren't connected.")
            return False

        try:
            ws.Send(data, dataStartOffsetBytes, dataLength)
            return True
        except Exception as e:
            Sentry.OnException("Sage Fabric SendMsg exception.", e)
        return False


    # Called when the websocket is up and authed.
    def _OnConnected(self, _:IWebSocketClient) -> None:
        self.Logger.info(f"{self._getLogTag()} Successfully authed and connected!")

        # Set connected, mark the time, and clear the backoff counter.
        with self.StateLock:
            self.IsConnected = True
        self.LastSuccessfulConnectSec = time.time()
        self.BackoffCounter = 0


    # Runs the main connection loop.
    def _ConnectionThread(self):
        while True:

            # Reset the state vars
            with self.StateLock:
                self.IsConnected = False
                self.Ws = None
            self.FiberManager.OnSocketReset()

            # Always increment the backoff counter.
            # We allow the backoff counter to go up to 20 minutes, for websockets that are failing to connect, we don't want to hammer the server.
            self.BackoffCounter += 1
            self.BackoffCounter = min(self.BackoffCounter, 400)

            isFirstReconnect = False
            if self.ConId == 0:
                # If this is our first connection ever, don't sleep and allow the use of the lowest latency server.
                isFirstReconnect = True
            else:
                # If this is the first reconnect attempt, allow the use of the lowest latency server.
                if self.BackoffCounter == 1:
                    isFirstReconnect = True
                # Sleep for the backoff time.
                sleepTimeSec = 3 * self.BackoffCounter + random.randint(5, 10)
                self.Logger.error(f"{self._getLogTag()} sleeping [{sleepTimeSec}s] before trying the Sage Fabric connection again.")
                time.sleep(sleepTimeSec)
            self.ConId += 1

            try:
                # Called when the websocket is closed.
                def Closed(ws:IWebSocketClient):
                    self.Logger.info(f"{self._getLogTag()} Websocket closed")

                # Get the subdomain to use. If possible, we want to use the low latency server.
                # If this is our first reconnect, then we should try to lowest latency server.
                subdomain = "hw-sage-v1"
                if isFirstReconnect:
                    lowestLatencySub = PingPong.Get().GetLowestLatencyServerSub()
                    if lowestLatencySub is not None:
                        subdomain = lowestLatencySub

                # Build the full URL, allow the dev config to override it.
                uri = f"wss://{subdomain}.homeway.io/sage-fabric-websocket"
                if self.DevLocalHomewayServerAddress is not None and len(self.DevLocalHomewayServerAddress) > 0:
                    self.Logger.info(f"{self._getLogTag()} Using dev local server address [{self.DevLocalHomewayServerAddress}]")
                    uri = f"ws://{self.DevLocalHomewayServerAddress}/sage-fabric-websocket"

                # Setup the headers.
                headers:Dict[str, str] = {}
                headers["X-Plugin-Id"] = self.PrinterId
                headers["X-Api-Key"] = self.ApiKey
                headers["x-Addon-Version"] = self.AddonVersion

                # Start the websocket.
                self.Logger.info(f"{self._getLogTag()} Starting fabric connection to [{uri}]")
                ws = Client(uri, onWsOpen=self._OnConnected, onWsData=self._OnData, onWsClose=Closed, headers=headers)
                with self.StateLock:
                    self.Ws = ws

                # Run until success or failure.
                ws.RunUntilClosed()

                self.Logger.info(f"{self._getLogTag()} Loop restarting.")

            except Exception as e:
                Sentry.OnException("Sage Fabric ConnectionThread exception.", e)


    def _OnData(self, _:IWebSocketClient, buffer:Buffer, msgType:WebSocketOpCode) -> None:
        try:
            # This should always be a binary message.
            if msgType is not WebSocketOpCode.BINARY:
                raise Exception(f"{self._getLogTag()} Received non-binary websocket message received.")

            # Let the fiber manager handle the incoming message.
            self.FiberManager.OnIncomingMessage(buffer)

        except Exception as e:
            Sentry.OnException("Sage Fiber _OnData exception.", e)
            self.Close()


    def _getLogTag(self) -> str:
        return f"Sage Fabric [{self.ConId}]"
