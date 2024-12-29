import time
import logging
import threading
import octowebsocket

from homeway.sentry import Sentry
from homeway.websocketimpl import Client

# Manages the Sage Fabric connection with the Homeway service.
class Fabric:

    # For debugging, it's too chatty to enable always.
    c_LogWsMessages = False

    def __init__(self, logger:logging.Logger, fiberManager, pluginId:str, apiKey:str) -> None:
        self.Logger = logger
        self.FiberManager = fiberManager
        self.PrinterId = pluginId
        self.ApiKey = apiKey

        # The current websocket connection and Id
        self.ConId = 0
        self.BackoffCounter = 0
        self.Ws = None

        # Indicates if the connection is connection and authed.
        self.IsConnected = False


    # Updates the API key if we get a new one from the server.
    def UpdateApiKey(self, apiKey:str) -> None:
        self.ApiKey = apiKey


    # Starts the connection thread.
    def Start(self) -> None:
        t = threading.Thread(target=self._ConnectionThread)
        t.daemon = True
        t.start()


    # Closes the connection if it's open.
    def Close(self) -> None:
        ws = self.Ws
        if ws is not None:
            ws.Close()


    # Sends a message using fabric.
    # Returns True on success.
    def SendMsg(self, data:bytearray, dataStartOffsetBytes:int, dataLength:int) -> bool:
        # Check the connection state.
        if self.IsConnected is False:
            self.Logger.error(f"{self._getLogTag()} message tired to be sent while we weren't authed.")
            return False
        # Capture and check the websocket.
        ws = self.Ws
        if ws is None:
            self.Logger.error(f"{self._getLogTag()} message tired to be sent while we weren't connected.")
            return False

        try:
            ws.Send(data, dataStartOffsetBytes, dataLength)
            return True
        except Exception as e:
            Sentry.Exception("Fabric SendMsg exception.", e)
        return False


    # Called when the websocket is up and authed.
    def _OnConnected(self, ws:Client) -> None:
        self.Logger.info(f"{self._getLogTag()} Successfully authed and connected!")
        self.IsConnected = True


    # Runs the main connection loop.
    def _ConnectionThread(self):
        while True:

            # Reset the state vars
            self.IsConnected = False
            self.Ws = None
            self.FiberManager.OnSocketReset()

            # If this isn't the first connection, sleep a bit before trying again.
            if self.ConId != 0:
                self.BackoffCounter += 1
                self.BackoffCounter = min(self.BackoffCounter, 12)
                self.Logger.error(f"{self._getLogTag()} sleeping before trying the Sage Fabric connection again.")
                time.sleep(5 * self.BackoffCounter)
            self.ConId += 1

            try:
                # Called when the websocket is closed.
                def Closed(ws:Client):
                    self.Logger.info(f"{self._getLogTag()} Websocket closed")

                # Start the web socket connection.
                #uri = "ws://10.0.0.15/sage-fabric-websocket"
                uri = "wss://homeway.io/sage-fabric-websocket"
                headers = {}
                headers["X-Plugin-Id"] = self.PrinterId
                headers["X-Api-Key"] = self.ApiKey
                self.Logger.info(f"{self._getLogTag()} Starting fabric connection to [{uri}]")
                self.Ws = Client(uri, onWsOpen=self._OnConnected, onWsData=self._OnData, onWsClose=Closed, headers=headers)

                # Run until success or failure.
                self.Ws.RunUntilClosed()

                self.Logger.info(f"{self._getLogTag()} Loop restarting.")

            except Exception as e:
                Sentry.Exception("Sage Fabric ConnectionThread exception.", e)


    def _OnData(self, ws:Client, buffer:bytes, msgType):
        try:
            # This should always be a binary message.
            if msgType is not octowebsocket.ABNF.OPCODE_BINARY:
                raise Exception(f"{self._getLogTag()} Received non-binary websocket message received.")

            # Let the fiber manager handle the incoming message.
            self.FiberManager.OnIncomingMessage(buffer)

        except Exception as e:
            Sentry.Exception("Sage Fiber _OnData exception.", e)
            self.Close()


    def _getLogTag(self) -> str:
        return f"Sage Fabric [{self.ConId}]"
