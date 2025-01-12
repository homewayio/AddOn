import time
import json
import logging
import requests

from wyoming.asr import Transcript, Transcribe
from wyoming.tts import Synthesize
from wyoming.error import Error
from wyoming.event import Event
from wyoming.handle import Handled
from wyoming.server import AsyncEventHandler
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.info import Describe, AsrModel, AsrProgram, Attribution, Info, TtsProgram, TtsVoice, IntentProgram, IntentModel

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

        # Holds the language we were told from transcribe.
        # This can be None if no language is sent.
        self.TranscribeLanguage_CanBeNone:str = None


    # Logs and error and writes an error message back to the client.
    async def WriteError(self, text:str, code:str=None) -> None:
        self.Logger.error(f"Homeway Sage Error: {text}")
        await self.write_event(Error(text, code).event())
        # Calling stop actually makes HA realize the error happened. Otherwise it will keep spinning,
        # even if we return False from handle_event which should disconnect, but it doesn't.
        await self.stop()


    # The main event handler for all wyoming events.
    # Returning False will disconnect the client.
    async def handle_event(self, event: Event) -> bool:

        # Fired when the server is first connected to and the client wants to know what models are available.
        if Describe.is_type(event.type):
            return await self._HandleDescribe()

        # This is sent before the audio stream, the only thing we need from it is the language.
        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            if transcribe.language is not None:
                self.TranscribeLanguage_CanBeNone = transcribe.language
            return True

        # All speech to text audio is handled by this function.
        if AudioChunk.is_type(event.type) or AudioStart.is_type(event.type) or AudioStop.is_type(event.type):
            # On start, we always re-create the transcribe handler.
            if AudioStart.is_type(event.type):
                self.SageTranscribeHandler = SageTranscribeHandler(self.Logger, self, self.FiberManager, self.TranscribeLanguage_CanBeNone, event)
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
            responseText = await self.FiberManager.Chat(msgJsonStr)

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

            # Get the user chosen voice name
            voiceName = None
            if transcript.voice is not None:
                voiceName = transcript.voice.name

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
                # To end the stream, the last message will have the isDone flag set and might include a buffer or it might not.
                # So if we get an empty buffer, we know the stream ended.
                if len(response.Bytes) == 0:
                    if response.IsFinalDataChunk is False:
                        self.Logger.warning("Sage - Synthesize - Got empty buffer but not final chunk.")
                    # Return true to keep going or indicate we ended successfully.
                    return True

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
            result = await self.FiberManager.Speak(text, voiceName, streamingDataReceivedCallback)
            self.Logger.debug(f"Sage Synthesize End - {text} - voice: {voiceName} - time: {time.time() - start} - chunks: {synthContext.ChunkCount} - bytes: {synthContext.TotalBytes}")

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
                        return True
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

        # This is a list of languages we support.
        # We keep them here on the client side so they don't have to be sent every time.
        # Most of our models are multi-lingual, so most of all of these work with any given voice or model.
        # For anything that isn't supported, the server will map it on demand.
        sageLanguages = ["af-ZA", "am-ET", "ar-AE","ar-BH","ar-DZ","ar-EG","ar-IL","ar-IQ","ar-JO","ar-KW","ar-LB","ar-MA","ar-MR","ar-OM","ar-PS","ar-QA","ar-SA","ar-SY","ar-TN","ar-YE","az-AZ","bg-BG","bn-BD","bn-IN","bs-BA","ca-ES","cmn-Hans-CN","cmn-Hans-HK","cmn-Hant-TW","cs-CZ","da-DK","de-AT","de-CH","de-DE","el-GR","en-AU","en-CA","en-GB","en-GH","en-HK","en-IE","en-IN","en-KE","en-NG","en-NZ","en-PH","en-PK","en-SG","en-TZ","en-US","en-ZA","es-AR","es-BO","es-CL","es-CO","es-CR","es-DO","es-EC","es-ES","es-GT","es-HN","es-MX","es-NI","es-PA","es-PE","es-PR","es-PY","es-SV","es-US","es-UY","es-VE","et-EE","eu-ES","fa-IR","fi-FI","fil-PH","fr-BE","fr-CA","fr-CH","fr-FR","gl-ES","gu-IN","hi-IN","hr-HR","hu-HU","hy-AM","id-ID","is-IS","it-CH","it-IT","iw-IL","ja-JP","jv-ID","ka-GE","kk-KZ","km-KH","kn-IN","ko-KR","lo-LA","lt-LT","lv-LV","mk-MK","ml-IN","mn-MN","mr-IN","ms-MY","my-MM","ne-NP","nl-BE","nl-NL","no-NO","pa-Guru-IN","pl-PL","pt-BR","pt-PT","ro-RO","ru-RU","si-LK","sk-SK","sl-SI","sq-AL","sr-RS","su-ID","sv-SE","sw-KE","sw-TZ","ta-IN","ta-LK","ta-MY","ta-SG","te-IN","th-TH","tr-TR","uk-UA","ur-IN","ur-PK","uz-UZ","vi-VN","yue-Hant-HK","zu-ZA",]

        def getOrThrow(d:dict, key:str, expectedType:type, default = None) -> any:
            value = d.get(key, None)
            if value is None:
                if default is not None:
                    return default
                raise Exception(f"Failed to get models from service, no {key}.")
            if isinstance(value, expectedType) is False:
                raise Exception(f"Failed to get models from service, {key} is not the expected type.")
            return value

        def getAttribution(d:dict) -> Attribution:
            a = getOrThrow(d, "Attribution", dict)
            return Attribution(getOrThrow(a, "Name", str), getOrThrow(a, "Url", str))

        # Parse the response.
        result = getOrThrow(response, "Result", dict)

        # Build the info object from the response.
        info = Info()

        # Get the AsrPrograms - Speech to Text
        info.asr = []
        for p in getOrThrow(result, "SpeechToText", list):
            program = AsrProgram(
                name=getOrThrow(p, "Name", str),
                description=getOrThrow(p, "Description", str),
                version=getOrThrow(p, "Version", str),
                attribution=getAttribution(p),
                installed=True,
                models=[]
            )
            info.asr.append(program)
            for m in getOrThrow(p, "Options", list):
                model = AsrModel(
                    name=getOrThrow(m, "Name", str),
                    description=getOrThrow(m, "Description", str),
                    version=getOrThrow(m, "Version", str),
                    languages=getOrThrow(m, "Languages", list, sageLanguages),
                    attribution=getAttribution(m),
                    installed=True
                )
                program.models.append(model)

        # Get the IntentProgram - ChatGPT
        info.intent = []
        for p in getOrThrow(result, "LlmChat", list):
            program = IntentProgram(
                name=getOrThrow(p, "Name", str),
                description=getOrThrow(p, "Description", str),
                version=getOrThrow(p, "Version", str),
                attribution=getAttribution(p),
                installed=True,
                models=[]
            )
            info.intent.append(program)
            for m in getOrThrow(p, "Options", list):
                model = IntentModel(
                    name=getOrThrow(m, "Name", str),
                    description=getOrThrow(m, "Description", str),
                    version=getOrThrow(m, "Version", str),
                    languages=getOrThrow(m, "Languages", list, sageLanguages),
                    attribution=getAttribution(m),
                    installed=True
                )
                program.models.append(model)

        # Get the TtsProgram - Text to Speech
        info.tts = []
        voiceCount = 0
        for p in getOrThrow(result, "TextToSpeech", list):
            program = TtsProgram(
                name=getOrThrow(p, "Name", str),
                description=getOrThrow(p, "Description", str),
                version=getOrThrow(p, "Version", str),
                attribution=getAttribution(p),
                installed=True,
                voices=[]
            )
            info.tts.append(program)
            for m in getOrThrow(p, "Options", list):
                voice = TtsVoice(
                    name=getOrThrow(m, "Name", str),
                    description=getOrThrow(m, "Description", str),
                    version=getOrThrow(m, "Version", str),
                    languages=getOrThrow(m, "Languages", list, sageLanguages),
                    attribution=getAttribution(m),
                    installed=True
                )
                voiceCount += 1
                program.voices.append(voice)

        self.Logger.info("Returning Sage Info to client. Voices: " + str(voiceCount))
        await self.write_event(info.event())
