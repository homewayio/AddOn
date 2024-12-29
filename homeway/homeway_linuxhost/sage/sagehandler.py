import time
import json
import logging
import requests

from wyoming.asr import Transcript
from wyoming.tts import Synthesize
from wyoming.error import Error
from wyoming.event import Event
from wyoming.handle import Handled
from wyoming.server import AsyncEventHandler
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.info import Describe, AsrModel, AsrProgram, Attribution, Info, TtsProgram, TtsVoice, TtsVoiceSpeaker, HandleProgram,HandleModel, IntentProgram, IntentModel

from homeway.sentry import Sentry

from .fabric import Fabric
from .sagehistory import SageHistory
from .fibermanager import FiberManager, SpeakDataResponse
from .sagetranscribehandler import SageTranscribeHandler


class SageHandler(AsyncEventHandler):

    # This is the max length of any string we will process, be it for transcribing, synthesizing, or anything else.
    # This is more of a sanity check, the server will enforce it's own limits.
    c_MaxStringLength = 500


    # Note this handler is created for each new request flow, like for a full Listen session.
    def __init__(self, logger:logging.Logger, fabric:Fabric, fiberManger:FiberManager, sageHistory:SageHistory, devLocalHomewayServerAddress_CanBeNone:str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.Logger = logger
        self.Fabric = fabric
        self.FiberManager = fiberManger
        self.SageHistory = sageHistory
        self.DevLocalHomewayServerAddress_CanBeNone = devLocalHomewayServerAddress_CanBeNone

        # This is created when the first audio stream starts and is reset when the stream ends.
        # It handles holding the context of the incoming audio stream for transcribing.
        self.SageTranscribeHandler:SageTranscribeHandler = None


    # Logs and error and writes an error message back to the client.
    async def WriteError(self, text:str, code:str=None) -> None:
        self.Logger.error(f"Homeway Sage Error: {text}")
        await self.write_event(Error(text, code).event())


    # The main event handler for all wyoming events.
    # Returning False will disconnect the client.
    async def handle_event(self, event: Event) -> bool:

        # Fired when the server is first connected to and the client wants to know what models are available.
        if Describe.is_type(event.type):
            return await self._HandleDescribe()

        # All speech to text audio is handled by this function.
        if AudioStart.is_type(event.type) or AudioChunk.is_type(event.type) or AudioStop.is_type(event.type):
            # On start, we always re-create the transcribe handler.
            if AudioStart.is_type(event.type):
                self.SageTranscribeHandler = SageTranscribeHandler(self.Logger, self, self.FiberManager, event)
                return True

            # We should always have a handler now.
            if self.SageTranscribeHandler is None:
                self.WriteError("Homeway Sage received an audio chunk or stop before the audio start event.")
                return False

            # Let the handler handle the event.
            return await self.SageTranscribeHandler.HandleStreamingAudio(event)

        # Fired when there's a input phrase from the user that the client wants to run the model on.
        if Transcript.is_type(event.type):
            transcript = Transcript.from_event(event)

            # Add the user text to the history, making it the most recent message.
            newMsg = self._EnforceMaxStringLength(transcript.text)
            self.Logger.debug(f"Sage - Transcript Start - {newMsg}")
            self.SageHistory.AddUserText(newMsg)

            # Get the history as a json object.
            msgJsonStr = json.dumps(self.SageHistory.GetHistoryJsonObj())

            # Make the request.
            start = time.time()
            responseText = await self.FiberManager.Transcript(msgJsonStr)

            # Check the result
            if responseText is None:
                await self.WriteError("Homeway Sage failed to transcribe the text.")
                return True

            # Ensure the response text is not too long.
            responseText = self._EnforceMaxStringLength(responseText)

            # Add the assistant text to the history.
            self.Logger.debug(f"Sage - Transcript End - {newMsg} -> {responseText} - time: {time.time() - start}")
            self.SageHistory.AddAssistantText(responseText)

            # Send the response back to the client.
            await self.write_event(Handled(responseText).event())
            return True

        # Fired when the client wants to synthesize a voice for the given text.
        if Synthesize.is_type(event.type):
            transcript = Synthesize.from_event(event)

            # Ensure all of the text is joined on one line.
            text = " ".join(transcript.text.strip().splitlines())
            text = self._EnforceMaxStringLength(text)

            # Since the Sage Fabric supports streaming, the Synthesize result will some times come back in chunks.
            # We will get multiple callbacks on the streamingDataReceivedCallback function, each with a chunk.
            class SynthContext:
                IsFirstChunk:bool = True
                ChunkCount:int = 0
                TotalBytes:int = 0
            synthContext:SynthContext = SynthContext()
            async def streamingDataReceivedCallback(response:SpeakDataResponse) -> bool:
                # If this is the first chunk, we need to send the audio start event.
                if synthContext.IsFirstChunk:
                    synthContext.IsFirstChunk = False
                    await self.write_event(AudioStart(rate=response.SampleRate, width=response.BytesPerSample, channels=response.Channels).event())

                # Now write the audio chunk.
                await self.write_event(AudioChunk(audio=response.Bytes, rate=response.SampleRate, width=response.BytesPerSample, channels=response.Channels).event())
                synthContext.ChunkCount += 1
                synthContext.TotalBytes += len(response.Bytes)

                # Return true to keep going.
                return True

            self.Logger.debug(f"Sage - Synthesize Start - {text}")
            start = time.time()
            result = await self.FiberManager.Speak(text, streamingDataReceivedCallback)
            self.Logger.debug(f"Sage Synthesize End - {text} - time: {time.time() - start} - chunks: {synthContext.ChunkCount} - bytes: {synthContext.TotalBytes}")

            # If we wrote anything, we need to send the audio stop event.
            if synthContext.IsFirstChunk is False:
                await self.write_event(AudioStop().event())

            # If the result is false, we failed to synthesize the text.
            if result is False:
                await self.WriteError("Homeway Sage failed to synthesize the text.")

            # Always return true to stay connected.
            return True

        # For all other events, return True.
        # Returning False will disconnect the client.
        return True


    # Ensures the text is not too long.
    def _EnforceMaxStringLength(self, text:str) -> str:
        if len(text) < SageHandler.c_MaxStringLength:
            return text
        self.Logger.warning(f"Sage - User input text too long, truncating. Length: {len(text)}")
        return text[:SageHandler.c_MaxStringLength]


    # Queries the service for the models and details we current have to offer.
    async def _HandleDescribe(self) -> bool:

        # Note for some reason the name of the AsrProgram is what will show up in the discovery for users.
        # Get the current programs / models / voices from the service.
        try:
            # Get the correct URL
            url = "https://homeway.io/api/sage/getmodels"
            if self.DevLocalHomewayServerAddress_CanBeNone is not None:
                url = f"http://{self.DevLocalHomewayServerAddress_CanBeNone}/api/sage/getmodels"

            # Since this is important, we have some retry logic.
            attempt = 0
            while True:
                attempt += 1

                # Attempt getting a valid response.
                try:
                    self.Logger.debug(f"Sage - Starting Info Service Request - {url}")
                    response = requests.get(url, timeout=5)
                    if response.status_code == 200:
                        await self._HandleServiceInfoAndWriteEvent(response.json())
                        return
                    self.Logger.warning(f"Sage - Failed to get models from service. Attempt: {attempt} - {response.status_code}")
                except Exception as e:
                    self.Logger.warning(f"Sage - Failed to get models from service. Attempt: {attempt} - {e}")

                # If we fail, try a few times. Throw when we hit the limit.
                if attempt > 3:
                    raise Exception("Failed to get models from service after 3 attempts.")

                # Sleep before trying again.
                time.sleep(5)

        except Exception as e:
            Sentry.Exception("Sage - Failed get info from service.", e)
            await self.WriteError("Homeway Sage failed to get the models info from the service.")
            return False


    # Handles the service info and writes the event back to the client.
    # This must write the info event or throw, so it will retry the process.
    async def _HandleServiceInfoAndWriteEvent(self, response:dict) -> None:

        # Parse the response.
        result = response.get("Result", None)
        if result is None:
            raise Exception("Failed to get models from service, no result.")
        



        models = [
            AsrModel(
                name="Hw Test",
                description="Some model?",
                attribution=Attribution(
                    name="Homeway",
                    url="https://homeway.io/",
                ),
                installed=True,
                languages=["en"],
                version="0.0.1"
            )
        ]

        voices = [
            TtsVoice(
                name="deepgram-sage",
                description="Deepgram Sage",
                attribution=Attribution(
                    name="Homeway",
                            url="https://homeway.io/",
                ),
                installed=True,
                version="0.0.1",
                languages=[
                    "en"
                ],
            )
        ]

        info = Info(
            intent=
            [
                IntentProgram(
                    name="homeway-intent-2",
                    description="test-test",
                    attribution=Attribution(
                        name="Homeway",
                        url="https://homeway.io/",
                    ),
                    installed=True,
                    version="0.0.1",
                    models=[
                        IntentModel(
                            name="homeway-intent-model",
                            description="test",
                            attribution=Attribution(
                                name="Homeway",
                                url="https://homeway.io/",
                            ),
                            installed=True,
                            version="0.0.1",
                            languages=["en"],
                        ),
                         IntentModel(
                            name="homeway-intent-model-gpt",
                            description="tes2",
                            attribution=Attribution(
                                name="Homeway",
                                url="https://homeway.io/",
                            ),
                            installed=True,
                            version="0.0.1",
                            languages=["en"],
                        )
                    ]
                )
            ],
            asr=[
                AsrProgram(
                    name="Homeway Free Speech To Text - OpenAI, Deepgram, Google, etc.",
                    description="Test",
                    attribution=Attribution(
                        name="Homeway",
                        url="https://homeway.io/",
                    ),
                    installed=True,
                    version="0.0.1",
                    models=models,

                )
            ],
            tts=[
                TtsProgram(
                    name="homeway-text-to-speech",
                    description="test",
                    attribution=Attribution(
                        name="Homeway",
                        url="https://homeway.io/",
                    ),
                    installed=True,
                    voices=voices,
                    version="0.0.1",

                )
            ],
           
 
        )

        if info is None:
            
            # TODO error handle?
            return False
        await self.write_event(info.event())
        return True
    

