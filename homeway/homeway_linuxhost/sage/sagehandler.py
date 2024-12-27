import logging
import time
import math

from wyoming.asr import Transcribe, Transcript
from wyoming.tts import Synthesize
from wyoming.audio import AudioChunk, AudioStop
from wyoming.event import Event
from wyoming.handle import Handled
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.audio import AudioChunk, AudioStart, AudioStop

from homeway.httpsessions import HttpSessions
from .fabric import Fabric


class SageHandler(AsyncEventHandler):

    def __init__(self, info: Info, logger:logging.Logger, fabric: Fabric, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.Logger = logger
        self.InfoEvent = info.event()
        self.Fabric = fabric
        self.IncomingAudioBuffer = bytearray()


    # The main event handler for all wyoming events.
    # Returning False will disconnect the client.
    async def handle_event(self, event: Event) -> bool:
        self.Logger.debug(f"Wyoming event: {event.type}")

        # Fired when the server is first connected to and the client wants to know what models are available.
        if Describe.is_type(event.type):
            await self.write_event(self.InfoEvent)
            return True

        if AudioStart.is_type(event.type):
            self.IncomingAudioBuffer = bytearray()
            return True
        if AudioChunk.is_type(event.type):
            e = AudioChunk.from_event(event)
            self.IncomingAudioBuffer.extend(AudioChunk.from_event(event).audio)
            return True
        if AudioStop.is_type(event.type):
            start = time.time()
            text = self.Fabric.Listen(self.IncomingAudioBuffer)
            self.Logger.warn(f"Sage WS Listen End - {text} - time: {time.time() - start}")
            await self.write_event(Transcript(text=text).event())
            return True

        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            return True

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
            # text = " ".join(transcript.text.strip().splitlines())

            self.Logger.debug(f"Sage - Synthesize Start - {text}")

            start = time.time()
            url = "https://homeway.io/api/sage/speak"
            response = HttpSessions.GetSession(url).post(url, json={"Text": text}, timeout=120)

            # Compute the audio values.
            data = response.content
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