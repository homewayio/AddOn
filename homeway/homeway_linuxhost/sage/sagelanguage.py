import logging
import threading
from typing import Optional

from homeway.interfaces import IHomeAssistantWebSocket
from homeway.sentry import Sentry


# A class that helps with language related operations for Sage.
class SageLanguage:

    def __init__(self, logger:logging.Logger, haWebsocket:IHomeAssistantWebSocket, sagePrefix:Optional[str]) -> None:
        self.Logger = logger
        self.HaWebsocket = haWebsocket
        self.SagePrefix = sagePrefix

        # These are the values we track.
        # The entire assist pipeline is set to a single language, and then each component can be set to a nationality variant of it.
        # So whatever this language is, all components should be a subset of it, for example, "en-US" all components should be some form of "en-XX".
        self.Language:Optional[str] = None
        # This is the chat language.
        self.ConversationLanguage:Optional[str] = None
        # We don't need SST, since it's sent with each request.
        self.TtsLanguage:Optional[str] = None

        self.RefreshLock = threading.Lock()
        self.RefreshEvent = threading.Event()
        self.RefreshWaitEvent = threading.Event()
        self.RefreshThread = threading.Thread(target=self._WorkerThread, name="SageLanguageWorker")
        self.RefreshThread.start()


    # Refresh the language information.
    # This is used to ideally prevent the TTS language from blocking.
    def Refresh(self) -> None:
        # Signal the worker thread to refresh.
        self.RefreshEvent.set()


    # If possible, this returns the user's selected chat language from Home Assistant.
    # Note this is blocking for the text to speech operation, so it should be quick.
    def GetConversationLanguage(self) -> Optional[str]:
        # If we have a recent value, return it.
        with self.RefreshLock:
            # If we have anything return it. The speech to text action will kick off a refresh, so this should always be fresh.
            if self.ConversationLanguage is not None:
                # Always kick off a refresh to keep it updated.
                # Chat doesn't happen often, so it's fine to kick it off each time.
                self.RefreshEvent.set()
                return self.ConversationLanguage

        # This shouldn't happen, so note it.
        # Ideally we would have the value from the first connect or a recent refresh.
        self.Logger.info("Conversation language not yet available, forcing refresh.")

        # Otherwise, signal a refresh and return the last known value.
        self.RefreshEvent.set()

        # Dont wait long since this is blocking.
        self.RefreshWaitEvent.wait(0.2)

        # Return whatever we have.
        with self.RefreshLock:
            return self.ConversationLanguage


    # If possible, this returns the user's selected TTS language from Home Assistant.
    # Note this is blocking for the text to speech operation, so it should be quick.
    def GetTtsLanguage(self) -> Optional[str]:
        # If we have a recent value, return it.
        with self.RefreshLock:
            # If we have anything return it. The speech to text action will kick off a refresh, so this should always be fresh.
            if self.TtsLanguage is not None:
                # Always kick off a refresh to keep it updated.
                # TTS doesn't happen often, so it's fine to kick it off each time.
                self.RefreshEvent.set()
                return self.TtsLanguage

        # This shouldn't happen, so note it.
        # Ideally we would have the value from the first connect or a recent refresh.
        self.Logger.info("TTS language not yet available, forcing refresh.")

        # Otherwise, signal a refresh and return the last known value.
        self.RefreshEvent.set()

        # Dont wait long since this is blocking.
        self.RefreshWaitEvent.wait(0.2)

        # Return whatever we have.
        with self.RefreshLock:
            return self.TtsLanguage


    # Starts the worker thread.
    def _WorkerThread(self) -> None:
        firstRun = True
        failedUpdateCount = 0
        while True:
            try:
                # If we have never updated, do it immediately.
                if not firstRun:
                    # If we failed the last update, use a timeout to retry.
                    # Otherwise, wait indefinitely until we are told to refresh.
                    waitTime = None
                    if failedUpdateCount > 0:
                        waitTime = 5.0 * min(failedUpdateCount, 6)
                    self.RefreshEvent.wait(waitTime)
                firstRun = False

                # Clear the event.
                with self.RefreshLock:
                    self.RefreshEvent.clear()
                    self.RefreshWaitEvent.clear()

                # Perform the refresh.
                failedUpdateCount += 1
                if self._RefreshPipelineInfo():
                    failedUpdateCount = 0
            except Exception as ex:
                Sentry.OnException("SageLanguage Worker Thread Error", ex)
            finally:
                # Notify any waiters that we are done.
                self.RefreshWaitEvent.set()


    def _RefreshPipelineInfo(self) -> bool:
        # Get the TTS language from Home Assistant via the websocket.
        if self.HaWebsocket is None:
            self.Logger.error("Home Assistant WebSocket is not available to get TTS language.")
            return False

        try:
            response = self.HaWebsocket.SendAndReceiveMsg({"type":"assist_pipeline/pipeline/list"})
            if response is None:
                # This will happen if the socket isn't connected.
                self.Logger.debug("No response received from Home Assistant for TTS language request.")
                return False
            pipelines = response.get("result", {}).get("pipelines", [])
            for pipeline in pipelines:
                # Look for a Homeway TTS pipeline.
                engine = pipeline.get("tts_engine", "").lower()
                if engine.find("homeway") != -1:
                    # If we have a prefix, ensure the engine matches it.
                    if self.SagePrefix is not None:
                        prefix = self.SagePrefix.lower()
                        if engine.lower().find(prefix) == -1:
                            continue

                    with self.RefreshLock:
                        self.Language = pipeline.get("language")
                        self.TtsLanguage = pipeline.get("tts_language")
                        self.ConversationLanguage = pipeline.get("conversation_language")
                    self.Logger.debug(f"Retrieved TTS language from Home Assistant: {self.TtsLanguage}")
                    return True

            # This is fine, for any user who hasn't setup sage.
            self.Logger.info("No Homeway TTS pipeline found in Home Assistant.")
            # DONT'T return False so we don't keep retrying.
            return True
        except Exception as e:
            self.Logger.error(f"Error retrieving TTS language from Home Assistant: {e}")
        return False
