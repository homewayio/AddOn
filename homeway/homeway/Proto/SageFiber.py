# automatically generated by the FlatBuffers compiler, do not modify

# namespace: Proto

import octoflatbuffers
from typing import Any
from homeway.Proto.SageStreamMessage import SageStreamMessage
from typing import Optional
class SageFiber(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAs(cls, buf, offset: int = 0):
        n = octoflatbuffers.encode.Get(octoflatbuffers.packer.uoffset, buf, offset)
        x = SageFiber()
        x.Init(buf, n + offset)
        return x

    @classmethod
    def GetRootAsSageFiber(cls, buf, offset=0):
        """This method is deprecated. Please switch to GetRootAs."""
        return cls.GetRootAs(buf, offset)
    # SageFiber
    def Init(self, buf: bytes, pos: int):
        self._tab = octoflatbuffers.table.Table(buf, pos)

    # SageFiber
    def Message(self) -> Optional[SageStreamMessage]:
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(4))
        if o != 0:
            x = self._tab.Indirect(o + self._tab.Pos)
            obj = SageStreamMessage()
            obj.Init(self._tab.Bytes, x)
            return obj
        return None

def SageFiberStart(builder: octoflatbuffers.Builder):
    builder.StartObject(1)

def Start(builder: octoflatbuffers.Builder):
    SageFiberStart(builder)

def SageFiberAddMessage(builder: octoflatbuffers.Builder, message: int):
    builder.PrependUOffsetTRelativeSlot(0, octoflatbuffers.number_types.UOffsetTFlags.py_type(message), 0)

def AddMessage(builder: octoflatbuffers.Builder, message: int):
    SageFiberAddMessage(builder, message)

def SageFiberEnd(builder: octoflatbuffers.Builder) -> int:
    return builder.EndObject()

def End(builder: octoflatbuffers.Builder) -> int:
    return SageFiberEnd(builder)
