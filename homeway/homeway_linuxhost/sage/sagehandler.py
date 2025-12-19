import time
import json
import logging
import threading
import asyncio
from typing import Optional, Any, Dict
import aiohttp

from wyoming.asr import Transcript, Transcribe
from wyoming.tts import SynthesizeStopped, Synthesize
from wyoming.error import Error
from wyoming.event import Event
from wyoming.handle import Handled
from wyoming.server import AsyncEventHandler
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.info import Describe, AsrModel, AsrProgram, Attribution, Info, TtsProgram, TtsVoice, IntentProgram, IntentModel

from homeway.sentry import Sentry

from ..util import Util
from ..ha.homecontext import HomeContext

from .fabric import Fabric
from .sagehistory import SageHistory
from .sagelanguage import SageLanguage
from .fibermanager import FiberManager, SpeakDataResponse
from .sagetranscribehandler import SageTranscribeHandler
from .interfaces import ISageHandler


class SageHandler(AsyncEventHandler, ISageHandler):

    # This is the max length of any string we will process, be it for transcribing, synthesizing, or anything else.
    # This is more of a sanity check, the server will enforce it's own limits.
    c_MaxStringLength = 500

    # Cache the describe info because it doesn't change often and it's sometimes requested many times rapidly.
    # Sometimes the describe event is called before an action for some reason and is blocking, so the cache helps there as well.
    # Since this hardly ever changes, there's no reason to update it often. It will update when the addon restarts.
    c_InfoEventMaxCacheTimeSec = 60 * 60 * 24 * 3
    s_InfoEvent:Optional[Info] = None
    s_InfoEventTime:float = 0.0
    s_InfoEventLock = threading.Lock()


    # Note this handler is created for each new request flow, like for a full Listen session.
    def __init__(self, logger:logging.Logger, fabric:Fabric, fiberManger:FiberManager, homeContext:HomeContext,
                   sageHistory:SageHistory, sageLanguage:SageLanguage, sagePrefix:Optional[str], devLocalHomewayServerAddress:Optional[str],
                   *args:Any, **kwargs:Any) -> None:
        super().__init__(*args, **kwargs)
        self.Logger = logger
        self.Fabric = fabric
        self.FiberManager = fiberManger
        self.HomeContext = homeContext
        self.SageHistory = sageHistory
        self.SagePrefix = sagePrefix
        self.SageLanguage = sageLanguage
        self.DevLocalHomewayServerAddress = devLocalHomewayServerAddress

        # This is created when the first audio stream starts and is reset when the stream ends.
        # It handles holding the context of the incoming audio stream for transcribing.
        self.SageTranscribeHandler:Optional[SageTranscribeHandler] = None

        # Holds the language we were told from transcribe.
        # This can be None if no language is sent.
        self.TranscribeLanguage:Optional[str] = None


    # Logs and error and writes an error message back to the client.
    async def WriteError(self, text:str, code:Optional[str]=None) -> None:
        self.Logger.error(f"Homeway Sage Error: {text}")
        await self.write_event(Error(text, code).event())
        # Calling stop actually makes HA realize the error happened. Otherwise it will keep spinning,
        # even if we return False from handle_event which should disconnect, but it doesn't.
        await self.stop()


    # The main event handler for all wyoming events.
    # Returning False will disconnect the client.
    async def handle_event(self, event:Event) -> bool:

        # Fired when the server is first connected to and the client wants to know what models are available.
        if Describe.is_type(event.type):
            return await self._HandleDescribe()

        # This is sent before the audio stream, the only thing we need from it is the language.
        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            if transcribe.language is not None:
                self.TranscribeLanguage = transcribe.language
            # Kick the language refresh so it's ready for chat and TTS.
            self.SageLanguage.Refresh()
            return True

        # All speech to text audio is handled by this function.
        if AudioChunk.is_type(event.type) or AudioStart.is_type(event.type) or AudioStop.is_type(event.type):
            # On start, we always re-create the transcribe handler.
            if AudioStart.is_type(event.type):
                self.SageTranscribeHandler = SageTranscribeHandler(self.Logger, self, self.FiberManager, self.TranscribeLanguage, event)
                return True

            # We should always have a handler now.
            if self.SageTranscribeHandler is None:
                await self.WriteError("Homeway Sage received an audio chunk or stop before the audio start event.")
                return False

            # Let the handler handle the event.
            return await self.SageTranscribeHandler.HandleStreamingAudio(event)

        # Fired when there's a input phrase from the user that the client wants to run the model on.
        if Transcript.is_type(event.type):
            transcript = Transcript.from_event(event)

            # Add the user text to the history, making it the most recent message.
            newMsg = self._EnforceMaxStringLength(transcript.text)
            self.Logger.debug(f"Sage - Transcript Start - {newMsg}")

            # Ensure the message exists and isn't just white space.
            if Util.IsStrNullOrWhitespace(newMsg):
                self.Logger.info("Sage - Transcript Start - Empty message, ignoring.")
                await self.write_event(Handled("").event())
                return True

            # Add the user message to the transcript.
            self.SageHistory.AddUserText(newMsg)

            # Build the request.
            request = {
                # These is the history of this conversation.
                "Messages" : self.SageHistory.GetHistoryMessagesJsonObj(),
            }
            requestJsonStr = json.dumps(request)

            # Get the home context, which is all of the entities and their relationships.
            # Also get the current state of all entities exposed.
            homeContext = self.HomeContext.GetSageHomeContext()
            states, liveContext = self.HomeContext.GetStatesAndLiveContext()

            # Try to get the conversation language from SageLanguage.
            # Same as with TTS, the event doesn't have the language, so we have to get it ourselves.
            languageCode:Optional[str] = self.SageLanguage.GetConversationLanguage()

            # Make the request.
            start = time.time()
            responseText = await self.FiberManager.Chat(requestJsonStr, homeContext, states, liveContext, languageCode)

            # Check the result
            if responseText is None:
                await self.WriteError("Homeway Sage failed to transcribe the text.")
                return True

            # Ensure the response text is not too long.
            responseText = self._EnforceMaxStringLength(responseText)

            # Add the assistant text to the history.
            self.Logger.debug(f"Sage - Transcript End - Time: {time.time() - start} - {newMsg} -> {responseText} -")
            self.SageHistory.AddAssistantText(responseText)

            # Send the response back to the client.
            await self.write_event(Handled(responseText).event())
            return True

        # Fired when the client wants to synthesize a voice for the given text.
        if Synthesize.is_type(event.type):
            transcript = Synthesize.from_event(event)

            # Get the user chosen voice name
            voiceName:Optional[str] = None
            if transcript.voice is not None:
                voiceName = transcript.voice.name

            # Get the TTS language from SageLanguage if possible.
            # For some reason the STT events have the language, but the TTS don't.
            languageCode:Optional[str] = self.SageLanguage.GetTtsLanguage()

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
                # But, even though we stream the audio, HA won't process it until the AudioEnd event is sent.
                # In version 2025.8 of HA they updated the logic to allow us to send a AudioStart -> AudioChunk -> AudioEnd for each streamed response, which then let the audio play instantly.
                # But that breaks older version of HA, and we can't version check right now, so we don't do it.
                # TODO - Add logic to version check HA and do the better logic if possible.
                # We can confirm this by looking at the code: https://github.com/home-assistant/core/blob/dev/homeassistant/components/wyoming/tts.py
                if synthContext.IsFirstChunk:
                    synthContext.IsFirstChunk = False
                    await self.write_event(AudioStart(rate=response.SampleRate, width=response.BytesPerSample, channels=response.Channels).event())

                # Now write the audio chunk.
                await self.write_event(AudioChunk(audio=response.Bytes.ForceAsBytes(), rate=response.SampleRate, width=response.BytesPerSample, channels=response.Channels).event())
                synthContext.ChunkCount += 1
                synthContext.TotalBytes += len(response.Bytes)

                # Return true to keep going.
                return True

            self.Logger.debug(f"Sage - Synthesize Start - {text}")
            start = time.time()
            result = await self.FiberManager.Speak(text, voiceName, languageCode, streamingDataReceivedCallback)
            self.Logger.debug(f"Sage Synthesize End - {text} - voice: {voiceName} - time: {time.time() - start} - chunks: {synthContext.ChunkCount} - bytes: {synthContext.TotalBytes}")

            # If we wrote anything, we need to send the audio stop event.
            if synthContext.IsFirstChunk is False:
                await self.write_event(AudioStop().event())

            # This shouldn't be required, since we aren't supporting streaming, but HA's impl seems to be broken and needs this to end the audio stream.
            # This bug was added in HA 2025.8
            await self.write_event(SynthesizeStopped().event())

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

        # Since the discovery command is called rapidly sometimes, we will cache the info event for a few seconds.
        try:
            info:Optional[Info] = None
            with SageHandler.s_InfoEventLock:
                if SageHandler.s_InfoEvent is not None and time.time() - SageHandler.s_InfoEventTime < SageHandler.c_InfoEventMaxCacheTimeSec:
                    info = SageHandler.s_InfoEvent

            if info is not None:
                self.Logger.debug("Returning a cached info event for Discovery.")
                await self._CacheAndWriteInfoEvent(info, False)
                return True
        except Exception as e:
            Sentry.OnException("Sage - _HandleDescribe failed to write the cached info object, we will try for a new info object.", e)

        # Note for some reason the name of the AsrProgram is what will show up in the discovery for users.
        # Get the current programs / models / voices from the service.
        try:
            # Get the correct URL
            url = "https://homeway.io/api/sage/getmodels"
            if self.DevLocalHomewayServerAddress is not None:
                url = f"http://{self.DevLocalHomewayServerAddress}/api/sage/getmodels"

            # Since this is important, we have some retry logic.
            # We have to explicitly set the headers because the lib will advertise it can handle gzip, deflate, and zstd, but zstd
            # wont work on arm.
            attempt = 0
            async with aiohttp.ClientSession(headers={"Accept-Encoding": "gzip, deflate"}) as session:
                while True:
                    attempt += 1
                    serviceInfo:Optional[Info] = None
                    try:
                        self.Logger.debug(f"Sage - Starting Info Service Request - {url}")
                        # Perform the async GET request with a timeout of 10 seconds.
                        async with session.get(url, timeout=10) as response:
                            if response.status == 200:
                                data = await response.json()
                                serviceInfo = self._BuildInfoEvent(data)
                                if serviceInfo is None:
                                    raise Exception("Failed to build info event.")
                            else:
                                self.Logger.warning(f"Sage - Failed to get models from service. Attempt: {attempt} - {response.status}")
                    except Exception as e:
                        self.Logger.warning(f"Sage - Failed to get models from service. Attempt: {attempt} - {e}")

                    # If we got valid serviceInfo, try to process it.
                    if serviceInfo is not None:
                        self.Logger.debug("Sage - Service request successful, sending to Wyoming protocol.")
                        try:
                            await self._CacheAndWriteInfoEvent(serviceInfo)
                            # Success, exit the function.
                            return True
                        except Exception as e:
                            self.Logger.warning(f"Sage - Failed to send info to wyoming protocol. Attempt: {attempt} - {e}")

                    # After 3 attempts, raise an exception.
                    if attempt > 3:
                        raise Exception("Failed to get models from service after 3 attempts.")

                    # Wait 5 seconds before retrying.
                    await asyncio.sleep(5)

        except Exception as e:
            Sentry.OnException("Sage - Failed get info from service.", e)
            await self.WriteError("Homeway Sage failed to get the models info from the service.")
            return False


    # Writes the info event back to the client.
    async def _CacheAndWriteInfoEvent(self, info:Info, cache:bool=True) -> None:
        # Write it
        await self.write_event(info.event())

        if cache:
            # After we successfully write the info event, we will cache it.
            with SageHandler.s_InfoEventLock:
                SageHandler.s_InfoEvent = info
                SageHandler.s_InfoEventTime = time.time()


    # Handles the service info and writes the event back to the client.
    # This must write the info event or throw, so it will retry the process.
    def _BuildInfoEvent(self, response:Dict[str, Any]) -> Info:

        # This is a list of languages we support.
        # We keep them here on the client side so they don't have to be sent every time.
        # Most of our models are multi-lingual, so most of all of these work with any given voice or model.
        # For anything that isn't supported, the server will map it on demand.
        sageLanguages = ["af-ZA", "am-ET", "ar-AE","ar-BH","ar-DZ","ar-EG","ar-IL","ar-IQ","ar-JO","ar-KW","ar-LB","ar-MA","ar-MR","ar-OM","ar-PS","ar-QA","ar-SA","ar-SY","ar-TN","ar-YE","az-AZ","bg-BG","bn-BD","bn-IN","bs-BA","ca-ES","cmn-Hans-CN","cmn-Hans-HK","cmn-Hant-TW","cs-CZ","da-DK","de-AT","de-CH","de-DE","el-GR","en-AU","en-CA","en-GB","en-GH","en-HK","en-IE","en-IN","en-KE","en-NG","en-NZ","en-PH","en-PK","en-SG","en-TZ","en-US","en-ZA","es-AR","es-BO","es-CL","es-CO","es-CR","es-DO","es-EC","es-ES","es-GT","es-HN","es-MX","es-NI","es-PA","es-PE","es-PR","es-PY","es-SV","es-US","es-UY","es-VE","et-EE","eu-ES","fa-IR","fi-FI","fil-PH","fr-BE","fr-CA","fr-CH","fr-FR","gl-ES","gu-IN","hi-IN","hr-HR","hu-HU","hy-AM","id-ID","is-IS","it-CH","it-IT","iw-IL","ja-JP","jv-ID","ka-GE","kk-KZ","km-KH","kn-IN","ko-KR","lo-LA","lt-LT","lv-LV","mk-MK","ml-IN","mn-MN","mr-IN","ms-MY","my-MM","ne-NP","nl-BE","nl-NL","no-NO","pa-Guru-IN","pl-PL","pt-BR","pt-PT","ro-RO","ru-RU","si-LK","sk-SK","sl-SI","sq-AL","sr-RS","su-ID","sv-SE","sw-KE","sw-TZ","ta-IN","ta-LK","ta-MY","ta-SG","te-IN","th-TH","tr-TR","uk-UA","ur-IN","ur-PK","uz-UZ","vi-VN","yue-Hant-HK","zu-ZA",]

        def getOrThrow(d:Dict[str, Any], key:str, expectedType:type, defaultValue:Optional[Any]=None) -> Any:
            value = d.get(key, None)
            if value is None:
                if defaultValue is not None:
                    return defaultValue
                raise Exception(f"Failed to get models from service, no {key}.")
            if isinstance(value, expectedType) is False:
                raise Exception(f"Failed to get models from service, {key} is not the expected type.")
            return value

        def getAttribution(d:Dict[str, Any]) -> Attribution:
            a = getOrThrow(d, "Attribution", dict)
            return Attribution(getOrThrow(a, "Name", str), getOrThrow(a, "Url", str))

        def addSagePrefixIfNeeded(s:str) -> str:
            if self.SagePrefix is None:
                return s
            return f"{self.SagePrefix} - {s}"

        # Parse the response.
        result = getOrThrow(response, "Result", dict)

        # Build the info object from the response.
        info = Info()

        # Get the AsrPrograms - Speech to Text
        info.asr = []
        for p in getOrThrow(result, "SpeechToText", list):
            program = AsrProgram(
                name=addSagePrefixIfNeeded(getOrThrow(p, "Name", str)),
                description=getOrThrow(p, "Description", str),
                version=getOrThrow(p, "Version", str),
                attribution=getAttribution(p),
                installed=True,
                models=[],
            )
            info.asr.append(program)
            for m in getOrThrow(p, "Options", list):
                model = AsrModel(
                    name=addSagePrefixIfNeeded(getOrThrow(m, "Name", str)),
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
                name=addSagePrefixIfNeeded(getOrThrow(p, "Name", str)),
                description=getOrThrow(p, "Description", str),
                version=getOrThrow(p, "Version", str),
                attribution=getAttribution(p),
                installed=True,
                models=[]
            )
            info.intent.append(program)
            for m in getOrThrow(p, "Options", list):
                model = IntentModel(
                    name=addSagePrefixIfNeeded(getOrThrow(m, "Name", str)),
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
                name=addSagePrefixIfNeeded(getOrThrow(p, "Name", str)),
                description=getOrThrow(p, "Description", str),
                version=getOrThrow(p, "Version", str),
                attribution=getAttribution(p),
                installed=True,
                voices=[],
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

        self.Logger.info("Sage Info built. Voices: " + str(voiceCount))
        return info
