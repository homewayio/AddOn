import octoflatbuffers

from .Proto import MessageContext
from .Proto import HandshakeSyn
from .Proto import StreamMessage
from .Proto.AddonTypes import AddonTypes
from .Proto.DataCompression import DataCompression

# A helper class that builds our Stream messages as flatbuffers.
class StreamMsgBuilder:

    @staticmethod
    def BuildHandshakeSyn(pluginId, privateKey, isPrimarySession, pluginVersion, localHttpProxyPort, localIp, rsaChallenge, rasKeyVersionInt, summonMethod, addonType:AddonTypes, receiveCompressionType:DataCompression):
        # Get a buffer
        builder = StreamMsgBuilder.CreateBuffer(500)

        # Setup strings
        pluginIdOffset = builder.CreateString(pluginId)
        privateKeyOffset = builder.CreateString(privateKey)
        pluginVersionOffset = builder.CreateString(pluginVersion)
        localIpOffset = None
        if localIp is not None:
            localIpOffset = builder.CreateString(localIp)

        # Setup the data vectors
        rasChallengeOffset = builder.CreateByteVector(rsaChallenge)

        # Build the handshake syn
        HandshakeSyn.Start(builder)
        HandshakeSyn.AddPluginId(builder, pluginIdOffset)
        HandshakeSyn.AddPrivateKey(builder, privateKeyOffset)
        HandshakeSyn.AddIsPrimaryConnection(builder, isPrimarySession)
        HandshakeSyn.AddPluginVersion(builder, pluginVersionOffset)
        HandshakeSyn.AddSummonMethod(builder, summonMethod)
        HandshakeSyn.AddAddonType(builder, addonType)
        if localIpOffset is not None:
            HandshakeSyn.AddLocalDeviceIp(builder, localIpOffset)
        HandshakeSyn.AddLocalHttpProxyPort(builder, localHttpProxyPort)
        HandshakeSyn.AddRsaChallenge(builder, rasChallengeOffset)
        HandshakeSyn.AddRasChallengeVersion(builder, rasKeyVersionInt)
        HandshakeSyn.AddReceiveCompressionType(builder, receiveCompressionType)
        synOffset = HandshakeSyn.End(builder)

        return StreamMsgBuilder.CreateStreamMsgAndFinalize(builder, MessageContext.MessageContext.HandshakeSyn, synOffset)


    @staticmethod
    def CreateBuffer(size) -> octoflatbuffers.Builder:
        return octoflatbuffers.Builder(size)


    @staticmethod
    def CreateStreamMsgAndFinalize(builder, contextType, contextOffset):
        # Create the message
        StreamMessage.Start(builder)
        StreamMessage.AddContextType(builder, contextType)
        StreamMessage.AddContext(builder, contextOffset)
        streamMsgOffset = StreamMessage.End(builder)

        # Finalize the message. We use the size prefixed
        builder.FinishSizePrefixed(streamMsgOffset)

        # Instead of using Output, which will create a copy of the buffer that's trimmed, we return the fully built buffer
        # with the header offset set and size. Flatbuffers are built backwards, so there's usually space in the front were we can add data
        # without creating a new buffer!
        # Note that the buffer is a bytearray
        buffer = builder.Bytes
        msgStartOffsetBytes = builder.Head()
        return (buffer, msgStartOffsetBytes, len(buffer) - msgStartOffsetBytes)
        #return builder.Output()


    @staticmethod
    def BytesToString(buf) -> str:
        # The default value for optional strings is None
        # So, we handle it.
        if buf is None:
            return None
        return buf.decode("utf-8")
