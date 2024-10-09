# namespace: WebStream

import threading
import time
import logging

import octowebsocket

from .headerimpl import HeaderHelper
from ..sentry import Sentry
from ..httprequest import HttpRequest
from ..mdns import MDns
from ..streammsgbuilder import StreamMsgBuilder
from ..localip import LocalIpHelper
from ..websocketimpl import Client
from ..compression import Compression, CompressionContext
from ..Proto import WebStreamMsg
from ..Proto import MessageContext
from ..Proto import WebSocketDataTypes
from ..Proto import PathTypes
from ..Proto import DataCompression

#
# A helper object that handles websocket request for the web stream system.
#
# The helper can close the stream by calling close directly on the WebStream object
# or by returning true from `IncomingServerMessage`
#
class WebStreamWsHelper:

    # Called by the main socket thread so this should be quick!
    # Throwing from here will shutdown the entire connection.
    def __init__(self, streamId, logger:logging.Logger, webStream, webStreamOpenMsg, openedTime):
        self.Id = streamId
        self.Logger = logger
        self.WebStream = webStream
        self.WebStreamOpenMsg:WebStreamMsg.WebStreamMsg = webStreamOpenMsg
        self.IsClosed = False
        self.StateLock = threading.Lock()
        self.OpenedTime = openedTime
        self.Ws = None
        self.FirstWsMessageSentToLocal = False
        self.ResolvedLocalHostnameUrl = None
        self.CompressionContext = CompressionContext(self.Logger)

        # These vars indicate if the actual websocket is opened or closed.
        # This is different from IsClosed, which is tracking if the webstream closed status.
        # These are important for when we try to send a message.
        self.IsWsObjOpened = False
        self.IsWsObjClosed = False

        # Capture the initial http context
        self.HttpInitialContext = webStreamOpenMsg.HttpInitialContext()
        if self.HttpInitialContext is None:
            raise Exception("Web stream ws helper got a open message with no http context")

        # Parse the headers, filter them, and keep them locally.
        # This is required for some clients, since they need to send the X-API-Key header with the API key.
        self.Headers = HeaderHelper.GatherWebsocketRequestHeaders(self.Logger, self.HttpInitialContext)
        self.SubProtocolList = HeaderHelper.GetWebSocketSubProtocols(self.Logger, self.HttpInitialContext)

        # It might take multiple attempts depending on the network setup of the client.
        # This value keeps track of them.
        self.ConnectionAttempt = 0
        # This boolean tracks if a connection attempt was ever successful or not.
        self.SuccessfullyOpenedSocket = False

        # Attempt to connect to the websocket.
        if self.AttemptConnection() is False:
            raise Exception("Web stream ws AttemptConnection didn't try to connect?")


    # This function will attempt to connect to the desired websocket.
    # Due to different port binding or network setups, we might need to try a few different IP and PORT combinations
    # before we succeeded. For that reason, this function is called when the object is first created and also when there's a
    # websocket error. Each call will attempt a new connection until there are no more possibilities, and then the function will return False.
    #
    # Returns True if a new connection is being attempted.
    # Returns False if a new connection is not being attempted.
    def AttemptConnection(self):
        # If this webstream context has already opened a successful websocket connection to something,
        # never try to connect again.
        if self.SuccessfullyOpenedSocket is True:
            return False

        # If this is not the first attempt, make sure the websocket is closed (which it most likely is)
        if self.Ws is not None:
            try:
                # Since the current websocket has callback handlers attached, first grab a local copy and null
                # it out before calling close. This allows the callbacks to check if they are firing for the current
                # websocket or an old one.
                ws = self.Ws
                self.Ws = None
                ws.Close()
            except Exception as _:
                pass

        # Get the path
        path = StreamMsgBuilder.BytesToString(self.HttpInitialContext.Path())
        if path is None:
            raise Exception("Web stream ws helper got a open message with no path")

        # Depending on the connection attempt, build the URI
        uri = None
        pathType = self.HttpInitialContext.PathType()
        if pathType is PathTypes.PathTypes.Relative:
            # If the path is relative, we will make a few attempts to connect.
            # Note these attempts are very closely related to the logic in the HttpRequest class and should stay in sync.
            if self.ConnectionAttempt == 0:
                # Try to connect using the main URL, this is what we expect to work.
                protocol = "ws://"
                if HttpRequest.GetDirectServiceUseHttps():
                    protocol = "wss://"
                uri = protocol + str(HttpRequest.GetDirectServiceAddress()) + ":" + str(HttpRequest.GetDirectServicePort()) + path
            elif self.ConnectionAttempt == 1:
                # Attempt 2 is to where we think the http proxy port is.
                # For this address, we need set the protocol correctly depending if the client detected https or not.
                protocol = "ws://"
                if HttpRequest.GetLocalHttpProxyIsHttps():
                    protocol = "wss://"
                uri = protocol + str(HttpRequest.GetDirectServiceAddress()) + ":" +str(HttpRequest.GetLocalHttpProxyPort()) + path
            elif self.ConnectionAttempt == 2:
                # Attempt 3 will be to try to connect with the device IP.
                # This is needed if the server isn't bound to localhost, but only the public IP. Try the http proxy port.
                # Since we are using the public IP, it's more likely that the http proxy port will be bound and not fire walled, since the server port is usually internal only.
                protocol = "ws://"
                if HttpRequest.GetLocalHttpProxyIsHttps():
                    protocol = "wss://"
                uri = protocol + LocalIpHelper.TryToGetLocalIp() + ":" + str(HttpRequest.GetLocalHttpProxyPort()) + path
            elif self.ConnectionAttempt == 3:
                # Attempt 4 will be to try to connect with the device IP.
                # This is needed if the server isn't bound to localhost, but only the public IP.
                uri = "ws://" + LocalIpHelper.TryToGetLocalIp() + ":" + str(HttpRequest.GetDirectServicePort()) + path
            else:
                # Report the issue and return False to indicate we aren't trying to connect.
                self.Logger.info(self.getLogMsgPrefix()+" failed to connect to relative path and has nothing else to try.")
                return False
        elif pathType is PathTypes.PathTypes.Absolute:
            # If this is an absolute path, there are two options:
            #   1) If the path is a local hostname, we will try to manually resolve the hostname and then try that connection directly.
            #      This is to mitigate mDNS problems, which are described in HttpRequest, in the PathTypes.Absolute handling logic.
            #      Basically, mDNS is flakey and it's not supported on some OSes, so doing it ourselves fixes some of that.
            #        - If this is the case, we will try our manually resolved url first, and the OG second.
            #   2) If the url isn't a local hostname or it fails to resolve manually, we just use the absolute URL directly.

            # Try to see if this is a local hostname, if we don't already have a result.
            if self.ResolvedLocalHostnameUrl is None:
                # This returns None if the URL doesn't contain a local hostname or it fails to resolve.
                self.ResolvedLocalHostnameUrl = MDns.Get().TryToResolveIfLocalHostnameFound(path)

            if self.ResolvedLocalHostnameUrl is None:
                # If self.ResolvedLocalHostnameUrl is None, there's no local hostname or it failed to resolve.
                # Thus we will just try the absolute URL and nothing else.
                if self.ConnectionAttempt != 0:
                    # Report the issue and return False to indicate we aren't trying to connect.
                    self.Logger.info(self.getLogMsgPrefix()+" failed to connect to absolute path and has nothing else to try.")
                    return False

                # Use the raw absolute path.
                uri = path
            else:
                # We have a local hostname url resolved in our string.
                if self.ConnectionAttempt == 0:
                    # For the first attempt, try using our manually resolved URL. Since it's already resolved and might be from a cache, it's going to be faster.
                    uri = self.ResolvedLocalHostnameUrl
                elif self.ConnectionAttempt == 1:
                    # For the second attempt, it means the manually resolved URL failed, so we just try the original path with no modification.
                    self.Logger.info(self.getLogMsgPrefix()+" failed to connect with the locally resoled hostname ("+self.ResolvedLocalHostnameUrl+"), trying the raw URL. " + path)
                    uri = path
                else:
                    # We tired both, neither worked. Give up.
                    self.Logger.info(self.getLogMsgPrefix()+" failed to connect to manually resolved local hostname and the absolute path.")
                    return False
        else:
            raise Exception("Web stream ws helper got a open message with an unknown path type "+str(pathType))

        # Validate a URI was set
        if uri is None:
            raise Exception(self.getLogMsgPrefix()+" AttemptConnection failed to create a URI")

        # Increment the connection attempt.
        self.ConnectionAttempt += 1

        # Make the websocket object and start it running.
        self.Logger.debug(self.getLogMsgPrefix()+"opening websocket to "+str(uri) + " attempt "+ str(self.ConnectionAttempt))
        ws = Client(uri, self.onWsOpened, None, self.onWsData, self.onWsClosed, self.onWsError, headers=self.Headers, subProtocolList=self.SubProtocolList)
        # It's important that we disable cert checks since the server might have a self signed cert or cert for a hostname that we aren't using.
        # This is safe to do, since the connection will be localhost or on the local LAN
        ws.SetDisableCertCheck(True)

        # To ensure we never leak a websocket, we need to use this lock.
        # We need to check the is closed flag and then only set the ws if it's not closed.
        with self.StateLock:
            if self.IsClosed:
                # Cleanup and leave
                try:
                    ws.Close()
                except Exception:
                    pass
                return False

            # We aren't closed, set the websocket and run it.
            # We have to be careful with this ws, because it needs to be closed to fully shutdown, but we can't use a with statement.
            self.Ws = ws
            self.Ws.RunAsync()

        # Return true to indicate we are trying to connect again.
        return True


    # When close is called, all http operations should be shutdown.
    # Called by the main socket thread so this should be quick!
    def Close(self):
        # Don't try to close twice.
        wsToClose = None
        with self.StateLock:
            # If we are already closed, there's nothing to do.
            if self.IsClosed is True:
                return
            # We will close now, so set the flag.
            self.IsClosed = True

            # We use this lock to protect the websocket and make sure we never open when when we are closed or closing.
            # We must capture this in the same lock as self.IsClosed
            wsToClose = self.Ws

        self.Logger.info(self.getLogMsgPrefix()+"websocket closed after " +str(time.time() - self.OpenedTime) + " seconds")

        # The initial connection is created (or at least started) in the constructor, but there's re-attempt logic
        # that can cause the websocket to be destroyed and re-created. For that reason we need to grab a local ref
        # and make sure it's not null. If the close fails, just ignore it, since we are shutting down already.
        if wsToClose is not None:
            try:
                wsToClose.Close()
            except Exception as _ :
                pass

        # Ensure the compressor is cleaned up
        try:
            self.CompressionContext.__exit__(None, None, None)
        except Exception as e:
            Sentry.Exception("Websocket stream helper failed to clean up the compression context.", e)


    # Called when a new message has arrived for this stream from the server.
    # This function should throw on critical errors, that will reset the connection.
    # Returning true will case the websocket to close on return.
    # This function is called on it's own thread from the web stream, so it's ok to block
    # as long as it gets cleaned up when the socket closes.
    def IncomingServerMessage(self, webStreamMsg:WebStreamMsg.WebStreamMsg):

        # We can get messages from this web stream before the actual websocket has opened and is ready for messages.
        # If this happens, when we try to send the message on the socket and we will get an error saying "the socket is closed" (which is incorrect, it's not open yet).
        # So we need to delay until we know the socket is ready or the webstream is shutdown.
        while self.IsWsObjOpened is False:
            # Check if the webstream has closed or the socket object is now reporting closed.
            if self.IsWsObjClosed is True or self.IsClosed:
                return True

            # Sleep for a bit to wait for the socket open. The socket will open super quickly (5-10ms), so don't delay long.
            # Sleep for 5ms.
            time.sleep(0.005)

        # If the websocket object is closed ignore this message. It will throw if the socket is closed
        # which will take down the entire Stream. But since it's closed the web stream is already cleaning up.
        # This can happen if the socket closes locally and we sent the message to clean up to the service, but there
        # were already inbound messages on the way.
        if self.IsWsObjClosed:
            return True

        # Note it's ok for this to be empty. Since DataAsByteArray returns 0 if it doesn't
        # exist, we need to check for it.
        buffer = webStreamMsg.DataAsByteArray()
        if buffer == 0:
            buffer = bytearray(0)

        # If the message is compressed, decompress it.
        compressionType = webStreamMsg.DataCompression()
        if compressionType != DataCompression.DataCompression.None_:
            buffer = Compression.Get().Decompress(self.CompressionContext, buffer, webStreamMsg.OriginalDataSize(), False, compressionType)

        # Get the send type.
        sendType = 0
        msgType = webStreamMsg.WebsocketDataType()
        if msgType == WebSocketDataTypes.WebSocketDataTypes.Text:
            sendType = octowebsocket.ABNF.OPCODE_TEXT
        elif msgType == WebSocketDataTypes.WebSocketDataTypes.Binary:
            sendType = octowebsocket.ABNF.OPCODE_BINARY
        elif msgType == WebSocketDataTypes.WebSocketDataTypes.Close:
            sendType = octowebsocket.ABNF.OPCODE_CLOSE
        else:
            raise Exception("Web stream ws was sent a data type that's unknown. "+str(msgType))

        # Before we send, make sure we have a local websocket still and it's not closed.
        # If the websocket object is closed ignore this message. It will throw if the socket is closed
        # which will take down the entire Stream. But since it's closed the web stream is already cleaning up.
        # This can happen if the socket closes locally and we sent the message to clean up to the service, but there
        # were already inbound messages on the way.
        localWs = self.Ws
        if self.IsWsObjClosed or self.IsClosed or localWs is None:
            return True
        # Send using the known non-null local ws object.
        localWs.SendWithOptCode(buffer, optCode=sendType)

        # Log for perf tracking
        if self.FirstWsMessageSentToLocal is False:
            self.Logger.info(self.getLogMsgPrefix()+"first message sent to local server after " +str(time.time() - self.OpenedTime) + " seconds")
            self.FirstWsMessageSentToLocal = True

        # Always return false, to keep the socket alive.
        return False


    def onWsData(self, ws, buffer:bytes, msgType):
        # Only handle callbacks for the current websocket.
        if self.Ws is not None and self.Ws != ws:
            return

        try:
            # Figure out the data type
            # TODO - we should support the OPCODE_CONT type at some point. But it's not needed right now.
            sendType = WebSocketDataTypes.WebSocketDataTypes.None_
            if msgType == octowebsocket.ABNF.OPCODE_BINARY:
                sendType = WebSocketDataTypes.WebSocketDataTypes.Binary
            elif msgType == octowebsocket.ABNF.OPCODE_TEXT:
                sendType = WebSocketDataTypes.WebSocketDataTypes.Text
                # In PY3 using the modern websocket_client lib the text also comes as a byte buffer.
            else:
                raise Exception("Web stream ws helper got a message type that's not supported. "+str(msgType))

            # Figure out if we should compress the data.
            usingCompression = len(buffer) >= Compression.MinSizeToCompress
            originalDataSize = 0
            compressionResult = None
            if usingCompression:
                originalDataSize = len(buffer)
                compressionResult = Compression.Get().Compress(self.CompressionContext, buffer)
                buffer = compressionResult.Bytes

            # Send the message along!
            builder = StreamMsgBuilder.CreateBuffer(len(buffer) + 200)

            # Note its ok to have an empty buffer, we still want to send the ping.
            dataOffset = None
            if len(buffer) > 0:
                dataOffset = builder.CreateByteVector(buffer)

            # Setup the message to send.
            WebStreamMsg.Start(builder)
            WebStreamMsg.AddStreamId(builder, self.Id)
            WebStreamMsg.AddIsControlFlagsOnly(builder, False)
            WebStreamMsg.AddWebsocketDataType(builder, sendType)
            if usingCompression:
                WebStreamMsg.AddDataCompression(builder, compressionResult.CompressionType)
                WebStreamMsg.AddOriginalDataSize(builder, originalDataSize)
            if dataOffset is not None:
                WebStreamMsg.AddData(builder, dataOffset)
            webStreamMsgOffset = WebStreamMsg.End(builder)
            buffer, msgStartOffsetBytes, msgSizeBytes = StreamMsgBuilder.CreateStreamMsgAndFinalize(builder, MessageContext.MessageContext.WebStreamMsg, webStreamMsgOffset)

            # Send it!
            self.WebStream.SendToStream(buffer, msgStartOffsetBytes, msgSizeBytes)
        except Exception as e:
            Sentry.Exception(self.getLogMsgPrefix()+ " got an error while trying to forward websocket data to the service.", e)
            self.WebStream.Close()


    def onWsClosed(self, ws):
        # Only handle callbacks for the current websocket.
        if self.Ws is not None and self.Ws != ws:
            return

        # Indicate the socket is closed.
        self.IsWsObjClosed = True

        # Make sure the stream is closed.
        self.WebStream.Close()


    def onWsError(self, ws, error):
        # Only handle callbacks for the current websocket.
        if self.Ws is not None and self.Ws != ws:
            return

        # If we are closed, don't bother reporting or reconnecting.
        isClosed = True
        with self.StateLock:
            isClosed = self.IsClosed

        # Check to see if this webstream is closed or not.
        # If the webstream is closed we don't want to bother with any other attempts to re-connect.
        if isClosed is False:
            # If we got an error before the websocket was ever opened, it was an issue connecting the websocket.
            # In that case this function will handle trying to connect again.
            if self.SuccessfullyOpenedSocket is False:
                if self.AttemptConnection() is True:
                    # If AttemptConnection returns true, a new connection is being attempted.
                    # We should not close the webstream, but instead just close to give this connection a chance.
                    return
                else:
                    # If we never successfully connected, set the flag on the close message to indicate such.
                    self.WebStream.SetClosedDueToFailedRequestConnection()

            # Since the webstream still thinks it's open, report this error since it will be the one shutting the web stream down.
            self.Logger.error(self.getLogMsgPrefix()+" got an error from the websocket: "+str(error))

        # Always call close on the web stream, because it's safe to call even if it's closed already
        # this just makes sure we don't accidentally close the stream somehow.
        self.WebStream.Close()


    def onWsOpened(self, ws):
        # Only handle callbacks for the current websocket.
        if self.Ws is not None and self.Ws != ws:
            return

        # Update the state to indicate we are ready to take messages.
        self.IsWsObjClosed = False
        self.IsWsObjOpened = True
        self.SuccessfullyOpenedSocket = True
        self.Logger.info(self.getLogMsgPrefix()+"opened, attempt "+str(self.ConnectionAttempt) + " after " +str(time.time() - self.OpenedTime) + " seconds")


    def getLogMsgPrefix(self):
        return "Web Stream ws   ["+str(self.Id)+"] "
