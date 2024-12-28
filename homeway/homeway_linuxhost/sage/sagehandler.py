import logging
import time
import math

from wyoming.asr import Transcript
from wyoming.tts import Synthesize
from wyoming.audio import AudioChunk, AudioStop
from wyoming.event import Event
from wyoming.handle import Handled
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.audio import AudioChunk, AudioStart, AudioStop

from homeway.Proto.SageDataTypesFormats import SageDataTypesFormats

from .fabric import Fabric
from .fibermanager import FiberManager


class SageHandler(AsyncEventHandler):

    def __init__(self, info: Info, logger:logging.Logger, fabric: Fabric, fiberManger:FiberManager, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.Logger = logger
        self.InfoEvent = info.event()
        self.Fabric = fabric
        self.FiberManager = fiberManger

        #
        # These are used to manage the streaming audio for the Listen action.
        # These are all reset on every new AudioStart.
        #

        # If set to true, this stream has failed, and all future requests should be ignored
        # until the next audio stream is started.
        self.IncomingAudioBufferHasFailed:bool = False

        # This holds the incoming audio buffer that is being streamed to the server.
        # If it's none, that means we haven't gotten any chunks since the last start.
        self.IncomingAudioBuffer:bytearray = None

        # This is the last time we sent the buffered audio.
        self.IncomingAudioBufferLastSentSec:float = None

        # This holds the start event for the incoming audio stream.
        self.IncomingAudioStartEvent:AudioStart = None
        self._ResetIncomingAudioBuffers()


    def _ResetIncomingAudioBuffers(self):
        self.IncomingAudioBufferHasFailed = False
        self.IncomingAudioBuffer = None
        self.IncomingAudioStartEvent = None
        self.IncomingAudioBufferLastSentSec = None


    # The main event handler for all wyoming events.
    # Returning False will disconnect the client.
    async def handle_event(self, event: Event) -> bool:
        self.Logger.debug(f"Wyoming event: {event.type}")

        # Fired when the server is first connected to and the client wants to know what models are available.
        if Describe.is_type(event.type):
            await self.write_event(self.InfoEvent)
            return True

        # All speech to text audio is handled by this function.
        if AudioStart.is_type(event.type) or AudioChunk.is_type(event.type) or AudioStop.is_type(event.type):
            return await self._HandleStreamingAudio(event)

        # Fired when there's a input phrase from the user that the client wants to run the model on.
        if Transcript.is_type(event.type):
            transcript = Transcript.from_event(event)
            self.Logger.info(f"Transcript: {transcript.text}")
            await self.write_event(Handled("Are the office lights on?").event())
            return True

        # Fired when the client wants to synthesize a voice for the given text.
        if Synthesize.is_type(event.type):
            transcript = Synthesize.from_event(event)

            # Ensure all of the text is joined on one line.
            text = " ".join(transcript.text.strip().splitlines())

            self.Logger.debug(f"Sage - Synthesize Start - {text}")

            start = time.time()
            bytes = self.FiberManager.Speak(text)

            # url = "https://homeway.io/api/sage/speak"
            # response = HttpSessions.GetSession(url).post(url, json={"Text": text}, timeout=120)

            # Compute the audio values.
            data = bytes
            rate = 24000
            width = 2
            channels = 1
            bytesPerSample = width * channels
            bytesPerChunk = bytesPerSample * 1024
            chunks = int(math.ceil(len(data) / bytesPerChunk))

            # Start the response.
            await self.write_event(AudioStart(rate=rate, width=width, channels=channels).event())

            # Write the audio chunks.
            for i in range(chunks):
                offset = i * bytesPerChunk
                chunk = data[offset : offset + bytesPerChunk]
                await self.write_event(AudioChunk(audio=chunk, rate=rate, width=width, channels=channels).event())

            # Write the end event.
            await self.write_event(AudioStop().event())
            self.Logger.warn(f"Sage Synthesize End - {text} - time: {time.time() - start}")
            return True


        # For all other events, return True.
        # Returning False will disconnect the client.
        return True


    # Handles all streaming audio for speech to text.
    async def _HandleStreamingAudio(self, event: Event) -> bool:

        # TODO - On failure, can we write an error back?

        # Called when audio is about to be streamed to the server.
        if AudioStart.is_type(event.type):
            # Ensure the listen action stream is reset.
            self.FiberManager.ResetListen()
            self._ResetIncomingAudioBuffers()
            # Capture the audio event so we have the info.
            self.IncomingAudioStartEvent = AudioStart.from_event(event)
            return True

        # Called when audio is being streamed to the server.
        if AudioChunk.is_type(event.type):
            e = AudioChunk.from_event(event)
            if e.audio is None or len(e.audio) == 0:
                self.Logger.warning("Sage received an empty audio chunk.")
                return True

            # If we failed to handle this stream during the audio stream, we should ignore all future requests until we reset.
            if self.IncomingAudioBufferHasFailed:
                return True

            # If this is the start of a new buffer, create the buffer now and start the timer.
            if self.IncomingAudioBuffer is None:
                self.IncomingAudioBuffer = bytearray(e.audio)
                self.IncomingAudioBufferLastSentSec = time.time()
                return True

            # Append to the current buffer.
            self.IncomingAudioBuffer.extend(e.audio)

            # Get get audio about every 5ms, so we build it up some before sending.
            timeSinceLastSendSec = time.time() - self.IncomingAudioBufferLastSentSec
            if timeSinceLastSendSec > 0.200: # 200ms
                # This will not block on a response, it will just send the audio.
                start = time.time()
                result = self.FiberManager.Listen(False, self.IncomingAudioBuffer, SageDataTypesFormats.AudioPCM, self.IncomingAudioStartEvent.rate, self.IncomingAudioStartEvent.channels, self.IncomingAudioStartEvent.width)
                deltaSec = time.time() - start
                self.Logger.warning(f"Sage Listen Stream Sent. Time: {deltaSec}s")

                # If None is returned, the stream failed.
                if result is None:
                    self.Logger.error("Sage Listen audio stream failed while streaming the upload.")
                    self.IncomingAudioBufferHasFailed = True

                # Warn if the time is taking too long.
                if deltaSec > 0.02:
                    self.Logger.warning(f"Sage Listen audio stream took more than {deltaSec}s to send.")

                # Reset the buffer and last send time.
                self.IncomingAudioBuffer = bytearray()
                self.IncomingAudioBufferLastSentSec = time.time()

            return True

        # Fired when the audio streaming is done.
        if AudioStop.is_type(event.type):
            # Ensure we have something to send. If this is None, we never got any audio chunks.
            # This happens sometimes and is fine.
            if self.IncomingAudioBuffer is None:
                return True

            # If we failed to handle this stream during the audio stream, we should ignore all future requests until we reset.
            if self.IncomingAudioBufferHasFailed:
                return True

            # Send the final audio chunk indicating that the audio stream is done.
            # This will now block and wait for a response.
            # Note that this incoming audio buffer can be empty if we don't have any buffered audio, which is fine.
            start = time.time()
            text = self.FiberManager.Listen(True, self.IncomingAudioBuffer, SageDataTypesFormats.AudioPCM, self.IncomingAudioStartEvent.rate, self.IncomingAudioStartEvent.channels, self.IncomingAudioStartEvent.width)

            # Check for a failure.
            if text is None:
                self.IncomingAudioBufferHasFailed = True
                return True

            # Send the text back to the client.
            self.Logger.info(f"Sage Listen End - {text} - latency: {time.time() - start}s")
            await self.write_event(Transcript(text=text).event())
            return True
