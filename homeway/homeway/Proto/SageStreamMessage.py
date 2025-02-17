# automatically generated by the FlatBuffers compiler, do not modify

# namespace: Proto

import octoflatbuffers
from typing import Any
from homeway.Proto.SageDataContext import SageDataContext
from typing import Optional
class SageStreamMessage(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAs(cls, buf, offset: int = 0):
        n = octoflatbuffers.encode.Get(octoflatbuffers.packer.uoffset, buf, offset)
        x = SageStreamMessage()
        x.Init(buf, n + offset)
        return x

    @classmethod
    def GetRootAsSageStreamMessage(cls, buf, offset=0):
        """This method is deprecated. Please switch to GetRootAs."""
        return cls.GetRootAs(buf, offset)
    # SageStreamMessage
    def Init(self, buf: bytes, pos: int):
        self._tab = octoflatbuffers.table.Table(buf, pos)

    # SageStreamMessage
    def StreamId(self):
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(4))
        if o != 0:
            return self._tab.Get(octoflatbuffers.number_types.Uint32Flags, o + self._tab.Pos)
        return 0

    # SageStreamMessage
    def IsOpenMsg(self):
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(6))
        if o != 0:
            return bool(self._tab.Get(octoflatbuffers.number_types.BoolFlags, o + self._tab.Pos))
        return False

    # SageStreamMessage
    def IsDataTransmissionDone(self):
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(8))
        if o != 0:
            return bool(self._tab.Get(octoflatbuffers.number_types.BoolFlags, o + self._tab.Pos))
        return False

    # SageStreamMessage
    def IsAbortMsg(self):
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(10))
        if o != 0:
            return bool(self._tab.Get(octoflatbuffers.number_types.BoolFlags, o + self._tab.Pos))
        return False

    # SageStreamMessage
    def Data(self, j: int):
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(12))
        if o != 0:
            a = self._tab.Vector(o)
            return self._tab.Get(octoflatbuffers.number_types.Uint8Flags, a + octoflatbuffers.number_types.UOffsetTFlags.py_type(j * 1))
        return 0

    # SageStreamMessage
    def DataAsNumpy(self):
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(12))
        if o != 0:
            return self._tab.GetVectorAsNumpy(octoflatbuffers.number_types.Uint8Flags, o)
        return 0

    # SageStreamMessage
    def DataAsByteArray(self):
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(12))
        if o != 0:
            return self._tab.GetVectorAsByteArray(o)
        return 0

    # SageStreamMessage
    def DataLength(self) -> int:
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(12))
        if o != 0:
            return self._tab.VectorLen(o)
        return 0

    # SageStreamMessage
    def DataIsNone(self) -> bool:
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(12))
        return o == 0

    # SageStreamMessage
    def Type(self):
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(14))
        if o != 0:
            return self._tab.Get(octoflatbuffers.number_types.Int8Flags, o + self._tab.Pos)
        return 0

    # SageStreamMessage
    def DataContext(self) -> Optional[SageDataContext]:
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(16))
        if o != 0:
            x = self._tab.Indirect(o + self._tab.Pos)
            obj = SageDataContext()
            obj.Init(self._tab.Bytes, x)
            return obj
        return None

    # SageStreamMessage
    def StatusCode(self):
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(18))
        if o != 0:
            return self._tab.Get(octoflatbuffers.number_types.Uint32Flags, o + self._tab.Pos)
        return 0

    # SageStreamMessage
    def ErrorMessage(self) -> Optional[str]:
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(20))
        if o != 0:
            return self._tab.String(o + self._tab.Pos)
        return None

def SageStreamMessageStart(builder: octoflatbuffers.Builder):
    builder.StartObject(9)

def Start(builder: octoflatbuffers.Builder):
    SageStreamMessageStart(builder)

def SageStreamMessageAddStreamId(builder: octoflatbuffers.Builder, streamId: int):
    builder.PrependUint32Slot(0, streamId, 0)

def AddStreamId(builder: octoflatbuffers.Builder, streamId: int):
    SageStreamMessageAddStreamId(builder, streamId)

def SageStreamMessageAddIsOpenMsg(builder: octoflatbuffers.Builder, isOpenMsg: bool):
    builder.PrependBoolSlot(1, isOpenMsg, 0)

def AddIsOpenMsg(builder: octoflatbuffers.Builder, isOpenMsg: bool):
    SageStreamMessageAddIsOpenMsg(builder, isOpenMsg)

def SageStreamMessageAddIsDataTransmissionDone(builder: octoflatbuffers.Builder, isDataTransmissionDone: bool):
    builder.PrependBoolSlot(2, isDataTransmissionDone, 0)

def AddIsDataTransmissionDone(builder: octoflatbuffers.Builder, isDataTransmissionDone: bool):
    SageStreamMessageAddIsDataTransmissionDone(builder, isDataTransmissionDone)

def SageStreamMessageAddIsAbortMsg(builder: octoflatbuffers.Builder, isAbortMsg: bool):
    builder.PrependBoolSlot(3, isAbortMsg, 0)

def AddIsAbortMsg(builder: octoflatbuffers.Builder, isAbortMsg: bool):
    SageStreamMessageAddIsAbortMsg(builder, isAbortMsg)

def SageStreamMessageAddData(builder: octoflatbuffers.Builder, data: int):
    builder.PrependUOffsetTRelativeSlot(4, octoflatbuffers.number_types.UOffsetTFlags.py_type(data), 0)

def AddData(builder: octoflatbuffers.Builder, data: int):
    SageStreamMessageAddData(builder, data)

def SageStreamMessageStartDataVector(builder, numElems: int) -> int:
    return builder.StartVector(1, numElems, 1)

def StartDataVector(builder, numElems: int) -> int:
    return SageStreamMessageStartDataVector(builder, numElems)

def SageStreamMessageAddType(builder: octoflatbuffers.Builder, type: int):
    builder.PrependInt8Slot(5, type, 0)

def AddType(builder: octoflatbuffers.Builder, type: int):
    SageStreamMessageAddType(builder, type)

def SageStreamMessageAddDataContext(builder: octoflatbuffers.Builder, dataContext: int):
    builder.PrependUOffsetTRelativeSlot(6, octoflatbuffers.number_types.UOffsetTFlags.py_type(dataContext), 0)

def AddDataContext(builder: octoflatbuffers.Builder, dataContext: int):
    SageStreamMessageAddDataContext(builder, dataContext)

def SageStreamMessageAddStatusCode(builder: octoflatbuffers.Builder, statusCode: int):
    builder.PrependUint32Slot(7, statusCode, 0)

def AddStatusCode(builder: octoflatbuffers.Builder, statusCode: int):
    SageStreamMessageAddStatusCode(builder, statusCode)

def SageStreamMessageAddErrorMessage(builder: octoflatbuffers.Builder, errorMessage: int):
    builder.PrependUOffsetTRelativeSlot(8, octoflatbuffers.number_types.UOffsetTFlags.py_type(errorMessage), 0)

def AddErrorMessage(builder: octoflatbuffers.Builder, errorMessage: int):
    SageStreamMessageAddErrorMessage(builder, errorMessage)

def SageStreamMessageEnd(builder: octoflatbuffers.Builder) -> int:
    return builder.EndObject()

def End(builder: octoflatbuffers.Builder) -> int:
    return SageStreamMessageEnd(builder)
