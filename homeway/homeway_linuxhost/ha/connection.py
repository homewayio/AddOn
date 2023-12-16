import os
import time
import json
import logging
import threading

from homeway.sentry import Sentry
from homeway.websocketimpl import Client

from .eventhandler import EventHandler


# Connects to Home Assistant and manages the connection.
class Connection:

    # For debugging, it's too chatty to enable always.
    c_LogWsMessages = False

    def __init__(self, logger:logging.Logger, homeAssistantIp:str, homeAssistantPort:int, eventHandler:EventHandler, accessToken_canBeNone:str) -> None:
        self.Logger = logger
        self.HomeAssistantIp = homeAssistantIp
        self.HomeAssistantPort = homeAssistantPort
        self.EventHandler = eventHandler
        self.AccessToken_CanBeNone = accessToken_canBeNone
        self.SetAccessToken = None

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
                self.SetAccessToken = os.getenv('SUPERVISOR_TOKEN')
                isAddon = True
                if self.SetAccessToken is None or len(self.SetAccessToken) == 0:
                    # If we can't get it from the env, we might not be running in an addon. See if it was passed to the plugin.
                    isAddon = False
                    self.SetAccessToken = self.AccessToken_CanBeNone
                    if self.SetAccessToken is None or len(self.SetAccessToken) == 0:
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
                uri = "ws://supervisor/core/api/websocket"
                if isAddon is False:
                    # If not, we use the passed details.
                    uri = f"ws://{self.HomeAssistantIp}:{self.HomeAssistantPort}/api/websocket"
                self.Logger.info(f"{self._getLogTag()} Starting connection to [{uri}]")
                self.Ws = Client(uri, onWsOpen=Opened, onWsData=self._OnData, onWsClose=Closed)

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
                    self.Logger.info(f"{self._getLogTag()} HA version {(jsonObj['ha_version'])}")
                if "type" not in jsonObj or jsonObj["type"] != "auth_required":
                    self.Logger.warn(f"{self._getLogTag()} we aren't authed, we are expecting auth_required but didn't get it.")
                # Return the auth message
                # https://developers.home-assistant.io/docs/api/websocket/
                self.SendMsg({"type":"auth", "access_token":self.SetAccessToken}, ignoreConnectionState=True)
                return

            # For now, we check all returned messages for errors and report them.
            if "success" in jsonObj and jsonObj["success"] is not True:
                self.Logger.error(f"{self._getLogTag()} HA returned an error. {jsonStr}")

            # Finally, if the message is a event, invoke the handler.
            if "type" in jsonObj and jsonObj["type"] == "event":
                event = jsonObj["event"]
                self.EventHandler.OnEvent(event)

        except Exception as e:
            Sentry.Exception("ConnectionThread exception.", e)
            self.Close()


    def SendMsg(self, msg:dict, ignoreConnectionState:bool = False) -> bool:
        # Check the connection state.
        if ignoreConnectionState is False:
            if self.IsConnected is False:
                self.Logger.error(f"{self._getLogTag()} message tired to be sent while we weren't authed.")
                return False
        # Capture and check the websocket.
        ws = self.Ws
        if ws is None:
            self.Logger.error(f"{self._getLogTag()} message tired to be sent while we weren't connected.")
            return False

        try:
            # Add the id field to all messages that are post auth.
            if self.IsConnected:
                with self.MsgIdLock:
                    msg["id"] = self.MsgId
                    self.MsgId += 1

            # Dump the message
            jsonStr = json.dumps(msg)
            if self.Logger.isEnabledFor(logging.DEBUG) and Connection.c_LogWsMessages:
                self.Logger.debug(f"{self._getLogTag()} Sending Ws Message {jsonStr}")

            # Send.
            ws.Send(jsonStr.encode(), False)
            return True
        except Exception as e:
            Sentry.Exception("SendMsg exception.", e)
        return False


    # Closes the connection if it's open.
    def Close(self) -> None:
        ws = self.Ws
        if ws is not None:
            ws.Close()


    def _getLogTag(self) -> str:
        return f"HaCon [{self.ConId}]"
