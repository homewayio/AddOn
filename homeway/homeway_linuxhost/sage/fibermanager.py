import sys
import struct
import logging
import threading
import octoflatbuffers
from typing import List

from homeway.sentry import Sentry

from homeway.Proto import SageStreamMessage
from homeway.Proto import SageFiber
from homeway.Proto import SageDataContext
from homeway.Proto.SageOperationTypes import SageOperationTypes
from homeway.Proto.SageDataTypesFormats import SageDataTypesFormats

from .fabric import Fabric


# Manages the fiber streams being sent over Fabric.
class FiberManager:


    def __init__(self, logger:logging.Logger):
        self.Logger = logger
        self.Fabric:Fabric = None

        # Used to keep track of pending request contexts.
        self.StreamContextLock = threading.Lock()
        self.StreamContextMap = {}
        self.StreamId = 1


    # Set the fabric connection.
    def SetFabric(self, fabric):
        self.Fabric = fabric


    # Called when the socket is closed and any pending requests need to be cleaned up.
    def OnSocketReset(self):
        # Release the pending contexts and clear them.
        # There's no need to send close messages, as the socket is already closed.
        with self.StreamContextLock:
            self.StreamId = 1
            for _, context in self.StreamContextMap.items():
                context.Event.set()
            self.StreamContextMap.clear()


    # Called whenever a new listen is started, to make sure any existing listen calls are closed.
    # For example, if we got a start and chunks, and a new start before the end.
    def ResetListen(self):
        # Check if there's a current listen request.
        context:StreamContext = None
        with self.StreamContextLock:
            for _, c in self.StreamContextMap.items():
                # We only allow one type of request at a time, so if we find it, it's the stream we want.
                if c.RequestType == SageOperationTypes.Listen:
                    context = c
                    break
        if context is None:
            return

        # We need to make sure this is cleaned up and possibly send a close message.
        self._CleanUpStreamContext(context.StreamId)


    # First, call this with isTransmissionDone set to false to upload stream data. When called like this, this does not block.
    # When the audio is fully streamed, call with isTransmissionDone and the reaming buffer (if any) to get the response. This will block.
    # No matter how it's called, it returns None on failure. If this fails at anytime during a stream, it should not be called again until ResetListen is called.
    def Listen(self, isTransmissionDone:bool, audio:bytes, audioFormat:SageDataTypesFormats, sampleRate:int, channels:int, bitsPerSample:int) -> str:

        # This is only called on the first message sent, this sends the audio settings.
        def createDataContextOffset(builder:octoflatbuffers.Builder) -> int:
            return self._CreateDataContext(builder, audioFormat, sampleRate, channels, bitsPerSample)

        class ResponseContext:
            Text = None
            StatusCode = None
        response:ResponseContext = ResponseContext()

        # This can be called at anytime, streaming or waiting for the response.
        # If it's called while streaming, an error has occurred and we should stop until the next audio reset.
        def onDataStreamReceived(statusCode:int, data:bytearray, dataContext:SageDataContext):
            # For listen, this should only be called once
            if response.StatusCode is not None:
                raise Exception("Sage Listen onDataStreamReceived called more than once.")

            # Check for a failure, which can happen at anytime.
            # If we have anything but a 200, stop processing now.
            response.StatusCode = statusCode
            if response.StatusCode != 200:
                return

            # This data format must be text.
            dataType = dataContext.DataType()
            if dataType != SageDataTypesFormats.Text:
                response.StatusCode = 500
                raise Exception("Sage Listen got a response that wasn't text?")

            # Set the text.
            response.Text = data.decode("utf-8")

        # Do the operation, stream or wait for the response.
        result = self._SendAndReceive(SageOperationTypes.Listen, audio, createDataContextOffset, onDataStreamReceived, isTransmissionDone)

        # If we failed, we always return None, for both upload streaming or the final response.
        if result is False:
            return None

        # If the status code is set at any time and not 200, we failed, regardless of the mode.
        if response.StatusCode is not None and response.StatusCode != 200:
            self.Logger.error(f"Sage Listen failed with status code {response.StatusCode}")
            return None

        # If we're still uploading, we return an empty string on success or None on failure.
        if isTransmissionDone is False:
            # If we are still uploading, we return an empty string on to indicate success.
            # to keep the same return types as the final response call.
            return ""

        # If we are here, this was the blocking request to get the result, so this should always be set.
        if response.Text is None:
            self.Logger.error("Sage Listen didn't fail the status code but has no text?")
            return None
        return response.Text


    # TODO
    def Speak(self, text:str) -> bytearray:

        # This is only called on the first message sent, this sends the audio settings.
        def createDataContextOffset(builder:octoflatbuffers.Builder) -> int:
            # TODO
            return self._CreateDataContext(builder, SageDataTypesFormats.Text, 0, 0, 0)

        class ResponseContext:
            Bytes = None
            StatusCode = None
        response:ResponseContext = ResponseContext()

        # This can be called at anytime, streaming or waiting for the response.
        # If it's called while streaming, an error has occurred and we should stop until the next audio reset.
        def onDataStreamReceived(statusCode:int, data:bytearray, dataContext:SageDataContext):
            # For listen, this should only be called once
            if response.StatusCode is not None:
                raise Exception("Sage Listen onDataStreamReceived called more than once.")

            # Check for a failure, which can happen at anytime.
            # If we have anything but a 200, stop processing now.
            response.StatusCode = statusCode
            if response.StatusCode != 200:
                return

            # This data format must be text.
            dataType = dataContext.DataType()
            if dataType != SageDataTypesFormats.AudioPCM:
                response.StatusCode = 500
                raise Exception("Sage Listen got a response that wasn't text?")

            # Set the text.
            response.Bytes = data

        # Do the operation, stream or wait for the response.
        data = text.encode("utf-8")
        result = self._SendAndReceive(SageOperationTypes.Speak, data, createDataContextOffset, onDataStreamReceived, True)

        # If we failed, we always return None, for both upload streaming or the final response.
        if result is False:
            return None

        # If the status code is set at any time and not 200, we failed, regardless of the mode.
        if response.StatusCode is not None and response.StatusCode != 200:
            self.Logger.error(f"Sage Listen failed with status code {response.StatusCode}")
            return None

        return response.Bytes


    # A helper function that allows us to send messages for many different types of actions.
    # Returns true on success, false on failure.
    # Note the behavior of isTransmissionDone:
    #   If isTransmissionDone set to False. This will cause a stream to be opened and multiple upload data messages to be sent. This function will NOT block to wait for a response, since more data needs to be sent first.
    #   When it's called and isTransmissionDone is set to True, the final data will be sent and the function will block until a response is received.
    # The onDataReceivedCallback can be called many times if the response is being streamed.
    # The onDataReceivedCallback can also be called on any call into this function with a status code, if the stream closed early for some reason.
    def _SendAndReceive(self,
                        requestType:SageOperationTypes,
                        sendData:bytearray,
                        dataContextCreateCallback,
                        onDataStreamReceivedCallback,
                        isTransmissionDone:bool=True,
                        timeoutSec:float = 20.0) -> bool:

        # First, get or create the stream.
        # This might be a new stream, or it might be a stream we have already started and are uploading data to.
        context:StreamContext = None
        with self.StreamContextLock:
            for _, c in self.StreamContextMap.items():
                # We only allow one type of request at a time, so if we find it, it's the stream we want.
                if c.RequestType == requestType:
                    context = c
                    break

        # If we didn't find a stream, this is a new stream, so create it.
        if context is None:
            with self.StreamContextLock:
                streamId = self.StreamId
                self.StreamId += 1
                context = StreamContext(streamId, requestType)
                self.StreamContextMap[streamId] = context
        else:
            # If we found a context, the only valid state is that the stream is open and we're uploading data.
            if context.HasSentOpenMessage is False:
                self.Logger.error(f"Sage failed to send message, stream [{streamId}] hans't sent the open message but was called again.")
                return False
            if context.IsDataTransmissionDone:
                self.Logger.error(f"Sage failed to send message, stream [{streamId}] is already done transmitting data but was called again.")
                return False

        # Now that we have the context and might have created it, we might need to clean up the context when we exit success or failed.
        # The only reason we wouldn't clean up the context is if we're going to keep it around for more upload data.
        cleanUpContextOnExit = True

        # Now that we have the context, build the message.
        try:
            # Allocate the building buffer.
            builder = octoflatbuffers.Builder(len(sendData) + 500)

            # Check if this is the first message, the open message.
            isOpenMessage = not context.HasSentOpenMessage

            # If this is the open message, we must send a data context
            dataContextOffset = None
            if isOpenMessage:
                dataContextOffset = dataContextCreateCallback(builder)
                if dataContextOffset is None:
                    self.Logger.error("Sage failed to send, the data context callback didn't return a data context offset.")
                    return False

            # Create the message.
            msgOffset = self._CreateStreamMessage(builder, context.StreamId, requestType, sendData, dataContextOffset, isOpen=isOpenMessage, isTransmissionDone=isTransmissionDone)

            # Finalize the message.
            SageFiber.Start(builder)
            SageFiber.AddMessage(builder, msgOffset)
            streamMsgOffset = SageFiber.End(builder)
            builder.FinishSizePrefixed(streamMsgOffset)
            buffer = builder.Bytes
            bufferStartOffsetBytes = builder.Head()
            bufferLen = len(buffer) - bufferStartOffsetBytes

            # Send the message
            if self.Fabric.SendMsg(buffer, bufferStartOffsetBytes, bufferLen) is False:
                self.Logger.error("Sage Fabric failed to send a message.")
                return False

            # We know the message was sent, so set the open message flag.
            context.HasSentOpenMessage = True

            # Now, if the upload data transmission isn't done, return, which will allow more data to be sent.
            if isTransmissionDone is False:
                # If the status code is set, it means the server already returned a result, which can only happen for an error,
                # since we haven't set the isTransmissionDone upload flag yet.
                if context.StatusCode is not None:
                    onDataStreamReceivedCallback(context.StatusCode, None, None)
                    return False

                # Otherwise, keep the context around and return success.
                cleanUpContextOnExit = False
                return True

            # We now need to wait for the entire streamed response.
            while True:
                # Wait for the data or a timeout.
                context.Event.wait(timeoutSec)

                # We don't use the result of the timeout because we need to check under lock to see if we got anything.
                # This is needed so we can clear the Event flag if we have more data to stream.
                data:List[bytearray] = None
                dataContext:SageDataContext = None
                statusCode:int = None
                isDataDownloadComplete:bool = True
                with self.StreamContextLock:
                    # Grab all of the data we currently have and process it.
                    data = context.Data
                    context.Data = []
                    isDataDownloadComplete = context.IsDataDownloadComplete
                    dataContext = context.DataContext
                    statusCode = context.StatusCode

                    # Clear the event under lock to ensure we don't miss a set.
                    context.Event.clear()

                # Regardless of the other vars, if we didn't get any data, the response wait timed out.
                if len(data) == 0:
                    self.Logger.error("Sage message timed out while waiting for a response.")
                    context.StatusCode = 408
                    onDataStreamReceivedCallback(context.StatusCode, None, None)
                    return False

                # Process the data
                if dataContext is None or statusCode is None or data is None:
                    raise Exception("Sage Listen got a response that was missing the data context or the status code.")

                # Process the data.
                for d in data:
                    onDataStreamReceivedCallback(statusCode, d, dataContext)

                # If we processed all the data and the stream is done, we're done.
                if isDataDownloadComplete:
                    return True

        except Exception as e:
            Sentry.Exception("Sage message exception", e)
        finally:
            # If we don't want to keep the context around, clean it up now.
            if cleanUpContextOnExit:
                self._CleanUpStreamContext(context.StreamId)
        return False


    # Returns the offset of the message in the buffer.
    def _CreateStreamMessage(self, builder:octoflatbuffers.Builder, streamId:int, msgType:SageOperationTypes, data:bytearray, dataContextOffset:int=None, isOpen:bool=False, isClose:bool=False, isTransmissionDone:bool=False) -> int:
        # Create any buffers we need.
        dataOffset = None
        if data is not None:
            dataOffset = builder.CreateByteVector(data)

        # Build the message.
        SageStreamMessage.Start(builder)
        SageStreamMessage.AddStreamId(builder, streamId)
        SageStreamMessage.AddType(builder, msgType)
        SageStreamMessage.AddIsOpenMsg(builder, isOpen)
        SageStreamMessage.AddIsCloseMsg(builder, isClose)
        SageStreamMessage.AddIsDataTransmissionDone(builder, isTransmissionDone)
        if dataContextOffset is not None:
            SageStreamMessage.AddDataContext(builder, dataContextOffset)
        if dataOffset is not None:
            SageStreamMessage.AddData(builder, dataOffset)
        return SageStreamMessage.End(builder)


    # Builds the data context.
    def _CreateDataContext(self, builder:octoflatbuffers.Builder, dataFormat:SageDataTypesFormats, sampleRate:int, channels:int, bitsPerSample:int) -> int:
        SageDataContext.Start(builder)
        SageDataContext.AddDataType(builder, dataFormat)
        SageDataContext.AddSampleRate(builder, sampleRate)
        SageDataContext.AddChannels(builder, channels)
        SageDataContext.AddBitsPerSample(builder, bitsPerSample)
        return SageDataContext.End(builder)


    # Removes the stream context from the map.
    # This will also send a close message, if required.
    def _CleanUpStreamContext(self, streamId:int):
        try:
            # Remove the context from the map.
            context:StreamContext = None
            with self.StreamContextLock:
                context = self.StreamContextMap.pop(streamId, None)

            # If there is no context, we're done.
            if context is None:
                return

            # If we never sent the open message or the data download is complete, there's no need to send the close message.
            if context.HasSentOpenMessage is False or context.IsDataDownloadComplete is True:
                return

            # Send the close message.
            builder = octoflatbuffers.Builder(50)
            msgOffset = self._CreateStreamMessage(builder, context.StreamId, context.RequestType, bytearray(), isClose=True)
            SageFiber.Start(builder)
            SageFiber.AddMessage(builder, msgOffset)
            streamMsgOffset = SageFiber.End(builder)
            builder.FinishSizePrefixed(streamMsgOffset)
            buffer = builder.Bytes
            bufferStartOffsetBytes = builder.Head()
            bufferLen = len(buffer) - bufferStartOffsetBytes
            if self.Fabric.SendMsg(buffer, bufferStartOffsetBytes, bufferLen) is False:
                # Only do a warning, because this might have failed bc the socket is closed.
                self.Logger.info("Failed to send Sage close message.")
        except Exception as e:
            # If we fail, reset the socket.
            Sentry.Exception("Sage close message send exception", e)
            self.Fabric.Close()


    # Called from the socket when a message is received
    def OnIncomingMessage(self, buf:bytearray):
        # First, read the message size.
        # We add 4 to account for the full buffer size, including the uint32.
        messageSize = self.Unpack32Int(buf, 0) + 4

        # Check that things make sense.
        if messageSize != len(buf):
            raise Exception("We got an Sage ws message that's not the correct size! MsgSize:"+str(messageSize)+"; BufferLen:"+str(len(buf)))

        # Parse the response
        fiber = SageFiber.SageFiber.GetRootAs(buf, 4)
        msg = fiber.Message()
        if msg is None:
            raise Exception("Sage Fiber message is None.")

        # TODO - handle one off close messages

        # Always required.
        streamId = msg.StreamId()
        data = msg.DataAsByteArray()
        isDataTransmissionDone = msg.IsDataTransmissionDone()
        if streamId is None or streamId <= 0:
            raise Exception(f"Sage Fiber message has an invalid stream id. {streamId}")
        if data is None:
            raise Exception("Sage Fiber message has an invalid data")

        # Find the context and set the response.
        with self.StreamContextLock:
            context = self.StreamContextMap.get(streamId, None)
            if context is None:
                # If there's no context this is usually ok, it means the stream was closed and this is old.
                self.Logger.info(f"Sage got a message for stream [{streamId}] but couldn't find the context.")
                return

            # Ensure we haven't already gotten the data complete flag.
            if context.IsDataDownloadComplete:
                raise Exception("Sage ws message was sent after IsDataDownloadComplete was set.")
            if isDataTransmissionDone:
                context.IsDataDownloadComplete = True

            if context.StatusCode is None:
                # This is the first (and possibly only) response.
                # These are required.
                statusCode = msg.StatusCode()
                dataContext = msg.DataContext()
                if statusCode is None or statusCode <= 0:
                    raise Exception(f"Sage Fiber message has an invalid status code. {statusCode}")
                if dataContext is None:
                    raise Exception("Sage Fiber message has is missing the data context.")
                context.StatusCode = statusCode
                context.DataContext = dataContext

            # Append the data.
            context.Data.append(data)

            # Set the event so the caller can consume the data.
            context.Event.set()


    # Helper to unpack uint32
    def Unpack32Int(self, buffer, bufferOffset) :
        if sys.byteorder == "little":
            if sys.version_info[0] < 3:
                return (struct.unpack('1B', buffer[0 + bufferOffset])[0]) + (struct.unpack('1B', buffer[1 + bufferOffset])[0] << 8) + (struct.unpack('1B', buffer[2 + bufferOffset])[0] << 16) + (struct.unpack('1B', buffer[3 + bufferOffset])[0] << 24)
            else:
                return (buffer[0 + bufferOffset]) + (buffer[1 + bufferOffset] << 8) + (buffer[2 + bufferOffset] << 16) + (buffer[3 + bufferOffset] << 24)
        else:
            if sys.version_info[0] < 3:
                return (struct.unpack('1B', buffer[0 + bufferOffset])[0] << 24) + (struct.unpack('1B', buffer[1 + bufferOffset])[0] << 16) + (struct.unpack('1B', buffer[2 + bufferOffset])[0] << 8) + struct.unpack('1B', buffer[3 + bufferOffset])[0]
            else:
                return (buffer[0 + bufferOffset] << 24) + (buffer[1 + bufferOffset] << 16) + (buffer[2 + bufferOffset] << 8) + (buffer[3 + bufferOffset])


# Used to track the current state of a stream.
class StreamContext:

    def __init__(self, streamId:int, requestType:SageOperationTypes):
        # Core properties.
        self.StreamId = streamId
        self.RequestType = requestType

        # State flags
        self.IsDataTransmissionDone = False
        self.HasSentOpenMessage = False

        # The event that will be set when the response is received.
        # Note this will be set multiple times if the response is being streamed.
        self.Event = threading.Event()

        # Set when the first response is received.
        self.StatusCode:int = None
        self.DataContext:SageDataContext = None

        # When set, the data has been fully downloaded.
        self.IsDataDownloadComplete = False

        # The response data.
        # This will be appended to as more data is received.
        self.Data:List[bytearray] = []
