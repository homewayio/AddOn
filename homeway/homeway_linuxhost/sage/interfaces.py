from abc import ABC, abstractmethod

from wyoming.event import Event

from homeway.buffer import Buffer


class IFiberManager(ABC):

    @abstractmethod
    def OnSocketReset(self) -> None:
        pass

    @abstractmethod
    def OnIncomingMessage(self, data:Buffer) -> None:
        pass


class ISageHandler(ABC):

    @abstractmethod
    async def write_event(self, event:Event) -> None:
        pass
