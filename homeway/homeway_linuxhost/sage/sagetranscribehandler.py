import time
import logging

from wyoming.event import Event
from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop

from homeway.Proto.SageDataTypesFormats import SageDataTypesFormats

from .fibermanager import FiberManager


# Handles holding the context of the streaming incoming audio stream for transcribe.
# This class can only be used once per transcribe request.
class SageTranscribeHandler:

    # The min amount of time we will buffer up audio before streaming it to the server as a chunk.
    # Right now, it's 200ms.
    c_MinChunkTimeSec = 0.200

    # The max amount of time we will stream audio to the server before stopping.
    # This is also enforced on the server side.
    c_MaxStreamTimeSec = 20.0

    def __init__(self, logger:logging.Logger, sageHandler, fiberManager:FiberManager, transcribeLanguage_CanBeNone:str, startAudioEvent:Event):
        self.Logger = logger
        self.SageHandler = sageHandler
        self.FiberManager = fiberManager
        self.TranscribeLanguage_CanBeNone = transcribeLanguage_CanBeNone

        self.Logger.debug(f"Sage - Listen Start - {self.TranscribeLanguage_CanBeNone}")

        # Reset any existing Listen action in the fiber manager.
        self.FiberManager.ResetListen()

        # This holds the start event for the incoming audio stream.
        if AudioStart.is_type(startAudioEvent.type) is False:
            raise ValueError("SageTranscribeHandler must be created with a AudioStart event.")
        self.IncomingAudioStartEvent:AudioStart = AudioStart.from_event(startAudioEvent)

        # If set to true, this stream has failed, and all future requests should be ignored
        # until the next audio stream is started.
        self.HasErrored:bool = False

        # This holds the incoming audio buffer that is being streamed to the server.
        # If it's none, that means we haven't gotten any chunks since the last start.
        self.Buffer:bytearray = None

        # This is the last time we sent the buffered audio.
        self.LastSentTimeSec:float = None

        # Used to track the start time of the audio stream and to see if there was any audio stream at all.
        self.AudioStreamStartTimeSec:float = None


    # Logs and error and writes an error message back to the client.
    async def _WriteError(self, text:str, code:str=None) -> None:
        # If we ever write an error back, set this boolean so we stop handing audio for this stream request.
        self.HasErrored = True
        await self.SageHandler.WriteError(text, code)


    # Helper for writing an event back to the client.
    async def _WriteEvent(self, event:Event) -> None:
        await self.SageHandler.write_event(event)


   # Handles all streaming audio for speech to text.
    async def HandleStreamingAudio(self, event: Event) -> bool:
        # If this is set, we failed to handle this stream some time in the past.
        # We should ignore all future requests for this stream.
        if self.HasErrored:
            return True

        # Called when audio is being streamed to the server.
        if AudioChunk.is_type(event.type):
            e = AudioChunk.from_event(event)
            if e.audio is None or len(e.audio) == 0:
                # This would be ok, if it ever happened. We have logic that will detect if we never got any audio.
                self.Logger.debug("Homeway Sage Listen - Received an empty audio chunk - ignoring.")
                return True

            # Set the stream start time and check the max streaming time.
            if self.AudioStreamStartTimeSec is None:
                self.AudioStreamStartTimeSec = time.time()
            else:
                streamTimeSec = time.time() - self.AudioStreamStartTimeSec
                if streamTimeSec > SageTranscribeHandler.c_MaxStreamTimeSec:
                    await self._WriteError(f"Homeway Sage Hit The Audio Stream Time Limit Of {int(SageTranscribeHandler.c_MaxStreamTimeSec)}s")
                    return True

            # If this is the start of a new buffer, create the buffer now and start the timer.
            if self.Buffer is None:
                self.Buffer = bytearray(e.audio)
                self.LastSentTimeSec = time.time()
                return True

            # Append to the current buffer.
            self.Buffer.extend(e.audio)

            # Get get audio about every 5ms, so we build it up some before sending.
            timeSinceLastSendSec = time.time() - self.LastSentTimeSec
            if timeSinceLastSendSec < SageTranscribeHandler.c_MinChunkTimeSec:
                return True

            # This will not block on a response, it will just send the audio.
            start = time.time()
            result = await self.FiberManager.Listen(False, self.Buffer, SageDataTypesFormats.AudioPCM, self.IncomingAudioStartEvent.rate, self.IncomingAudioStartEvent.channels, self.IncomingAudioStartEvent.width, self.TranscribeLanguage_CanBeNone)
            deltaSec = time.time() - start

            # This should never happen.
            if result is None:
                await self._WriteError("Homeway Sage Audio Stream Failed - No Result")
                return True

            # If the error text is set, we failed to send the audio.
            # We try to send the error string, since it might help the user.
            if result.Error is not None:
                await self._WriteEvent(Transcript(text=result).event())
                await self._WriteError("Homeway Sage Audio Stream Failed - " + result.Error)
                return True

            # Ensure the operation didn't take too long.
            if deltaSec > 0.020:
                self.Logger.warning(f"Sage Listen upload chunk stream took more than 20ms. Time: {deltaSec}s")

            # Reset the buffer and last send time.
            self.Buffer = bytearray()
            self.LastSentTimeSec = time.time()
            return True

        # Fired when the audio streaming is done.
        if AudioStop.is_type(event.type):
            # Ensure we have something to send. If this is None, we never got any audio chunks.
            # This happens sometimes and is fine, we just return an empty string.
            if self.AudioStreamStartTimeSec is None:
                self.Logger.debug("Sage Listen - We never got any audio chunks, returning an empty transcript.")
                await self._WriteEvent(Transcript("").event())
                return True

            # Send the final audio chunk indicating that the audio stream is done.
            # This will now block and wait for a response.
            # Note that this incoming audio buffer can be empty if we don't have any buffered audio, which is fine.
            start = time.time()
            result = await self.FiberManager.Listen(True, self.Buffer, SageDataTypesFormats.AudioPCM, self.IncomingAudioStartEvent.rate, self.IncomingAudioStartEvent.channels, self.IncomingAudioStartEvent.width, self.TranscribeLanguage_CanBeNone)

            # This should never happen.
            if result is None:
                await self._WriteError("Homeway Sage Listen - No Result Returned.")
                return True

            # If the error text is set, we failed to send the audio.
            # We try to send the error string, since it might help the user.
            if result.Error is not None:
                await self._WriteEvent(Transcript(text=result).event())
                await self._WriteError("Homeway Sage Audio Stream Failed - " + result.Error)
                return True

            # This shouldn't happen.
            if result.Result is None:
                await self._WriteError("Homeway Sage Listen - No Result Returned.")
                return True

            # Send the text back to the client.
            text = result.Result
            self.Logger.debug(f"Sage Listen End - `{text}` - latency: {time.time() - start}s")
            await self._WriteEvent(Transcript(text=text).event())
            return True

        # This event should not be here.
        self.Logger.warning(f"Sage SageTranscribeHandler should not be getting {event.type} events.")
        return True
