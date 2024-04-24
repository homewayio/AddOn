# automatically generated by the FlatBuffers compiler, do not modify

# namespace: Proto

import octoflatbuffers
from typing import Any
from typing import Optional
class HttpHeader(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAs(cls, buf, offset: int = 0):
        n = octoflatbuffers.encode.Get(octoflatbuffers.packer.uoffset, buf, offset)
        x = HttpHeader()
        x.Init(buf, n + offset)
        return x

    @classmethod
    def GetRootAsHttpHeader(cls, buf, offset=0):
        """This method is deprecated. Please switch to GetRootAs."""
        return cls.GetRootAs(buf, offset)
    # HttpHeader
    def Init(self, buf: bytes, pos: int):
        self._tab = octoflatbuffers.table.Table(buf, pos)

    # HttpHeader
    def Key(self) -> Optional[str]:
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(4))
        if o != 0:
            return self._tab.String(o + self._tab.Pos)
        return None

    # HttpHeader
    def Value(self) -> Optional[str]:
        o = octoflatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(6))
        if o != 0:
            return self._tab.String(o + self._tab.Pos)
        return None

def HttpHeaderStart(builder: octoflatbuffers.Builder):
    builder.StartObject(2)

def Start(builder: octoflatbuffers.Builder):
    HttpHeaderStart(builder)

def HttpHeaderAddKey(builder: octoflatbuffers.Builder, key: int):
    builder.PrependUOffsetTRelativeSlot(0, octoflatbuffers.number_types.UOffsetTFlags.py_type(key), 0)

def AddKey(builder: octoflatbuffers.Builder, key: int):
    HttpHeaderAddKey(builder, key)

def HttpHeaderAddValue(builder: octoflatbuffers.Builder, value: int):
    builder.PrependUOffsetTRelativeSlot(1, octoflatbuffers.number_types.UOffsetTFlags.py_type(value), 0)

def AddValue(builder: octoflatbuffers.Builder, value: int):
    HttpHeaderAddValue(builder, value)

def HttpHeaderEnd(builder: octoflatbuffers.Builder) -> int:
    return builder.EndObject()

def End(builder: octoflatbuffers.Builder) -> int:
    return HttpHeaderEnd(builder)
