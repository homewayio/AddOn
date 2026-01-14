import logging
import sys
import threading
import traceback
from typing import Dict, List, Optional

from .WebStream.webstreamimpl import WebStreamImpl
from .httprequest import HttpRequest
from .localip import LocalIpHelper
from .streammsgbuilder import StreamMsgBuilder
from .serverauth import ServerAuthHelper
from .sentry import Sentry
from .compression import Compression
from .interfaces import IStream, ISession
from .buffer import Buffer, ByteLikeOrMemoryView

from .Proto.StreamMessage import StreamMessage
from .Proto import HandshakeAck
from .Proto import MessageContext
from .Proto import WebStreamMsg
from .Proto import Summon
from .Proto.DataCompression import DataCompression

#
# This file represents one connection session to the service. If anything fails it is destroyed and a new connection will be made.
#

class Session(ISession):

    def __init__(self,
                    stream:IStream,
                    logger:logging.Logger,
                    pluginId:str,
                    privateKey:str,
                    isPrimarySession:bool,
                    sessionId:int,
                    pluginVersion:str
                ):
        self.ActiveWebStreams:Dict[int, WebStreamImpl] = {}
        self.ActiveWebStreamsLock = threading.Lock()
        self.IsAcceptingStreams = True

        self.Logger = logger
        self.SessionId = sessionId
        self.Stream = stream
        self.PluginId = pluginId
        self.PrivateKey = privateKey
        self.isPrimarySession = isPrimarySession
        self.PluginVersion = pluginVersion

        # Create our server auth helper.
        self.ServerAuth = ServerAuthHelper(self.Logger)


    def OnSessionError(self, backoffModifierSec:int) -> None:
        # Just forward
        self.Stream.OnSessionError(self.SessionId, backoffModifierSec)


    def Send(self, buffer:Buffer, msgStartOffsetBytes:int, msgSize:int) -> None:
        # The message is already encoded, pass it along to the socket.
        self.Stream.SendMsg(buffer, msgStartOffsetBytes, msgSize)


    def HandleSummonRequest(self, msg:StreamMessage) -> None:
        try:
            context = msg.Context()
            if context is None:
                self.Logger.error("Summon message is missing context.")
                return

            # Parse the summon message
            summonMsg = Summon.Summon()
            summonMsg.Init(context.Bytes, context.Pos)
            serverConnectUrl = StreamMsgBuilder.BytesToString(summonMsg.ServerConnectUrl())
            summonMethod = summonMsg.SummonMethod()
            if serverConnectUrl is None or len(serverConnectUrl) == 0:
                self.Logger.error("Summon notification is missing a server url.")
                return

            # Process it!
            self.Stream.OnSummonRequest(self.SessionId, serverConnectUrl, summonMethod)
        except Exception as e:
            Sentry.OnException("Failed to handle summon request ", e)


    def HandleHandshakeAck(self, msg:StreamMessage) -> None:
        # Get the context.
        context = msg.Context()
        if context is None:
            raise Exception("HandleHandshakeAck message is missing context.")

        # Handles a handshake ack message.
        handshakeAck = HandshakeAck.HandshakeAck()
        handshakeAck.Init(context.Bytes, context.Pos)

        if handshakeAck.Accepted():
            # Accepted!
            # Parse and validate the RAS challenge.
            rasChallengeResponse = StreamMsgBuilder.BytesToString(handshakeAck.RsaChallengeResult())
            if self.ServerAuth.ValidateChallengeResponse(rasChallengeResponse) is False:
                raise Exception("Server RAS challenge failed!")
            # Parse out the response and report.
            connectedAccounts:List[str] = []
            connectedAccountsLen = handshakeAck.ConnectedAccountsLength()
            if handshakeAck.ConnectedAccountsLength() != 0:
                i = 0
                connectedAccounts = []
                while i < connectedAccountsLen:
                    account = StreamMsgBuilder.BytesToString(handshakeAck.ConnectedAccounts(i))
                    if account is not None:
                        connectedAccounts.append(account)
                    i += 1

            # Parse out the api key
            apiKey = StreamMsgBuilder.BytesToString(handshakeAck.ApiKey())
            if apiKey is None:
                raise Exception("Handshake ack is missing apiKey.")

            self.Stream.OnHandshakeComplete(self.SessionId, apiKey, connectedAccounts)
        else:
            # Pull out the error.
            error = handshakeAck.Error()
            if error is not None:
                error = StreamMsgBuilder.BytesToString(error)
            else:
                error = "no error given"
            self.Logger.error("Handshake failed, reason '" + str(error) + "'")

            # The server can send back a backoff time we should respect.
            backoffModifierSec = handshakeAck.BackoffSeconds()

            # Check if an update is required, if so we need to tell the UI and set the back off to be crazy high.
            if handshakeAck.RequiresPluginUpdate():
                backoffModifierSec = 43200 # 1 month
                self.Stream.OnPluginUpdateRequired()

            self.OnSessionError(backoffModifierSec)


    def HandleWebStreamMessage(self, msg:StreamMessage) -> None:
        # Get the context.
        context = msg.Context()
        if context is None:
            raise Exception("HandleWebStreamMessage message is missing context.")

        # Handles a web stream.
        webStreamMsg = WebStreamMsg.WebStreamMsg()
        webStreamMsg.Init(context.Bytes, context.Pos)

        # Get the stream id
        streamId = webStreamMsg.StreamId()
        if streamId == 0:
            self.Logger.error("We got a web stream message for an invalid stream id of 0")
            # throwing here will terminate this entire Socket and reset.
            raise Exception("We got a web stream message for an invalid stream id of 0")

        # Grab the lock before messing with the map.
        localStream:Optional[WebStreamImpl] = None
        with self.ActiveWebStreamsLock:
            localStream = self.ActiveWebStreams.get(streamId, None)
            if localStream is None:
                # It doesn't exist. Validate this is a open message.
                if webStreamMsg.IsOpenMsg() is False:
                    # TODO - Handle messages that arrive for just closed streams better.
                    isCloseMessage = webStreamMsg.IsCloseMsg()
                    if isCloseMessage:
                        self.Logger.debug("We got a web stream message for a stream id [" + str(streamId) + "] that doesn't exist and isn't an open message. IsClose:"+str(isCloseMessage))
                    else:
                        self.Logger.warning("We got a web stream message for a stream id [" + str(streamId) + "] that doesn't exist and isn't an open message. IsClose:"+str(isCloseMessage))
                    # Don't throw, because this message maybe be coming in from the server as the local side closed.
                    return

                # Check that we are still accepting streams
                if self.IsAcceptingStreams is False:
                    self.Logger.info("Session got a webstream open request after we stopped accepting streams. streamId:"+str(streamId))
                    return

                # Create the new stream object now.
                localStream = WebStreamImpl(name="OctoWebStreamPumper", args=(self.Logger, streamId, self, ))
                # Set it in the map
                self.ActiveWebStreams[streamId] = localStream
                # Start it's main worker thread
                localStream.start()

        # If we get here, we know we must have a localStream
        localStream.OnIncomingServerMessage(webStreamMsg)


    def WebStreamClosed(self, sessionId:int) -> None:
        # Called from the webstream when it's closing.
        with self.ActiveWebStreamsLock:
            # Provide none so this doesn't thrown
            foundStream = self.ActiveWebStreams.pop(sessionId, None)
            if foundStream is None:
                self.Logger.error("A web stream asked to close that wasn't in our webstream map.")


    def CloseAllWebStreamsAndDisable(self):
        # The streams will remove them selves from the map when they close, so all we need to do is ask them
        # to close.
        localWebStreamList:List[WebStreamImpl] = []
        with self.ActiveWebStreamsLock:
            # Close them all.
            self.Logger.info("Closing all open web stream sockets ("+str(len(self.ActiveWebStreams))+")")

            # Set the flag to indicate we aren't accepting any more
            self.IsAcceptingStreams = False

            # Copy all of the streams locally.
            # pylint: disable=consider-using-dict-items
            for streamId in self.ActiveWebStreams:
                localWebStreamList.append(self.ActiveWebStreams[streamId])

        # Try catch all of this so we don't leak exceptions.
        # Use our local web stream list to tell them all to close.
        try:
            for webStream in localWebStreamList:
                try:
                    webStream.Close()
                except Exception as e:
                    Sentry.OnException("Exception thrown while closing web streamId", e)
        except Exception as ex:
            Sentry.OnException("Exception thrown while closing all web streams.", ex)


    def StartHandshake(self, summonMethod:int, addonType:int):
        # Send the handshakesyn
        try:
            # Get our unique challenge
            rasChallenge = self.ServerAuth.GetEncryptedChallenge()
            if rasChallenge is None:
                raise Exception("Rsa challenge generation failed.")
            rasChallengeKeyVerInt = ServerAuthHelper.c_ServerAuthKeyVersion

            # Define which type of compression we can receive (beyond None)
            # Ideally this is zstandard lib, but all client must support zlib, so we can fallback to it.
            receiveCompressionType = DataCompression.Zlib
            if Compression.Get().CanUseZStandardLib:
                receiveCompressionType = DataCompression.ZStandard

            # Build the message
            buffer, msgStartOffsetBytes, msgSizeBytes = StreamMsgBuilder.BuildHandshakeSyn(
                self.PluginId, self.PrivateKey, self.isPrimarySession, self.PluginVersion,
                HttpRequest.GetLocalHttpProxyPort(), LocalIpHelper.TryToGetLocalIpOfConnectionTarget(),
                rasChallenge, rasChallengeKeyVerInt, summonMethod, addonType, receiveCompressionType)

            # Send!
            self.Stream.SendMsg(buffer, msgStartOffsetBytes, msgSizeBytes)
        except Exception as e:
            Sentry.OnException("Failed to send handshake syn.", e)
            self.OnSessionError(0)


    # This is the main receive function for all messages coming from the server.
    # Since all web stream messages use their own threads, we don't spin off a thread
    # for messages here. However, that means we need to be careful to not do any
    # long processing in the function, since it will delay all incoming messages.
    def HandleMessage(self, msgBytes:Buffer) -> None:
        # Decode the message.
        msg:Optional[StreamMessage] = None
        try:
            msg = self.DecodeStreamMessage(msgBytes)
        except Exception as e:
            Sentry.OnException("Failed to decode message local request.", e)
            self.OnSessionError(0)
            return

        # Handle it.
        try:
            # If this is a handshake ack, handle it.
            if msg.ContextType() == MessageContext.MessageContext.HandshakeAck:
                self.HandleHandshakeAck(msg)
                return

            # Handle web stream messages
            if msg.ContextType() == MessageContext.MessageContext.WebStreamMsg:
                self.HandleWebStreamMessage(msg)
                return

            # Handle summon notifications
            if msg.ContextType() == MessageContext.MessageContext.Summon:
                self.HandleSummonRequest(msg)
                return

            # We don't know what this is, probably a new message we don't understand.
            self.Logger.info("Unknown message type received, ignoring.")
            return

        except Exception as e:
            # If anything throws, we consider it a protocol failure.
            traceback.print_exc()
            Sentry.OnException("Failed to handle message.", e)
            self.OnSessionError(0)
            return


    # Helper to unpack uint32
    def Unpack32Int(self, buffer:ByteLikeOrMemoryView, bufferOffset:int):
        if sys.byteorder == "little":
            return (buffer[0 + bufferOffset]) + (buffer[1 + bufferOffset] << 8) + (buffer[2 + bufferOffset] << 16) + (buffer[3 + bufferOffset] << 24)
        else:
            return (buffer[0 + bufferOffset] << 24) + (buffer[1 + bufferOffset] << 16) + (buffer[2 + bufferOffset] << 8) + (buffer[3 + bufferOffset])


    def DecodeStreamMessage(self, buf:Buffer) -> StreamMessage:
        # Our wire protocol is a uint32 followed by the flatbuffer message.
        rawBuffer = buf.Get()

        # First, read the message size.
        # We add 4 to account for the full buffer size, including the uint32.
        messageSize = self.Unpack32Int(rawBuffer, 0) + 4

        # Check that things make sense.
        if messageSize != len(rawBuffer):
            raise Exception("We got an StreamMsg that's not the correct size! MsgSize:"+str(messageSize)+"; BufferLen:"+str(len(rawBuffer)))

        # Decode and return
        return StreamMessage.GetRootAs(rawBuffer, 4) #pyright: ignore[reportUnknownMemberType]
