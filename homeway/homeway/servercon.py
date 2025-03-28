import time
import random
import logging
from datetime import datetime

from .Proto.AddonTypes import AddonTypes

from .sentry import Sentry
from .websocketimpl import Client
from .session import Session
from .repeattimer import RepeatTimer
from .pingpong import PingPong
from .hostcommon import HostCommon
from .threaddebug import ThreadDebug

#
# This class is responsible for connecting and maintaining a connection to a server.
# This includes making sure all of the websocket connections and making sure they are cleaned up
# Handling disconnects, errors, backoff, and retry logic.
# Handling RunFor logic which limits how long a server connection stays active.
#
class ServerCon:

    # The RunFor system allows the host to specify how long this server connection should be active.
    # This time includes all valid connections and disconnects. Simply put, after x amount of time, the class
    # should be cleaned up and RunBlocking will return.
    #
    # This functionally is used to occasionally make the plugin refresh it's primary server, because over time the
    # best server connection might change. When the server reconnects it will resolve plugin connect hostname again
    # which will route it to the best server.
    # This feature is also used for secondary connections, which allows the plugin to connect ot multiple servers at once.
    # Secondary server connections are used when a shared connection url resolves to a different server than we are currently connected.
    #
    # The run for system accounts for user activity, and will allow extra time after the run for time if the user is still using the
    # connection.

    # How frequency we check if RunFor is done.
    RunForTimeCheckerIntervalSec = 60 * 2 # 2 minutes

    # The min amount of time from the last user activity RunFor will wait before disconnecting.
    RunForMinTimeSinceLastUserActivitySec = 60 * 5 # 5 minutes.

    # The max amount of time beyond the RunFor limit we will wait for user activity to stop.
    RunForMaxUserActivityWaitTimeSec = 60 * 60 * 2 # 2 hours.


    # Must be > 0 or the increment logic will fail (since it's value X 2)
    # We want to keep this low though, so incase the connection closes due to a Stream error,
    # we will reconnect quickly again. Remember we always add the random reconnect time as well.
    WsConnectBackOffSec_Default = 1
    # We always add a random second count to the reconnect sleep to add variance. This is the min value.
    # Remember the server takes about 5 seconds to reboot, so connecting before then is useless.
    WsConnectRandomMinSec = 10
    # We always add a random second count to the reconnect sleep to add variance. This is the max value.
    # Having a wider window allows the client to reconnect at different times, which is good for the server.
    WsConnectRandomMaxSec = 30

    def __init__(self, host, endpoint:str, isPrimaryConnection:bool, shouldUseLowestLatencyServer:bool, pluginId:str, privateKey:str,
                  logger:logging.Logger, statusChangeHandler, pluginVersion:str, runForSeconds, summonMethod, addonType:AddonTypes):
        self.ProtocolVersion = 1
        self.Session = None
        self.IsDisconnecting = False
        self.IsWsConnecting = False
        self.ActiveSessionId = 0
        self.Ws = None
        self.WsConnectBackOffSec = self.WsConnectBackOffSec_Default
        self.NoWaitReconnect = False

        self.Host = host
        self.Logger = logger
        self.IsPrimaryConnection = isPrimaryConnection
        self.PluginId = pluginId
        self.PrivateKey = privateKey
        self.PluginVersion = pluginVersion
        self.SummonMethod = summonMethod
        self.AddonType = addonType

        self.DefaultEndpoint = endpoint
        self.CurrentEndpoint = self.DefaultEndpoint
        self.ShouldUseLowestLatencyServer = shouldUseLowestLatencyServer
        self.TempDisableLowestLatencyEndpoint = False

        # Check that the settings are valid..
        if self.ShouldUseLowestLatencyServer and self.IsPrimaryConnection is False:
            self.Logger.Error("Non primary ServerCon cannot use ShouldUseLowestLatencyServer, since it might not connect to where it was requested.")

        # If this is the primary connection, register for the latency data complete callback.
        # This callback wil only fire on the very first time the plugin is ran.
        if self.IsPrimaryConnection:
            PingPong.Get().RegisterPluginFirstRunLatencyCompleteCallback(self.OnFirstRunLatencyDataComplete)

        # Note! Will be None for secondary connections!
        self.StatusChangeHandler = statusChangeHandler

        # Setup RunFor
        self.RunForSeconds = runForSeconds
        self.CreationTime = datetime.now()
        self.LastUserActivityTime = self.CreationTime

        # Start the RunFor time checker.
        self.RunForTimeChecker = RepeatTimer(self.Logger, self.RunForTimeCheckerIntervalSec, self.OnRunForTimerCallback)
        self.RunForTimeChecker.start()


    def Cleanup(self):
        # Stop the RunFor time checker if we have one.
        if self.RunForTimeChecker is not None:
            self.RunForTimeChecker.Stop()


    # Returns a printable string that says the endpoint and the active session id.
    def GetConnectionString(self):
        # Use the currently in use endpoint.
        return str(self.CurrentEndpoint)+"["+str(self.ActiveSessionId)+"]"


    # Returns the endpoint to use, be it the default or the lowest latency.
    def GetEndpoint(self):
        newEndpoint = None

        # Check if we can use the lowest latency server.
        if self.ShouldUseLowestLatencyServer and self.IsPrimaryConnection:
            # Only try to use the lowest latency option if we aren't in a temp block from it.
            # This will happen if we tried to connect to the lowest latency server and failed.
            if self.TempDisableLowestLatencyEndpoint is False:
                # Check if we have a known lowest latency server.
                lowestLatencySub = PingPong.Get().GetLowestLatencyServerSub()
                if lowestLatencySub is not None:
                    newEndpoint = HostCommon.GetPluginConnectionUrl(subdomain=lowestLatencySub)
                    self.Logger.info("Attempting to use lowest latency server: "+newEndpoint)

        # Otherwise use the default endpoint.
        if newEndpoint is None:
            newEndpoint = self.DefaultEndpoint

        self.CurrentEndpoint = newEndpoint
        return self.CurrentEndpoint


    def OnOpened(self, ws):
        self.Logger.info("Connected To Homeway, server con "+self.GetConnectionString()+". Starting handshake...")

        # On success make the lowest latency endpoint possible again, since we successfully connected to it or the primary.
        # And we note that we have connected.
        self.IsWsConnecting = False
        self.TempDisableLowestLatencyEndpoint = False

        # Also after the open call has been successful, ensure the disconnecting flag is cleared.
        # This ensures any races between the disconnect function and a new connection won't result in the
        # flag getting stuck to being set.
        self.IsDisconnecting = False

        # Create a new session for this websocket connection.
        self.Session = Session(self, self.Logger, self.PluginId, self.PrivateKey, self.IsPrimaryConnection, self.ActiveSessionId, self.PluginVersion)
        self.Session.StartHandshake(self.SummonMethod, self.AddonType)


    def OnClosed(self, ws):
        self.Logger.info("Service websocket closed.")


    def OnError(self, ws, err):
        # If this error happened while we were connecting, set the TempDisableLowestLatencyEndpoint to true to block the lowest latency endpoint.
        # This is because the host might not be available temporally, so we will use the default.
        if self.IsWsConnecting:
            self.TempDisableLowestLatencyEndpoint = True
            self.Logger.info("Blocking lowest latency endpoint, since we failed while the WS connect was happening.")
        self.Logger.error("Homeway Ws error: " +str(err))


    def OnMsg(self, ws, msg):
        # When we get any message, consider it user activity.
        self.LastUserActivityTime = datetime.now()

        if self.Session:
            # Grab the session id now, since it can change by the time this call is done.
            # For example, if this call creates an error that ends up shutting down the ws.
            localSessionId = self.ActiveSessionId
            try:
                self.Session.HandleMessage(msg)
            except Exception as e:
                Sentry.Exception("Exception in Session.HandleMessage " + self.GetConnectionString() + ".", e)
                self.OnSessionError(localSessionId, 0)


    def OnHandshakeComplete(self, sessionId, apiKey, connectedAccounts):
        if sessionId != self.ActiveSessionId:
            self.Logger.info("Got a handshake complete for an old session, "+str(sessionId)+", ignoring.")
            return

        self.Logger.info("Handshake complete, server con "+self.GetConnectionString()+", successfully connected to Homeway!")

        # Only primary connections have this handler.
        if self.StatusChangeHandler is not None:
            self.StatusChangeHandler.OnPrimaryConnectionEstablished(apiKey, connectedAccounts)

        # Only set the back off when we are done with the handshake and it was successful.
        self.WsConnectBackOffSec = self.WsConnectBackOffSec_Default


    # Called by the session if we should kill this socket.
    def OnSessionError(self, sessionId, backoffModifierSec):
        if sessionId != self.ActiveSessionId:
            self.Logger.info("Got a session error callback for an old session, "+str(sessionId)+", ignoring.")
            return

        # If a back off modifier is supplied, we should add it to the current backoff.
        # This is driven by the service when it asks us to back off in our connection time.
        if backoffModifierSec > 0:
            self.WsConnectBackOffSec += backoffModifierSec

        self.Logger.error("Session reported an error ["+self.GetConnectionString()+"], closing the websocket. Backoff time sec: " + str(self.WsConnectBackOffSec))

        # Shut things down
        self.Disconnect()


    # Called by the server con if the plugin needs to be updated. The backoff time will be set very high
    # and this notification will be handled by the UI to show the user a message.
    def OnPluginUpdateRequired(self):
        # This will be null for secondary connections
        if self.StatusChangeHandler is not None:
            self.StatusChangeHandler.OnPluginUpdateRequired()


    # A summon request can be sent by the services if the user is connected to a different
    # server than we are connected to. In such a case we will multi connect a temp non-primary connection
    # to the request server as well, that will be to service the user.
    def OnSummonRequest(self, sessionId, summonConnectUrl, summonMethod):
        self.Host.OnSummonRequest(summonConnectUrl, summonMethod)


    def Disconnect(self):
        # Only close the Stream once, even though we might get multiple calls.
        # This can happen because disconnecting might case proxy socket errors, for example
        # if we closed all of the sockets locally and then the server tries to close one.
        if self.IsDisconnecting is False:
            self.IsDisconnecting = True
            # Try to close all of the sockets before we disconnect, so we send the messages.
            # It's important to try catch this logic to ensure we always end up calling close on the current websocket.
            try:
                if self.Session:
                    self.Session.CloseAllWebStreamsAndDisable()
            except Exception as e:
                Sentry.Exception("Exception when calling CloseAllWebStreamsAndDisable from Disconnect.", e)
        else:
            self.Logger.info("ServerCon Disconnect was called, but we are skipping the CloseAllWebStreamsAndDisable because it has already been done.")

        # On every disconnect call, try to disconnect the websocket. We do this because we have seen that for some reason calling Close doesn't seem
        # to always actually cause the websocket to close and cause RunUntilClosed to return. Thus we hope if we keep trying to close it, maybe it will.
        # We have traced this bug down in to the WS lib, so it's kind of out of our control.
        # What we want is for this Close() to cause the websocket to disconnect, which will make RunUntilClosed return and then the connection loop will handle
        # the reconnect.
        ws = self.Ws
        self.Logger.info("ServerCon websocket close start. IsPrimary?:"+str(self.IsPrimaryConnection) + "; wsId:"+self.GetWsId(ws))
        if ws:
            ws.Close()
        self.Logger.info("ServerCon disconnect complete.")


    # Returns if the RunFor time has expired, including considering user activity.
    def IsRunForTimeComplete(self):
        # Check if we are past our RunFor time.
        hasRanFor = datetime.now() - self.CreationTime
        if hasRanFor.total_seconds() > self.RunForSeconds:
            # Check the last user activity.
            timeSinceUserActivity = datetime.now() - self.LastUserActivityTime
            if timeSinceUserActivity.total_seconds() > self.RunForMinTimeSinceLastUserActivitySec:
                # We have passed the RunFor time and the min amount of time since the last user activity.
                self.Logger.info("Server con "+self.GetConnectionString()+" IS past it's RunFor time "+str(hasRanFor)+" and IS past it's time since last user activity "+str(timeSinceUserActivity))
                return True
            else:
                # Check how long we have been waiting on user activity.
                timeSinceRunForShouldHaveEnded = hasRanFor.total_seconds() - self.RunForSeconds
                if timeSinceRunForShouldHaveEnded > self.RunForMaxUserActivityWaitTimeSec:
                    self.Logger.info("Server con "+self.GetConnectionString()+" IS past it's RunFor time "+str(hasRanFor)+", but IS NOT past it's time since last user activity "+str(timeSinceUserActivity) + " BUT we have exceeded the max user activity time.")
                    return True
                self.Logger.info("Server con "+self.GetConnectionString()+" IS past it's RunFor time "+str(hasRanFor)+", but IS NOT past it's time since last user activity "+str(timeSinceUserActivity))
        return False


    # Fires at a regular interval to see if we should disconnect this server connection.
    def OnRunForTimerCallback(self):
        if self.IsRunForTimeComplete():
            try:
                self.Logger.info("Server con "+self.GetConnectionString()+" RunFor is complete and will be disconnected.")
                self.Disconnect()
                ThreadDebug.DoThreadDumpLogout(self.Logger)
            except Exception as e:
                Sentry.Exception("Exception in OnRunForTimerCallback during disconnect. "+self.GetConnectionString()+".", e)


    # A callback fired only for the primary connection and only when the first latency data is ready after the plugin's first run.
    # Since our first Stream connection won't have latency data to choose the best server, it will always default. This function makes it
    # possible for us to switch to the best latency server in that once special case.
    def OnFirstRunLatencyDataComplete(self):
        try:
            self.Logger.info("First run latency callback fired, disconnecting primary Stream to reconnect to most ideal latency server. Current: "+self.GetConnectionString()+".")
            self.NoWaitReconnect = True
            self.Disconnect()
        except Exception as e:
            Sentry.Exception("Exception in OnFirstRunLatencyDataComplete during disconnect. "+self.GetConnectionString()+".", e)


    def RunBlocking(self):
        while 1:
            # Since we want to run forever, we want to make sure any exceptions get caught but then we try again.
            try:
                # Clear the disconnecting flag.
                # We do this just before connects, because this flag weeds out all of the error noise
                # that might happen while we are performing a disconnect. But at this time, all of that should be
                # 100% done now.
                self.IsDisconnecting = False

                # Set the connecting flag, so we know if we are in the middle of a ws connect.
                # This is set to false when the websocket is established.
                self.IsWsConnecting = True

                # Since there can be old pending actions from old sessions (session == one websocket connection).
                # We will keep track of the current session, so old errors from sessions don't effect the new one.
                self.ActiveSessionId += 1

                # Get the new endpoint. This will either be the default endpoint or the lowest latency endpoint.
                endpoint = self.GetEndpoint()

                # Connect to the service.
                self.Ws = Client(endpoint, onWsOpen=self.OnOpened, onWsMsg=self.OnMsg, onWsClose=self.OnClosed, onWsError=self.OnError)
                with self.Ws:
                    self.Logger.info("Attempting to talk to Homeway, server con "+self.GetConnectionString() + " wsId:"+self.GetWsId(self.Ws))
                    self.Ws.RunUntilClosed()

                # Handle disconnects
                self.Logger.info("Disconnected from Homeway, server con "+self.GetConnectionString())

                # Ensure all proxy sockets are closed.
                if self.Session:
                    self.Session.CloseAllWebStreamsAndDisable()

            except Exception as e:
                self.TempDisableLowestLatencyEndpoint = True
                Sentry.Exception("Exception in Homeway's main RunBlocking function. server con:"+self.GetConnectionString()+".", e)
                time.sleep(20)

            # On each disconnect, check if the RunFor time is now done.
            if self.IsRunForTimeComplete():
                # If our run for time expired, cleanup and return.
                self.Cleanup()
                self.Logger.info("Server con "+self.GetConnectionString()+" RunFor is complete, disconnected, and exiting the main thread.")
                # Exit the main run blocking loop.
                return

            # We have a back off time, but always add some random noise as well so not all clients try to use the exact same time.
            # Note this applies to all reconnects, even for errors in the system and not server connection loss.
            self.WsConnectBackOffSec += random.randint(self.WsConnectRandomMinSec, self.WsConnectRandomMaxSec)

            # Don't sleep if we want to NoWaitReconnect
            if self.NoWaitReconnect:
                self.NoWaitReconnect = False
                self.Logger.info("Skipping reconnect delay due to instant reconnect request.")
            else:
                self.Logger.info("Sleeping for " + str(self.WsConnectBackOffSec) + " seconds before trying again.")
                time.sleep(self.WsConnectBackOffSec)

            # Increment the back off time.
            self.WsConnectBackOffSec *= 2
            if self.WsConnectBackOffSec > 180 :
                self.WsConnectBackOffSec = 180
                # If we have failed and are waiting over 3 minutes, we will return which will check the server
                # protocol again, since it might have changed.
                return


    def SendMsg(self, buffer:bytearray, msgStartOffsetBytes:int, msgSize:int):
        # When we send any message, consider it user activity.
        self.LastUserActivityTime = datetime.now()
        self.Ws.Send(buffer, msgStartOffsetBytes, msgSize, True)


    def GetWsId(self, ws):
        ws = self.Ws
        if ws is not None:
            return str(id(ws))
        return "UNKNOWN"
