import sys
import json
import struct
import logging
import threading
from typing import List, Dict
import octoflatbuffers

from homeway.sentry import Sentry
from homeway.compression import CompressionResult

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
        self.StreamContextMap:Dict[str,StreamContext] = {}
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
    # This will always return a ListenResult object
    #    If Error is ever set, the operation failed and the stream should be stopped.
    #    If Result is set, the final result has been delivered.
    #    Otherwise, the stream is in a good state and more data can be sent.
    async def Listen(self, isTransmissionDone:bool, audio:bytes, audioFormat:SageDataTypesFormats, sampleRate:int, channels:int, bytesPerSample:int, languageCode_CanBeNone:str) -> "ListenResult":

        # This is only called on the first message sent, this sends the audio settings.
        def createDataContextOffset(builder:octoflatbuffers.Builder) -> int:
            return self._CreateDataContext(builder, audioFormat, sampleRate, channels, bytesPerSample, languageCode_CanBeNone)

        # onDataStreamReceived can be called at anytime, streaming or waiting for the response.
        # If it's called while streaming, an error has occurred and we should stop until the next audio reset.
        class ResponseContext:
            Text:str = None
            StatusCode:int = None
        response:ResponseContext = ResponseContext()
        async def onDataStreamReceived(statusCode:int, data:bytearray, dataContext:SageDataContext, isFinalDataChunk:bool):
            # For listen, this should only be called once
            if response.StatusCode is not None:
                raise Exception("Sage Listen onDataStreamReceived called more than once.")

            # Check for a failure, which can happen at anytime.
            # If we have anything but a 200, stop processing now.
            response.StatusCode = statusCode
            if response.StatusCode != 200:
                return False

            # This data format must be text.
            dataType = dataContext.DataType()
            if dataType != SageDataTypesFormats.Text:
                response.StatusCode = 500
                raise Exception("Sage Listen got a response that wasn't text?")

            # Set the text.
            # Remember that an empty buffer isn't a failure, it means there were no words in the text!
            response.Text = data.decode("utf-8")
            return True

        # Do the operation, stream or wait for the response.
        result = await self._SendAndReceive(SageOperationTypes.Listen, audio, createDataContextOffset, onDataStreamReceived, isTransmissionDone)

        # If the status code is set at any time and not 200, we failed, regardless of the mode.
        # Check this before the function result, so we get the status code.
        if response.StatusCode is not None and response.StatusCode != 200:
            # In some special cases, we want to map the status code to a user message.
            errorStr = self._MapErrorStatusCodeToUserStr(response.StatusCode)
            if errorStr is None:
                errorStr = "Sage Listen failed with status code " + str(response.StatusCode)
            return ListenResult.Failure(errorStr)

        # If we failed, we always return None, for both upload streaming or the final response.
        if result is False:
            return ListenResult.Failure("Sage listen stream failed.")

        # If we're still uploading, we return an empty string on success or None on failure.
        if isTransmissionDone is False:
            # If we are still uploading, we return an empty string on to indicate success.
            # to keep the same return types as the final response call.
            return ListenResult.Success()

        # If we are here, this was the blocking request to get the result, so this should always be set.
        # Remember that an empty buffer isn't a failure, it means there were no words in the text!
        if response.Text is None:
            self.Logger.error("Sage Listen didn't fail the status code but has no text?")
            return ListenResult.Failure("Sage listen had a successful status code but had no text.")
        return ListenResult.Success(response.Text)


    # Called with a text string to synthesize audio.
    # async streamingDataReceivedCallback(SpeakDataResponse) -> bool
    #   If the callback returns False, the operation will stop.
    # Return True on success, False on failure.
    async def Speak(self, text:str, voiceName_OrNone:str, streamingDataReceivedCallback) -> bool:

        # Creates the sending data context for the text we want to send.
        def createDataContextOffset(builder:octoflatbuffers.Builder) -> int:
            return self._CreateDataContext(builder, SageDataTypesFormats.Json)

        # This onDataStreamReceived will be called each time there's more chunked audio data
        # to stream back.
        class ResponseContext:
            StatusCode:int = None
        response:ResponseContext = ResponseContext()
        async def onDataStreamReceived(statusCode:int, data:bytearray, dataContext:SageDataContext, isFinalDataChunk:bool):

            # Check for a failure, which can happen at anytime.
            # If we have anything but a 200, stop processing now.
            response.StatusCode = statusCode
            if response.StatusCode != 200:
                return

            # Create the data object and call the handler.
            # If it returns false, we will stop.
            dataResponse = SpeakDataResponse(data, dataContext.DataType(), dataContext.SampleRate(), dataContext.Channels(), dataContext.BytesPerSample(), isFinalDataChunk)
            return await streamingDataReceivedCallback(dataResponse)

        # Do the operation, stream or wait for the response.
        request = {"Text": text, "VoiceName": voiceName_OrNone}
        requestBytes = json.dumps(request).encode("utf-8")
        result = await self._SendAndReceive(SageOperationTypes.Speak, requestBytes, createDataContextOffset, onDataStreamReceived)

        # If the status code is set at any time and not 200, we failed, regardless of the mode.
        # Check this before the function result, so we get the status code.
        if response.StatusCode is not None and response.StatusCode != 200:
            self.Logger.error(f"Sage Speak failed with status code {response.StatusCode}")
            return False

        # If we failed, return false.
        if result is False:
            return False

        return True


    # Takes a chat json object and returns the assistant's response text.
    # Returns None on failure.
    async def Chat(self, requestJson:str, homeContext_CanBeNone:CompressionResult, states_CanBeNone:CompressionResult) -> bytearray:

        # Our data type is a json string.
        def createDataContextOffset(builder:octoflatbuffers.Builder) -> int:
            return self._CreateDataContext(builder, SageDataTypesFormats.Json, homeContext=homeContext_CanBeNone, states=states_CanBeNone)

        # We expect the onDataStreamReceived handler to be called once, with the full response.
        class ResponseContext:
            Bytes = None
            StatusCode = None
        response:ResponseContext = ResponseContext()
        async def onDataStreamReceived(statusCode:int, data:bytearray, dataContext:SageDataContext, isFinalDataChunk:bool):
            # For Chat, this should only be called once
            if response.StatusCode is not None:
                raise Exception("Sage Chat onDataStreamReceived called more than once.")

            # Check for a failure, which can happen at anytime.
            # If we have anything but a 200, stop processing now.
            response.StatusCode = statusCode
            if response.StatusCode != 200:
                return False

            # This data format must be text.
            dataType = dataContext.DataType()
            if dataType != SageDataTypesFormats.Text:
                response.StatusCode = 500
                raise Exception("Sage Chat got a response that wasn't text?")

            # Set the text.
            response.Bytes = data
            return True

        # Encode the input json string.
        data = requestJson.encode("utf-8")

        # Do the operation, wait for the result.
        result = await self._SendAndReceive(SageOperationTypes.Chat, data, createDataContextOffset, onDataStreamReceived, True)

        # If the status code is set at any time and not 200, we failed.
        # Check this before the function result, so we get the status code.
        if response.StatusCode is not None and response.StatusCode != 200:
            # In some special cases, we want to map the status code to a user message.
            userError = self._MapErrorStatusCodeToUserStr(response.StatusCode)
            if userError is not None:
                return userError
            self.Logger.error(f"Sage Chat failed with status code {response.StatusCode}")
            return None

        # If we failed, we always return None.
        if result is False:
            return None

        # Decode the text response
        return response.Bytes.decode("utf-8")


    # A helper function that allows us to send messages for many different types of actions.
    # Returns true on success, false on failure.
    # Note the behavior of isTransmissionDone:
    #   If isTransmissionDone set to False. This will cause a stream to be opened and multiple upload data messages to be sent. This function will NOT block to wait for a response, since more data needs to be sent first.
    #   When it's called and isTransmissionDone is set to True, the final data will be sent and the function will block until a response is received.
    # The onDataReceivedCallback can be called many times if the response is being streamed.
    # The onDataReceivedCallback can also be called on any call into this function with a status code, if the stream closed early for some reason.
    async def _SendAndReceive(self,
                        requestType:SageOperationTypes,
                        sendData:bytearray,
                        dataContextCreateCallback,
                        onDataStreamReceivedCallbackAsync,
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

            # Send the message
            if self._SendStreamMessage(builder, msgOffset) is False:
                self.Logger.error("Sage Fabric failed to send a message.")
                return False

            # We know the message was sent, so set the open message flag.
            # This is used to determine if we need to send a close message if the stream is closed early.
            context.HasSentOpenMessage = True

            # Now, if the upload data transmission isn't done, return, which will allow more data to be sent.
            if isTransmissionDone is False:
                # If we are doing the upload stream, check that the stream wasn't aborted from the server side before returning.
                if context.StatusCode is not None or context.IsAborted:
                    # If the stream was aborted, fire the callback and return False
                    await onDataStreamReceivedCallbackAsync(context.StatusCode, bytearray(), None, True)
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

                # Check for a stream abort, before we check if there's no data.
                if context.IsAborted:
                    self.Logger.error(f"Sage message stream was aborted: {context.StatusCode}")
                    await onDataStreamReceivedCallbackAsync(context.StatusCode, bytearray(), None, True)
                    return False

                # Regardless of the other vars, if we didn't get any data, the response wait timed out.
                if len(data) == 0:
                    self.Logger.error("Sage message timed out while waiting for a response.")
                    context.StatusCode = 608
                    await onDataStreamReceivedCallbackAsync(context.StatusCode, bytearray(), None, True)
                    return False

                # Process the data
                if dataContext is None or statusCode is None or data is None:
                    raise Exception("Sage Listen got a response that was missing the data context or the status code.")

                # Process the data.
                # Note that the final message can have a buffer or might be empty. In that case the last data will be an empty bytearray().
                count = 0
                for d in data:
                    count += 1
                    isLastChunk = isDataDownloadComplete and count == len(data)
                    if await onDataStreamReceivedCallbackAsync(statusCode, d, dataContext, isLastChunk) is False:
                        return False

                # If we processed all the data and the stream is done, we're done.
                if isDataDownloadComplete:
                    return True

        except Exception as e:
            Sentry.Exception("Sage message pipeline exception", e)
        finally:
            # If we don't want to keep the context around, clean it up now.
            if cleanUpContextOnExit:
                self._CleanUpStreamContext(context.StreamId)
        return False


    # Returns the offset of the message in the buffer.
    def _CreateStreamMessage(self, builder:octoflatbuffers.Builder, streamId:int, msgType:SageOperationTypes, data:bytearray,
                             dataContextOffset:int=None, statusCode:int=None, isOpen:bool=None, isAbort:bool=None, isTransmissionDone:bool=None) -> int:
        # Create any buffers we need.
        dataOffset = None
        if data is not None:
            dataOffset = builder.CreateByteVector(data)

        # Build the message.
        SageStreamMessage.Start(builder)
        SageStreamMessage.AddStreamId(builder, streamId)
        SageStreamMessage.AddType(builder, msgType)
        if statusCode is not None:
            SageStreamMessage.AddStatusCode(builder, statusCode)
        if isOpen is not None:
            SageStreamMessage.AddIsOpenMsg(builder, isOpen)
        if isAbort is not None:
            SageStreamMessage.AddIsAbortMsg(builder, isAbort)
        if isTransmissionDone is not None:
            SageStreamMessage.AddIsDataTransmissionDone(builder, isTransmissionDone)
        if dataContextOffset is not None:
            SageStreamMessage.AddDataContext(builder, dataContextOffset)
        if dataOffset is not None:
            SageStreamMessage.AddData(builder, dataOffset)
        return SageStreamMessage.End(builder)


    # Builds the data context.
    def _CreateDataContext(self, builder:octoflatbuffers.Builder, dataFormat:SageDataTypesFormats,
                           sampleRate:int=None, channels:int=None, bytesPerSample:int=None, languageCode:str=None,
                           homeContext:CompressionResult=None, states:CompressionResult=None) -> int:
        homeContextBytesOffset = None
        if homeContext is not None:
            homeContextBytesOffset = builder.CreateByteVector(homeContext.Bytes)
        satesBytesOffset = None
        if states is not None:
            satesBytesOffset = builder.CreateByteVector(states.Bytes)

        languageCodeOffset = None
        if languageCode is not None:
            languageCodeOffset = builder.CreateString(languageCode)

        SageDataContext.Start(builder)
        SageDataContext.AddDataType(builder, dataFormat)
        if sampleRate is not None:
            SageDataContext.AddSampleRate(builder, sampleRate)
        if channels is not None:
            SageDataContext.AddChannels(builder, channels)
        if bytesPerSample is not None:
            SageDataContext.AddBytesPerSample(builder, bytesPerSample)
        if languageCodeOffset is not None:
            SageDataContext.AddLanguageCode(builder, languageCodeOffset)
        if homeContextBytesOffset is not None:
            SageDataContext.AddHomeContext(builder, homeContextBytesOffset)
            SageDataContext.AddHomeContextCompression(builder, homeContext.CompressionType)
            SageDataContext.AddHomeContextOriginalDataSize(builder, homeContext.UncompressedSize)
        if satesBytesOffset is not None:
            SageDataContext.AddStates(builder, satesBytesOffset)
            SageDataContext.AddStatesCompression(builder, states.CompressionType)
            SageDataContext.AddStatesOriginalDataSize(builder, states.UncompressedSize)
        return SageDataContext.End(builder)


    # Sends a stream message
    # Can throw on unexpect failures.
    def _SendStreamMessage(self, builder:octoflatbuffers.Builder, streamMessageOffset:int) -> bool:
        # Build the fiber
        SageFiber.Start(builder)
        SageFiber.AddMessage(builder, streamMessageOffset)
        streamMsgOffset = SageFiber.End(builder)

        # Finalize the builder
        builder.FinishSizePrefixed(streamMsgOffset)
        buffer = builder.Bytes
        bufferStartOffsetBytes = builder.Head()
        bufferLen = len(buffer) - bufferStartOffsetBytes

        # Send it!
        return self.Fabric.SendMsg(buffer, bufferStartOffsetBytes, bufferLen)


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

            # We don't need to send an abort message if...
            #    We never sent a open message.
            #    The server already finished the data response.
            #    We already sent or received an abort message.
            if context.HasSentOpenMessage is False or context.IsDataDownloadComplete is True or context.IsAborted is True:
                return
            context.IsAborted = True

            # Build the abort message.
            # The abort message must have a status code set.
            builder = octoflatbuffers.Builder(50)
            msgOffset = self._CreateStreamMessage(builder, context.StreamId, context.RequestType, bytearray(), statusCode=400, isAbort=True)

            # Send it!
            if self._SendStreamMessage(builder, msgOffset) is False:
                # Only do a warning, because this might have failed bc the socket is closed.
                self.Logger.info("Failed to send Sage abort message.")

        except Exception as e:
            # If we fail, reset the socket.
            Sentry.Exception("Sage _CleanUpStreamContext exception", e)
            self.Fabric.Close()


    # Called from the socket when a message is received
    def OnIncomingMessage(self, buf:bytearray):
        # First, read the message size. Add 4 to account for the full buffer size, including the uint32.
        messageSize = self.Unpack32Int(buf, 0) + 4

        # Check that things make sense.
        if messageSize != len(buf):
            raise Exception("Sage Fabric got a ws message that's not the correct size! MsgSize:"+str(messageSize)+"; BufferLen:"+str(len(buf)))

        # Parse the response
        fiber = SageFiber.SageFiber.GetRootAs(buf, 4)
        msg = fiber.Message()
        if msg is None:
            raise Exception("Sage Fiber message is None.")

        # Always required.
        streamId = msg.StreamId()
        if streamId is None or streamId <= 0:
            raise Exception(f"Sage Fiber message has an invalid stream id. {streamId}")

        # Find the context and set the response.
        with self.StreamContextLock:
            context = self.StreamContextMap.get(streamId, None)
            if context is None:
                # If there's no context this is usually ok, it means the stream was closed and this is old.
                self.Logger.debug(f"Sage got a message for stream [{streamId}] but couldn't find the context.")
                return

            # The abort message doesn't require the other checks.
            if msg.IsAbortMsg():
                # The abort message should always have a non-success status code.
                statusCode = msg.StatusCode()
                if statusCode is None or statusCode < 300:
                    self.Logger.warning(f"Sage Fiber abort message has an invalid status code. {statusCode} Updating to 600.")
                    statusCode = 600
                context.StatusCode = statusCode
                context.IsAborted = True
            else:
                # This is a standard message.

                # DataAsByteArray will return the int 0 if there's no data, so check the length first.
                # We always want the data to be a valid bytearray object, so we just make an empty one.
                data:bytearray = None
                if msg.DataLength() > 0:
                    data = msg.DataAsByteArray()
                else:
                    data = bytearray()
                isDataTransmissionDone = msg.IsDataTransmissionDone()

                # There needs to be a data buffer unless this is the final message, then it's optional.
                if len(data) == 0 and isDataTransmissionDone is False:
                    raise Exception("Sage Fiber message was missing a data buffer")

                # Ensure we haven't already gotten the data complete flag.
                if context.IsDataDownloadComplete:
                    raise Exception("Sage ws message was sent after IsDataDownloadComplete was set.")
                if isDataTransmissionDone:
                    context.IsDataDownloadComplete = True

                if context.StatusCode is None:
                    # This is the first (and possibly only) response.
                    # The status code is always required.
                    statusCode = msg.StatusCode()
                    if statusCode is None or statusCode <= 0:
                        raise Exception(f"Sage Fiber message has an invalid status code. {statusCode}")
                    context.StatusCode = statusCode

                    # The data context is always required as well, unless it's a close message.
                    dataContext = msg.DataContext()
                    if dataContext is None and isDataTransmissionDone is False:
                        raise Exception("Sage Fiber message has is missing the data context.")
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


    # Some status codes we want to map and then send a message to the user.
    # Returns None if there's no error to map.
    def _MapErrorStatusCodeToUserStr(self, statusCode:int) -> str:
        linkErrorMessage = "You must link your Homeway addon with your Homeway account before using Sage! https://homeway.io/s/link"
        if statusCode is None:
            return None
        if statusCode == 401:
            self.Logger.warning(linkErrorMessage)
            return linkErrorMessage
        return None


# Holds the context of the result for the speak function.
class SpeakDataResponse:
    def __init__(self, data:bytearray, dataType:SageDataTypesFormats, sampleRate:int, channels:int, bytesPerSample:int, isFinalDataChunk:bool):
        self.Bytes = data
        self.DataFormat = dataType
        self.SampleRate = sampleRate
        self.Channels = channels
        self.BytesPerSample = bytesPerSample
        self.IsFinalDataChunk = isFinalDataChunk


# Used to track the current state of a stream.
class StreamContext:

    def __init__(self, streamId:int, requestType:SageOperationTypes):
        # Core properties.
        self.StreamId = streamId
        self.RequestType = requestType

        # State flags
        self.IsDataTransmissionDone = False
        self.HasSentOpenMessage = False
        self.IsAborted = False

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


# Returned from the listen function, see the function def for more details.
class ListenResult:
    @staticmethod
    def Success(resultText:str=None) -> "ListenResult":
        return ListenResult(resultText=resultText)

    @staticmethod
    def Failure(errorStr:str) -> "ListenResult":
        return ListenResult(errorStr=errorStr)

    def __init__(self, resultText:str = None, errorStr:str = None):
        self.Result = resultText
        self.Error = errorStr
