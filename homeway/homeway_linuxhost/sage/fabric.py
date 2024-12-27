import time
import json
import logging
import threading
import octoflatbuffers

from homeway.sentry import Sentry
from homeway.websocketimpl import Client

from homeway.Proto import SageFiber


# Connects to Home Assistant and manages the connection.
class Fabric:

    # For debugging, it's too chatty to enable always.
    c_LogWsMessages = False

    def __init__(self, logger:logging.Logger, pluginId:str, apiKey:str) -> None:
        self.Logger = logger
        #self.EventHandler = eventHandler
        self.HaVersionString = None

        # The current websocket connection and Id
        self.ConId = 0
        self.BackoffCounter = 0
        self.Ws = None

        # We need to send a message id with each message.
        self.MsgIdLock = threading.Lock()
        self.MsgId = 1

        # Indicates if the connection is connection and authed.
        self.IsConnected = False

        # If set, when the websocket is connected, we should send the HA restart command.
        self.IssueRestartOnConnect = False

        self.PendingContext = None


    def Start(self) -> None:
        t = threading.Thread(target=self._ConnectionThread)
        t.daemon = True
        t.start()


    # Called when the websocket is up and authed.
    def _OnConnected(self) -> None:
        self.Logger.info(f"{self._getLogTag()} Successfully authed and connected!")
        self.IsConnected = True



    # Runs the main connection we maintain with Home Assistant.
    def _ConnectionThread(self):
        while True:

            # Reset the state vars
            self.IsConnected = False
            self.Ws = None
            self.MsgId = 1

            # If this isn't the first connection, sleep a bit before trying again.
            if self.ConId != 0:
                self.BackoffCounter += 1
                self.BackoffCounter = min(self.BackoffCounter, 12)
                self.Logger.error(f"{self._getLogTag()} sleeping before trying the HA connection again.")
                time.sleep(5 * self.BackoffCounter)
            self.ConId += 1

            try:

                # Setup our handlers.

                # This is called when the socket is opened.
                def Opened(ws:Client):
                    self.Logger.info(f"{self._getLogTag()} Websocket opened")

                # Called when the websocket is closed.
                def Closed(ws:Client):
                    self.Logger.info(f"{self._getLogTag()} Websocket closed")

                # Start the web socket connection.
                # If we got auth from the env var, we running in the add on and use this address.
                uri = "wss://homeway.io/sage-fabric-websocket"
                self.Logger.info(f"{self._getLogTag()} Starting connection to [{uri}]")
                self.Ws = Client(uri, onWsOpen=Opened, onWsData=self._OnData, onWsClose=Closed)

                # Run until success or failure.
                self.Ws.RunUntilClosed()

                self.Logger.info(f"{self._getLogTag()} Loop restarting.")

            except Exception as e:
                Sentry.Exception("ConnectionThread exception.", e)


    def _OnData(self, ws:Client, buffer:bytes, msgType):
        try:
            # Parse the message
            # sageFiber = SageFiber.SageFiber()
            # sageFiber.Init(buffer, 0)
            # text = sageFiber.Text()

            if self.PendingContext is not None:
                self.PendingContext.Result = buffer.decode()
                self.PendingContext.Event.set()


            # jsonStr = buffer.decode()
            # jsonObj = json.loads(jsonStr)
            # if self.Logger.isEnabledFor(logging.DEBUG) and Connection.c_LogWsMessages:
            #     jsonFormatted = json.dumps(jsonObj, indent=2)
            #     self.Logger.debug(f"{self._getLogTag()} WS Message \r\n{jsonFormatted}\r\n")

        except Exception as e:
            Sentry.Exception("ConnectionThread exception.", e)
            self.Close()


    def Listen(self, audio:bytes) -> str:

        try:
            # builder = octoflatbuffers.Builder(len(audio) + 500)

            # audioOffset = builder.CreateByteVector(audio)

            # SageFiber.Start(builder)
            # SageFiber.AddData(builder, audioOffset)
            # streamMsgOffset = SageFiber.End(builder)
            # SageFiber.fin

            # # Finalize the message. We use the size prefixed
            # builder.FinishSizePrefixed(streamMsgOffset)
            # builder.Output()

            # Instead of using Output, which will create a copy of the buffer that's trimmed, we return the fully built buffer
            # with the header offset set and size. Flatbuffers are built backwards, so there's usually space in the front were we can add data
            # without creating a new buffer!
            # Note that the buffer is a bytearray
            # buffer = builder.Bytes
            # msgStartOffsetBytes = builder.Head()
            # msgSize = len(buffer) - msgStartOffsetBytes
            #return builder.Output()
            self.Ws.Send(audio, 0, len(audio))

            self.PendingContext = Context()
            self.PendingContext.Event.wait(5)
            text = self.PendingContext.Result
            self.PendingContext = None
            return text
        except Exception as e:
            self.Logger.error(str(e))
        return ""


    # def SendMsg(self, msg:dict, ignoreConnectionState:bool = False) -> bool:
    #     # Check the connection state.
    #     if ignoreConnectionState is False:
    #         if self.IsConnected is False:
    #             self.Logger.error(f"{self._getLogTag()} message tired to be sent while we weren't authed.")
    #             return False

    #     # Capture and check the websocket.
    #     ws = self.Ws
    #     if ws is None:
    #         self.Logger.error(f"{self._getLogTag()} message tired to be sent while we weren't connected.")
    #         return False

    #     try:
    #         # Add the id field to all messages that are post auth.
    #         if self.IsConnected:
    #             with self.MsgIdLock:
    #                 msg["id"] = self.MsgId
    #                 self.MsgId += 1

    #         # Dump the message
    #         jsonStr = json.dumps(msg)
    #         if self.Logger.isEnabledFor(logging.DEBUG) and Connection.c_LogWsMessages:
    #             self.Logger.debug(f"{self._getLogTag()} Sending Ws Message {jsonStr}")

    #         # Since we must encode the data, which will create a copy, we might as well just send the buffer as normal,
    #         # without adding the extra space for the header. We can add the header here or in the WS lib, it's the same amount of work.
    #         ws.Send(jsonStr.encode(), isData=False)
    #         return True
    #     except Exception as e:
    #         Sentry.Exception("SendMsg exception.", e)
    #     return False


    # Closes the connection if it's open.
    def Close(self) -> None:
        ws = self.Ws
        if ws is not None:
            ws.Close()


    def _getLogTag(self) -> str:
        return f"HaCon [{self.ConId}]"


class Context:
    def __init__(self):
        self.Event = threading.Event()
        self.Result = None
