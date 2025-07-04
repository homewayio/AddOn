import time
import json
import logging
import threading

from homeway.sentry import Sentry
from homeway.websocketimpl import Client

from .eventhandler import EventHandler
from .serverinfo import ServerInfo


# Connects to Home Assistant and manages the connection.
class Connection:

    # For debugging, it's too chatty to enable always.
    c_LogWsMessages = False

    def __init__(self, logger:logging.Logger, eventHandler:EventHandler) -> None:
        self.Logger = logger
        self.EventHandler = eventHandler
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

        # Allows for blocking message send responses.
        self.PendingContextsLock = threading.Lock()
        self.PendingContexts = {}

        # If set, we call this back when the WS is connected and authed.
        self.HomeContextOnConnectedCallback = None


    def Start(self) -> None:
        t = threading.Thread(target=self.ConnectionThread)
        t.daemon = True
        t.start()


    # Issues the restart command to Home Assistant.
    def RestartHa(self) -> None:
        if self.IsConnected:
            self.IssueRestartOnConnect = False
            self.Logger.error(f"{self._getLogTag()} Sending HA restart command.")
            self.SendMsg({"type": "call_service", "domain": "homeassistant", "service": "restart", "service_data": {}})
        else:
            self.Logger.error(f"{self._getLogTag()} HA restart command deferred, since we aren't connected.")
            self.IssueRestartOnConnect = True


    # Sets the callback to be fired when the WS is connected.
    def SetHomeContextOnConnectedCallback(self, callback) -> None:
        self.HomeContextOnConnectedCallback = callback


    # Called when the websocket is up and authed.
    def _OnConnected(self) -> None:
        self.Logger.info(f"{self._getLogTag()} Successfully authed and connected!")

        # If we need to restart HA, do it now.
        if self.IssueRestartOnConnect:
            # Since this will kill the websocket there's nothing else to do.
            self.RestartHa()
            return

        # We need to subscribe to events, so we can fire the required assistant callbacks.
        # TODO - For now this subs us to everything. We can also be selective of which types we want, which we could
        # explore in the future.
        if self.SendMsg({"type":"subscribe_events"}) is False:
            self.Logger.error(f"{self._getLogTag()} failed to send event subscribe call.")

        # If we have a callback, call it.
        callback = self.HomeContextOnConnectedCallback
        if callback is not None:
            callback()


    # Runs the main connection we maintain with Home Assistant.
    def ConnectionThread(self):
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
                # First, we need to get the access token. If we are running in the addon, we should be able to pull it from the env var,
                # since we use the flag in our addon config.
                accessToken = ServerInfo.GetAccessToken()
                if accessToken is None or len(accessToken) == 0:
                    # We need an access token, so we can't do anything.
                    self.Logger.error(f"{self._getLogTag()} no access token, thus we can't connect.")
                    time.sleep(120)
                    continue

                # If we are here, we have an access token!
                # Setup our handlers.

                # This is called when the socket is opened.
                def Opened(ws:Client):
                    self.Logger.info(f"{self._getLogTag()} Websocket opened")

                # Called when the websocket is closed.
                def Closed(ws:Client):
                    self.Logger.info(f"{self._getLogTag()} Websocket closed")

                # Start the web socket connection.
                # If we got auth from the env var, we running in the add on and use this address.
                uri = f"{(ServerInfo.GetServerBaseUrl('ws'))}/api/websocket"
                self.Logger.info(f"{self._getLogTag()} Starting connection to [{uri}]")
                self.Ws = Client(uri, onWsOpen=Opened, onWsData=self._OnData, onWsClose=Closed)

                # It's important that we disable cert checks since the server might have a self signed cert or cert for a hostname that we aren't using.
                # This is safe to do, since the connection will be localhost or on the local LAN
                self.Ws.SetDisableCertCheck(True)

                # Run until success or failure.
                self.Ws.RunUntilClosed()

                self.Logger.info(f"{self._getLogTag()} Loop restarting.")

            except Exception as e:
                Sentry.Exception("ConnectionThread exception.", e)


    def _OnData(self, ws:Client, buffer:bytes, msgType):
        try:
            jsonStr = buffer.decode()
            jsonObj = json.loads(jsonStr)
            if self.Logger.isEnabledFor(logging.DEBUG) and Connection.c_LogWsMessages:
                jsonFormatted = json.dumps(jsonObj, indent=2)
                self.Logger.debug(f"{self._getLogTag()} WS Message \r\n{jsonFormatted}\r\n")

            # Before we do anything, make sure we are authed.
            if self.IsConnected is False:
                # Check if this is the auth response.
                if "type" in jsonObj and jsonObj["type"] == "auth_ok":
                    # Auth success!
                    self.IsConnected = True
                    self.BackoffCounter = 0
                    self._OnConnected()
                    return
                # Check for an auth failed response.
                if "type" in jsonObj and jsonObj["type"] == "auth_invalid":
                    msg = "unknown"
                    if "message" in jsonObj:
                        msg = jsonObj["message"]
                    self.Logger.error(f"{self._getLogTag()} Auth failed! Message: {msg}")
                    # Home assistant will close the ws, but we will do it as well.
                    self.Close()
                    return

                # Otherwise, this should be very first message, which is the auth require message.
                # The version should be in the auth_required message, if so, print it.
                if "ha_version" in jsonObj:
                    self.HaVersionString = jsonObj["ha_version"]
                    self.Logger.info(f"{self._getLogTag()} HA version {(self.HaVersionString)}")
                if "type" not in jsonObj or jsonObj["type"] != "auth_required":
                    self.Logger.warn(f"{self._getLogTag()} we aren't authed, we are expecting auth_required but didn't get it.")
                # Return the auth message
                # https://developers.home-assistant.io/docs/api/websocket/
                self.SendMsg({"type":"auth", "access_token": ServerInfo.GetAccessToken()}, ignoreConnectionState=True)
                return

            # For now, if there are any errors, we always log them.
            if "success" in jsonObj and jsonObj["success"] is not True:
                self.Logger.error(f"{self._getLogTag()} HA returned an error. {jsonStr}")

            msgType = jsonObj.get("type", None)
            if msgType is None:
                self.Logger.error(f"{self._getLogTag()} message without a type field! {json.dumps(jsonObj, indent=2)}")
                return

            if msgType == "result":
                # Check if there's a pending context for this message ID.
                msgId = jsonObj.get("id", None)
                if msgId is None:
                    self.Logger.error(f"{self._getLogTag()} result message without an id field!")
                else:
                    # Check if there's a pending context for this message.
                    # It's ok if there's no pending context, since we might have sent a message that we don't care about the response.
                    with self.PendingContextsLock:
                        if msgId in self.PendingContexts:
                            # If we find a mach, set the response and signal the event.
                            pendingContext = self.PendingContexts[msgId]
                            pendingContext.Response = jsonObj
                            pendingContext.Event.set()
                            # Return since this message is being handled by the pending context.
                            return

            # Finally, if the message is a event, invoke the handler.
            elif msgType == "event":
                try:
                    event = jsonObj["event"]
                    self.EventHandler.OnEvent(event, self.HaVersionString)
                except Exception as e:
                    Sentry.Exception("HA Event Handler threw an exception.", e)

        except Exception as e:
            Sentry.Exception("ConnectionThread exception.", e)
            self.Close()


    # Sends a message to Home Assistant.
    # If waitForResponse is True, either the response dict will be returned or False if the message failed or timeout.
    # If waitForResponse is False, True will be returned if the message was sent, False if it failed.
    def SendMsg(self, msg:dict, waitForResponse:bool = False, ignoreConnectionState:bool = False) -> bool:
        # Check the connection state.
        if ignoreConnectionState is False:
            if self.IsConnected is False:
                self.Logger.error(f"{self._getLogTag()} message tried to be sent while we weren't authed.")
                return False
        # Capture and check the websocket.
        ws = self.Ws
        if ws is None:
            self.Logger.error(f"{self._getLogTag()} message tried to be sent while we weren't connected.")
            return False

        msgId = 0
        pendingContext = None
        try:
            # Add the id field to all messages that are post auth.
            if self.IsConnected:
                with self.MsgIdLock:
                    msgId = self.MsgId
                    msg["id"] = msgId
                    self.MsgId += 1

            # Create a pending context
            if waitForResponse:
                pendingContext = PendingContexts()
                with self.PendingContextsLock:
                    self.PendingContexts[msgId] = pendingContext

            # Dump the message
            jsonStr = json.dumps(msg)
            if self.Logger.isEnabledFor(logging.DEBUG) and Connection.c_LogWsMessages:
                self.Logger.debug(f"{self._getLogTag()} Sending Ws Message {jsonStr}")

            # Since we must encode the data, which will create a copy, we might as well just send the buffer as normal,
            # without adding the extra space for the header. We can add the header here or in the WS lib, it's the same amount of work.
            ws.Send(jsonStr.encode(), isData=False)

            # If we aren't waiting for a response, we are done.
            if pendingContext is None:
                return True

            # Wait for the response.
            if pendingContext.Event.wait(10.0) is False:
                # Timeout, return false.
                return False

            # We got the response, return it.
            return pendingContext.Response

            # Wait for the response.
        except Exception as e:
            Sentry.Exception("SendMsg exception.", e)
        finally:
            # If we have a pending context, make sure to remove it.
            if pendingContext is not None:
                with self.PendingContextsLock:
                    del self.PendingContexts[msgId]
        return False


    # Closes the connection if it's open.
    def Close(self) -> None:
        ws = self.Ws
        if ws is not None:
            ws.Close()


    def _getLogTag(self) -> str:
        return f"HaCon [{self.ConId}]"


# Holds a pending context for an outstanding sent message.
class PendingContexts:

    def __init__(self):
        self.Event = threading.Event()
        self.Response:dict = None
