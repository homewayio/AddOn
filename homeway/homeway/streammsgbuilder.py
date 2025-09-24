from typing import Any, Optional, Tuple

import octoflatbuffers
from .buffer import Buffer

from .Proto import MessageContext
from .Proto import HandshakeSyn
from .Proto import StreamMessage

# A helper class that builds our Stream messages as flatbuffers.
class StreamMsgBuilder:

    @staticmethod
    def BuildHandshakeSyn(
                pluginId:str,
                privateKey:str,
                isPrimarySession:bool,
                pluginVersion:str,
                localHttpProxyPort:int,
                localIp:str,
                rsaChallenge:bytes,
                rasKeyVersionInt:int,
                summonMethod:int,
                addonType:int,
                receiveCompressionType:int
            ) -> Tuple[Buffer, int, int]:
        # Get a buffer
        builder = StreamMsgBuilder.CreateBuffer(500)

        # Setup strings
        pluginIdOffset = builder.CreateString(pluginId) #pyright: ignore[reportUnknownMemberType]
        privateKeyOffset = builder.CreateString(privateKey) #pyright: ignore[reportUnknownMemberType]
        pluginVersionOffset = builder.CreateString(pluginVersion) #pyright: ignore[reportUnknownMemberType]
        localIpOffset = None
        if localIp is not None:
            localIpOffset = builder.CreateString(localIp) #pyright: ignore[reportUnknownMemberType]

        # Setup the data vectors
        rasChallengeOffset = builder.CreateByteVector(rsaChallenge) #pyright: ignore[reportUnknownMemberType]

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
    def CreateBuffer(size:int) -> octoflatbuffers.Builder:
        return octoflatbuffers.Builder(size)


    @staticmethod
    def CreateStreamMsgAndFinalize(builder:octoflatbuffers.Builder, contextType:int, contextOffset:int) -> Tuple[Buffer, int, int]:
        # Create the message
        StreamMessage.Start(builder)
        StreamMessage.AddContextType(builder, contextType)
        StreamMessage.AddContext(builder, contextOffset)
        streamMsgOffset = StreamMessage.End(builder)

        # Finalize the message. We use the size prefixed
        builder.FinishSizePrefixed(streamMsgOffset) #pyright: ignore[reportUnknownMemberType]

        # Instead of using Output, which will create a copy of the buffer that's trimmed, we return the fully built buffer
        # with the header offset set and size. Flatbuffers are built backwards, so there's usually space in the front were we can add data
        # without creating a new buffer!
        # Note that the buffer is a bytearray
        buffer = Buffer(builder.Bytes)
        msgStartOffsetBytes = builder.Head()
        return (buffer, msgStartOffsetBytes, len(buffer) - msgStartOffsetBytes)


    @staticmethod
    def BytesToString(buf:Any) -> Optional[str]:
        # The default value for optional strings is None
        # So, we handle it.
        if buf is None:
            return None
        return buf.decode("utf-8")
