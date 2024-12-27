import asyncio
import logging
import threading
from functools import partial

from wyoming.info import AsrModel, AsrProgram, Attribution, Info, TtsProgram, TtsVoice, TtsVoiceSpeaker, HandleProgram,HandleModel, IntentProgram, IntentModel
from wyoming.server import AsyncServer

from homeway.sentry import Sentry

from .sagehandler import SageHandler
from .fabric import Fabric

# The main root host for Sage
class SageHost:

    # TODO - This should be dynamic to support multiple instances, but it can't change.
    c_ServerPort = 8765

    def __init__(self, logger:logging.Logger):
        self.Logger = logger
        self.PluginId:str = None
        self.ApiKey:str = None
        self.Fabric:Fabric = None


    # Once the api key is known, we can start.
    def Start(self, pluginId:str, apiKey:str):
        self.PluginId = pluginId
        self.ApiKey = apiKey

        # Start the fabric connection with Homeway
        self.Fabric = Fabric(self.Logger, self.PluginId, self.ApiKey)
        self.Fabric.Start()

        # Start an independent thread to run asyncio.
        threading.Thread(target=self._run).start()


    def _run(self):
        # A main protector for the asyncio loop.
        while True:
            try:
                asyncio.run(self._ServerThread())
            except Exception as e:
                Sentry.Exception("SageHost Asyncio Error", e)


    # The main asyncio loop for the server.
    async def _ServerThread(self):

        info = self._GetInfo()

        self.Logger.info(f"Starting wyoming server on port {SageHost.c_ServerPort}")
        server = AsyncServer.from_uri(f"tcp://0.0.0.0:{SageHost.c_ServerPort}")
        model_lock = asyncio.Lock()

        # Run!
        await server.run(
            partial(
                SageHandler,
                info,
                self.Logger,
                self.Fabric,
            )
        )


    def _GetInfo(self) -> Info:
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
                name="homeway-voice",
                description="test voice - deepgram long name test",
                attribution=Attribution(
                    name="Homeway",
                            url="https://homeway.io/",
                ),
                installed=True,
                version="0.0.1",
                languages=[
                    "en"
                ],
                speakers=[
                    TtsVoiceSpeaker("name-speaker")
                ]
            )
        ]

        info = Info(
            asr=[
                AsrProgram(
                    name="homeway-voice-speech-render",
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
            handle= [
                HandleProgram(
                    name="homeway-handle",
                    description="test",
                    attribution=Attribution(
                        name="Homeway",
                        url="https://homeway.io/",
                    ),
                    installed=True,
                    version="0.0.1",
                    models=[
                        HandleModel(
                            name="homeway-handle-model",
                            description="test",
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
            intent=
            [
                IntentProgram(
                    name="homeway-intent",
                    description="test",
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
                        )
                    ]
                )
            ]
        )
        return info
