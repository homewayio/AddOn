import time
import logging
import threading
from typing import List

# A class to track and manage the chat history, since we can't get it from the context.
class SageHistory:

    # The max number of history items to keep.
    c_MaxHistoryItems = 25

    # The max age of a history item in seconds.
    # This is a tricky value, because we don't know if the user closed and opened a new chat.
    # So we keep track of the last time we got a message, and if it's been too long, we clear the history.
    # For now we defer to being too long over too short, since the context won't effect new questions much.
    c_MaxTimeBetweenMessagesSec = 60 * 60

    def __init__(self, logger:logging.Logger):
        self.Logger = logger
        self.Lock = threading.Lock()
        self.LastMessageReceivedTimeSec = time.time()
        self.History:List[SageHistoryItem] = []


    # Sets text the user input.
    def AddUserText(self, text:str) -> None:
        self.Logger.debug(f"Homeway Sage - User Text - {text}")
        self._AddItem("User", text)


    # Sets text the assistant output.
    def AddAssistantText(self, text:str) -> None:
        self.Logger.debug(f"Homeway Sage - Assistant Text - {text}")
        self._AddItem("Assistant", text)


    # Returns the history as a json object.
    def GetHistoryMessagesJsonObj(self) -> dict:
        with self.Lock:
            # The format of this json object must be kept in sync with the server.
            self.Logger.debug(f"Homeway Sage - Building chat history of {len(self.History)} messages.")
            messages = []
            for x in self.History:
                messages.append({
                    "Type": x.Type,
                    "Text": x.Text
                })
            return messages


    # Adds a history item.
    def _AddItem(self, msgType:str, text:str) -> None:
        # Validate the value.
        if msgType != "User" and msgType != "Assistant":
            self.Logger.error(f"Homeway Sage - Unknown message type - {msgType}")
        if len(text) == 0:
            self.Logger.debug("Homeway Sage - Empty message text - ignoring")
            return

        with self.Lock:
            # Since we don't get a notification when a new chat has been started, we don't know if the user is
            # doing a new chat or continuing an old one. So we need to clear the history if it's been too long.
            currentTimeSec = time.time()
            if len(self.History) > 0 and currentTimeSec - self.LastMessageReceivedTimeSec > self.c_MaxTimeBetweenMessagesSec:
                self.Logger.debug("Homeway Sage - Clearing history due to stale time.")
                self.History.clear()

            # Update the last message time.
            self.LastMessageReceivedTimeSec = currentTimeSec

            # Add it
            self.History.append(SageHistoryItem(msgType, text))

            # Check the max history items.
            if len(self.History) > self.c_MaxHistoryItems:
                self.History.pop(0)


# Keeps track of a single history item.
class SageHistoryItem:
    def __init__(self, msgType:str, text:str):
        self.Type = msgType
        self.Text = text
