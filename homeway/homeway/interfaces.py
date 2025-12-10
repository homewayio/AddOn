from enum import Enum
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .buffer import Buffer
from .httpresult import HttpResult

#
# Common Objects
# (used over interfaces)
#


# A simple enum to define the opcodes we use.
# This should mirror the _abnf.py file in the websocket library.
# These also are directly from the WS spec https://datatracker.ietf.org/doc/html/rfc6455#section-5.2
class WebSocketOpCode(Enum):
    CONT = 0x0
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA

    @staticmethod
    def FromWsLibInt(value: int) -> 'WebSocketOpCode':
        return WebSocketOpCode(value)

    def ToWsLibInt(self) -> int:
        return self.value


# Important! There are patterns these classes must follow in terms of how the use the callbacks.
# All callbacks should be fired from the async run thread, except error and close.
# The flow must be the following:
#
#    Create
#    RunAsync
#       -> Async Thread Starts
#             Wait for open
#             onWsOpen
#             Loop for messages
#                onWsData
#
#    onWsClosed can be called at anytime, even before onWsOpen is called!
#    If there is an error, onWsError will be called then onWsClosed.
class IWebSocketClient(ABC):

    @abstractmethod
    def Close(self) -> None:
        pass


    @abstractmethod
    def RunAsync(self) -> None:
        pass

    @abstractmethod
    def Send(self, buffer:Buffer, msgStartOffsetBytes:Optional[int]=None, msgSize:Optional[int]=None, isData:bool=True) -> None:
        pass

    @abstractmethod
    def SendWithOptCode(self, buffer:Buffer, msgStartOffsetBytes:Optional[int]=None, msgSize:Optional[int]=None, optCode=WebSocketOpCode.BINARY) -> None:
        pass

    @abstractmethod
    def SetDisableCertCheck(self, disable:bool) -> None:
        pass


class IConfigManager(ABC):

    @abstractmethod
    def CanEditConfig(self) -> bool:
        pass

    @abstractmethod
    def NeedsRestart(self) -> bool:
        pass


class IAccountLinkStatusUpdateHandler(ABC):

    @abstractmethod
    def OnAccountLinkStatusUpdate(self, isLinked:bool) -> None:
        pass


class IHomeContext(ABC):

    # Gets the full device and entity tree stored in our cache.
    @abstractmethod
    def GetFullDeviceAndEntityTree(self, forceRefresh: bool) -> Optional[List[Dict[str, Any]]]:
        pass

    # Looks up a full entity dict by its entity ID, or None if not found.
    @abstractmethod
    def GetEntityById(self, entityId: str, forceRefresh:bool=False) -> Optional[Dict[str, Any]]:
        pass

    # Given a device or entity dict and assistant types, this returns true or false if it's exposed or not.
    @abstractmethod
    def IsExposeToAssistant(self, obj:Dict[str, Any], checkAlexa:bool=False, checkGoogle:bool=False, checkSage:bool=False) -> bool:
        pass

    # Given a device or entity dict and assistant types, this returns if it's disabled by the user, integration, or other system.
    @abstractmethod
    def IsDisabled(self, obj:Dict[str, Any]) -> bool:
        pass

    # This logic needs to be the same as it's done in Home Assistant, to make sure the name matches.
    @abstractmethod
    def MakeFriendlyNameFromEntityId(self, entityId:str) -> Optional[str]:
        pass


class IHomeAssistantWebSocket(ABC):

    # Allows for any system to send and receive messages to Home Assistant,
    # since there are some APIs that can only be interacted with via the WS API.
    # Returns the response dict, or None on failure/timeout.
    @abstractmethod
    def SendAndReceiveMsg(self, msg:Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pass

    # Gets the Home Assistant version string, or None if not known.
    @abstractmethod
    def GetHomeAssistantVersionString(self) -> Optional[str]:
        pass


class IHost(ABC):

    @abstractmethod
    def OnSummonRequest(self, summonConnectUrl:str, summonMethod:int) -> None:
        pass


class IStream(ABC):

    @abstractmethod
    def OnSessionError(self, sessionId:int, backoffModifierSec:int) -> None:
        pass

    @abstractmethod
    def SendMsg(self, buffer:Buffer, msgStartOffsetBytes:int, msgSize:int) -> None:
        pass

    @abstractmethod
    def OnSummonRequest(self, sessionId:int, summonConnectUrl:str, summonMethod:int) -> None:
        pass

    @abstractmethod
    def OnHandshakeComplete(self, sessionId:int, apiKey:str, connectedAccounts:List[str]) -> None:
        pass

    @abstractmethod
    def OnPluginUpdateRequired(self) -> None:
        pass


class ISession(ABC):

    @abstractmethod
    def WebStreamClosed(self, sessionId:int) -> None:
        pass

    @abstractmethod
    def OnSessionError(self, backoffModifierSec:int) -> None:
        pass

    @abstractmethod
    def Send(self, buffer:Buffer, msgStartOffsetBytes:int, msgSize:int) -> None:
        pass


class IWebStream(ABC):

    @abstractmethod
    def SendToStream(self, buffer:Buffer, msgStartOffsetBytes:int, msgSize:int, isCloseFlagSet=False, silentlyFail=False) -> None:
        pass

    @abstractmethod
    def Close(self) -> None:
        pass

    @abstractmethod
    def SetClosedDueToFailedRequestConnection(self) -> None:
        pass


class IStateChangeHandler(ABC):

    # Called by the server logic when the server connection has been established.
    @abstractmethod
    def OnPrimaryConnectionEstablished(self, apiKey:str, connectedAccounts:List[str]) -> None:
        pass

    # Called by the server logic when a plugin update is required for this client.
    @abstractmethod
    def OnPluginUpdateRequired(self) -> None:
        pass


class IWebRequestHandler(ABC):

    # Given a URL (which can be absolute or relative) check if we might want to edit the response.
    # If no, then None is returned and the call is handled as normal.
    # If yes, some kind of context object must be returned, which will be given back to us.
    #     If yes, the entire response will be read as one full byte buffer, and given for us to deal with.
    @abstractmethod
    def CheckIfResponseNeedsToBeHandled(self, uri:str) -> Optional[Any]:
        pass

    # If we returned a context above in CheckIfResponseNeedsToBeHandled, this will be called after the web request is made
    # and the body is fully read. The entire body will be read into the bodyBuffer.
    # We are able to modify the bodyBuffer as we wish or not, but we must return the full bodyBuffer back to be returned.
    @abstractmethod
    def HandleResponse(self, contextObject:Any, httpResult:HttpResult, bodyBuffer:Buffer) -> Buffer:
        pass


class IServerInfoHandler(ABC):

    # Returns the access token, either from the environment or passed from the config.
    @abstractmethod
    def GetAccessToken(self) -> Optional[str]:
        pass

    # Returns the full <protocol>://<host or ip>:<port> depending on how the access token is setup, either in the docker container or running independently.
    # Takes a string that must be "http" or "ws" depending on the desired protocol. This can't be an enum since it's used over the compat handler API.
    # The protocol will automatically be converted to https or wss from the insecure mode as needed, determined by the server config.
    @abstractmethod
    def GetServerBaseUrl(self, protocol:str) -> str:
        pass
