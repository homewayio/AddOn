import time
import logging
import threading
from typing import List

# A class to track and manage the chat history, since we can't get it from the context.
class SageHistory:

    # The max number of history items to keep.
    c_MaxHistoryItems = 20

    # The max age of a history item in seconds.
    c_MaxHistoryItemsAgeSec = 60 * 60 * 24

    def __init__(self, logger:logging.Logger):
        self.Logger = logger
        self.Lock = threading.Lock()
        self.UserMsgHistory:List[SageHistoryItem] = []
        self.AssistantMsgHistory:List[SageHistoryItem] = []


    # Sets text the user input.
    def AddUserText(self, text:str) -> None:
        self.Logger.debug(f"Sage - User Text - {text}")
        self._AddItem(self.UserMsgHistory, text)


    # Sets text the assistant output.
    def AddAssistantText(self, text:str) -> None:
        self.Logger.debug(f"Sage - Assistant Text - {text}")
        self._AddItem(self.AssistantMsgHistory, text)


    # Returns the history as a json object.
    def GetHistoryJsonObj(self) -> dict:
        with self.Lock:
            # We need to remove any old history items.
            currentTimeSec = time.time()
            self.UserMsgHistory = [x for x in self.UserMsgHistory if currentTimeSec - x.TimestampSec < self.c_MaxHistoryItemsAgeSec]
            self.AssistantMsgHistory = [x for x in self.AssistantMsgHistory if currentTimeSec - x.TimestampSec < self.c_MaxHistoryItemsAgeSec]

            # Now we can return the history.
            # This json object schema is determined by the server.
            # Note the most recent user message is the new input.
            return {
                "User": [x.Text for x in self.UserMsgHistory],
                "Assistant": [x.Text for x in self.AssistantMsgHistory]
            }


    def _AddItem(self, historyList:List["SageHistoryItem"], text:str) -> None:
        with self.Lock:
            historyList.append(SageHistoryItem(text))
            if len(historyList) > self.c_MaxHistoryItems:
                historyList.pop(0)


# Keeps track of a single history item.
class SageHistoryItem:
    def __init__(self, text:str):
        self.TimestampSec = time.time()
        self.Text = text
