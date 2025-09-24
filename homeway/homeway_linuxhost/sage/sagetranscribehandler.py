import time
import logging
from typing import Optional

from wyoming.event import Event
from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop

from homeway.Proto.SageDataTypesFormats import SageDataTypesFormats
from homeway.buffer import Buffer

from .fibermanager import FiberManager
from .interfaces import ISageHandler


# Handles holding the context of the streaming incoming audio stream for transcribe.
# This class can only be used once per transcribe request.
class SageTranscribeHandler:

    # The min amount of time we will buffer up audio before streaming it to the server as a chunk.
    # Right now, it's 200ms.
    c_MinChunkTimeSec = 0.200

    # The max amount of time we will stream audio to the server before stopping.
    # This is also enforced on the server side.
    c_MaxStreamTimeSec = 20.0


    def __init__(self, logger:logging.Logger, sageHandler:ISageHandler, fiberManager:FiberManager, transcribeLanguage:Optional[str], startAudioEvent:Event):
        self.Logger = logger
        self.SageHandler = sageHandler
        self.FiberManager = fiberManager
        self.TranscribeLanguage = transcribeLanguage

        self.Logger.debug(f"Sage - Listen Start - {self.TranscribeLanguage}")

        # Reset any existing Listen action in the fiber manager.
        self.FiberManager.ResetListen()

        # This holds the start event for the incoming audio stream.
        if AudioStart.is_type(startAudioEvent.type) is False:
            raise ValueError("SageTranscribeHandler must be created with a AudioStart event.")
        self.IncomingAudioStartEvent:AudioStart = AudioStart.from_event(startAudioEvent)

        # If this is not None, we have hit an error and can ignore the rest of this listen stream.
        # But due to the way the protocol works, we still need to handle the rest of the stream and send the error at the end as the result.
        self.ErrorMessage:Optional[str] = None

        # This holds the incoming audio buffer that is being streamed to the server.
        # If it's none, that means we haven't gotten any chunks since the last start.
        self.DataBuffer:Optional[bytearray] = None

        # This is the last time we sent the buffered audio.
        self.LastSentTimeSec:Optional[float] = None

        # Used to track the start time of the audio stream and to see if there was any audio stream at all.
        self.AudioStreamStartTimeSec:Optional[float] = None


    # Logs and error and writes an error message back to the client.
    def _SetError(self, text:str) -> str:
        # If we ever write an error back, set this boolean so we stop handing audio for this stream request.
        self.Logger.warning(f"Sage Listen Failed - {text}")
        self.ErrorMessage = text
        return text


    # Helper for writing an event back to the client.
    async def _WriteEvent(self, event:Event) -> None:
        await self.SageHandler.write_event(event)


   # Handles all streaming audio for speech to text.
    async def HandleStreamingAudio(self, event:Event) -> bool:

        # Called when audio is being streamed to the server.
        if AudioChunk.is_type(event.type):

            # If we have errored and this is more stream, we just ignore it until the stream ends.
            if self.ErrorMessage is not None:
                return True

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
                    self._SetError(f"You hit the max speech-to-text time limit of {int(SageTranscribeHandler.c_MaxStreamTimeSec)} seconds.")
                    return True

            # If this is the start of a new buffer, create the buffer now and start the timer.
            if self.DataBuffer is None:
                self.DataBuffer = bytearray(e.audio)
                self.LastSentTimeSec = time.time()
                return True

            # This should never happen, but if it does, log it and reset the timer.
            if self.LastSentTimeSec is None:
                self.Logger.error("Sage Listen - LastSentTimeSec is None when DataBuffer is not None. This should never happen.")
                self.LastSentTimeSec = time.time()

            # Append to the current buffer.
            self.DataBuffer.extend(e.audio)

            # Get get audio about every 5ms, so we build it up some before sending.
            timeSinceLastSendSec = time.time() - self.LastSentTimeSec
            if timeSinceLastSendSec < SageTranscribeHandler.c_MinChunkTimeSec:
                return True

            # This will not block on a response, it will just send the audio.
            start = time.time()
            result = await self.FiberManager.Listen(False, Buffer(self.DataBuffer), SageDataTypesFormats.AudioPCM, self.IncomingAudioStartEvent.rate,
                                                     self.IncomingAudioStartEvent.channels, self.IncomingAudioStartEvent.width, self.TranscribeLanguage)
            deltaSec = time.time() - start

            # Before anything else, reset the buffer and last send time.
            self.DataBuffer = bytearray()
            self.LastSentTimeSec = time.time()

            # This should never happen.
            if result is None:
                self._SetError("Homeway Sage Audio Stream Failed - No Result")
                return True

            # If the error text is set, we failed to send the audio.
            # We try to send the error string, since it might help the user.
            if result.Error is not None:
                self._SetError(result.Error)
                return True

            # Ensure the operation didn't take too long.
            if deltaSec > 0.020:
                self.Logger.warning(f"Sage Listen upload chunk stream took more than 20ms. Time: {deltaSec}s")

            # Return true to indicate we handled the event.
            return True

        # Fired when the audio streaming is done.
        if AudioStop.is_type(event.type):
            # Ensure we have something to send. If this is None, we never got any audio chunks.
            # This happens sometimes and is fine, we just return an empty string.
            if self.AudioStreamStartTimeSec is None:
                self.Logger.debug("Sage Listen - We never got any audio chunks, returning an empty transcript.")
                await self._WriteEvent(Transcript("").event())
                return True

            # If we have an error that ended the stream early, return it now.
            # Note we can't write this as any kind of error or it gets lost, so we write it as a result.
            if self.ErrorMessage is not None:
                await self._WriteEvent(Transcript(self.ErrorMessage).event())
                return True

            # If we got any data, the data buffer will not be None.
            # So if it is None, we never streamed anything.
            if self.DataBuffer is None:
                self.Logger.info("Sage Listen - We never got any audio chunks, returning an empty transcript.")
                await self._WriteEvent(Transcript(text="").event())
                return True

            # Send the final audio chunk indicating that the audio stream is done.
            # This will now block and wait for a response.
            # Note that this incoming audio buffer can be empty if we don't have any buffered audio, which is fine.
            start = time.time()
            result = await self.FiberManager.Listen(True, Buffer(self.DataBuffer), SageDataTypesFormats.AudioPCM,
                                                    self.IncomingAudioStartEvent.rate, self.IncomingAudioStartEvent.channels,
                                                    self.IncomingAudioStartEvent.width, self.TranscribeLanguage)

            # This should be the final run, so we can set the buffer to None.
            self.DataBuffer = None

            # This should never happen.
            if result is None:
                err = self._SetError("Homeway Sage Listen - No Result Returned.")
                await self._WriteEvent(Transcript(err).event())
                return True

            # If the error text is set, we failed to send the audio.
            # We try to send the error string, since it might help the user.
            if result.Error is not None:
                err = self._SetError(result.Error)
                await self._WriteEvent(Transcript(err).event())
                return True

            # This shouldn't happen.
            if result.Result is None:
                err = self._SetError("Homeway Sage Listen - No Result Returned")
                await self._WriteEvent(Transcript(err).event())
                return True

            # Send the text back to the client.
            text = result.Result
            self.Logger.debug(f"Sage Listen End - `{text}` - latency: {time.time() - start}s")
            await self._WriteEvent(Transcript(text=text).event())
            return True

        # This event should not be here.
        self.Logger.warning(f"Sage SageTranscribeHandler should not be getting {event.type} events.")
        return True
