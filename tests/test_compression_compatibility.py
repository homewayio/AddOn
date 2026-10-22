"""Check Homeway's compression wire format across old and current codecs."""

# pylint: disable=import-error,no-name-in-module,protected-access

import base64
import logging
import unittest
from unittest.mock import patch

from homeway.buffer import Buffer
from homeway.compression import Compression, CompressionContext
from homeway.Proto.DataCompression import DataCompression
from homeway.zstandarddictionary import ZStandardDictionary

try:
    import zstandard as zstd
except ImportError:
    zstd = None


# Frozen with python-zstandard 0.22.0 (libzstd 1.5.5), using Homeway's existing
# dictionary ID 1 and level 3. Homeway and OctoEverywhere have different dictionaries.
# Keep these bytes fixed when upgrading: same-version round trips alone cannot
# detect breaking older peers. Stream chunks were captured after FLUSH_BLOCK,
# before ending the frame, matching the relay's exact uncompressed-size contract.
LEGACY_SINGLE = base64.b64decode(
    "KLUv/WEBYAh1AQBYMSIua2l0Y2h9fQoK/BJxqFwIBTs6J+d3+HCzCG+Su1lrZMBizDlyxVF8xsUM"
)
# ZstdNet 1.5.7 Compressor.Wrap(PAYLOADS[0]), matching Homeway.Service's
# CompressionPool.RentZstdCompressor single-message path without a dictionary.
SERVER_SINGLE = base64.b64decode(
    "KLUv/WBgCGUCAMQDeyJpZCI6MSwidHlwZSI6ImV2ZW50Iiw6eyJlbnRpdHlfImxpZ2h0LmtpdGNo"
    "ZW4iLCJzdGF0b24ifX0KBAAScYKXWDFLaqFzSxM="
)
LEGACY_STREAM = tuple(base64.b64decode(value) for value in (
    "KLUv/QFYAXQBAFgxIi5raXRjaH19Cgr8EnGoXAgFOzon53f4cLMIb5K7WWtkwGLMOXLFUXzGxQw=",
    "lAAAGHtmZgPM63iCPo4sMFaJDcYG",
    "VAgABBAAAQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8wMTIz"
    "NDU2Nzg5Ojs8PT4/QEFCQ0RFRkdISUpLTE1OT1BRUlNUVVZXWFlaW1xdXl9gYWJjZGVmZ2hpamts"
    "bW5vcHFyc3R1dnd4eXp7fH1+f4CBgoOEhYaHiImKi4yNjo+QkZKTlJWWl5iZmpucnZ6foKGio6Sl"
    "pqeoqaqrrK2ur7CxsrO0tba3uLm6u7y9vr/AwcLDxMXGx8jJysvMzc7P0NHS09TV1tfY2drb3N3e"
    "3+Dh4uPk5ebn6Onq6+zt7u/w8fLz9PX29/j5+vv8/f7/AQAA/QPqmgI=",
))
PAYLOADS = (
    b'{"id":1,"type":"event","event":{"entity_id":"light.kitchen","state":"on"}}\n' * 32,
    b'{"id":1,"type":"event","event":{"entity_id":"light.kitchen","state":"off"}}\n' * 32,
    bytes(range(256)) * 3,
)


@unittest.skipIf(zstd is None, "zstandard is optional; CI installs the real codec")
class TestCompressionCompatibility(unittest.TestCase):
    """Exercise real dictionaries and streaming state instead of codec mocks."""

    def setUp(self) -> None:
        self.logger = logging.getLogger("test-compression-compatibility")
        for owner in (Compression, ZStandardDictionary):
            singleton = patch.object(owner, "_Instance", None)
            singleton.start()
            self.addCleanup(singleton.stop)
        with patch("homeway.compression.multiprocessing.cpu_count", return_value=3):
            self.compression = Compression(self.logger, "")
        Compression._Instance = self.compression
        self.assertTrue(self.compression.CanUseZStandardLib)
        dictionary = ZStandardDictionary.Get().PreTrainedDict
        assert dictionary is not None
        self.assertEqual(dictionary.dict_id(), 1)

    def test_decodes_legacy_single_message(self) -> None:
        """Current decoders must continue accepting complete legacy frames."""
        with CompressionContext(self.logger) as context:
            result = self.compression.Decompress(
                context, Buffer(LEGACY_SINGLE), len(PAYLOADS[0]), True, DataCompression.ZStandard
            )
            self.assertEqual(result.Get(), PAYLOADS[0])

    def test_decodes_server_single_message_without_dictionary(self) -> None:
        """The server's complete frames do not require the streaming dictionary."""
        with CompressionContext(self.logger) as context:
            result = self.compression.Decompress(
                context, Buffer(SERVER_SINGLE), len(PAYLOADS[0]), True, DataCompression.ZStandard
            )
            self.assertEqual(result.Get(), PAYLOADS[0])

    def test_decodes_legacy_stream_without_waiting_for_frame_end(self) -> None:
        """Each legacy message must decode before the shared frame finishes."""
        self.assertEqual(len(LEGACY_STREAM), len(PAYLOADS))
        with CompressionContext(self.logger) as context:
            for index, compressed in enumerate(LEGACY_STREAM):
                expected = PAYLOADS[index]
                result = self.compression.Decompress(
                    context, Buffer(compressed), len(expected), index == len(PAYLOADS) - 1, DataCompression.ZStandard
                )
                self.assertEqual(result.Get(), expected)

    def test_single_message_preserves_wire_metadata_and_dictionary(self) -> None:
        """Complete frames retain the protocol enum, dictionary and content size."""
        assert zstd is not None
        with CompressionContext(self.logger) as encoder, CompressionContext(self.logger) as decoder:
            encoder.SetTotalCompressedSizeOfData(len(PAYLOADS[0]))
            result = self.compression.Compress(encoder, Buffer(PAYLOADS[0]))
            self.assertEqual(result.CompressionType, 3)
            self.assertEqual(result.UncompressedSize, len(PAYLOADS[0]))
            frame = zstd.get_frame_parameters(result.Bytes.ForceAsBytes())
            self.assertEqual(frame.dict_id, 1)
            self.assertEqual(frame.content_size, len(PAYLOADS[0]))
            decoded = self.compression.Decompress(
                decoder, result.Bytes, result.UncompressedSize, True, result.CompressionType
            )
            self.assertEqual(decoded.Get(), PAYLOADS[0])

    def test_stream_messages_decode_individually_with_known_or_unknown_total(self) -> None:
        """Both HTTP bodies and indefinite event streams flush each message."""
        for knownSize in (False, True):
            with self.subTest(known_size=knownSize):
                with CompressionContext(self.logger) as encoder, CompressionContext(self.logger) as decoder:
                    if knownSize:
                        encoder.SetTotalCompressedSizeOfData(sum(len(payload) for payload in PAYLOADS))
                    for index, payload in enumerate(PAYLOADS):
                        result = self.compression.Compress(encoder, Buffer(payload))
                        decoded = self.compression.Decompress(
                            decoder, result.Bytes, len(payload), index == len(PAYLOADS) - 1, result.CompressionType
                        )
                        self.assertEqual(decoded.Get(), payload)

    def test_pooled_codecs_can_start_fresh_after_unfinished_stream(self) -> None:
        """Pool reuse must discard the preceding stream's compression history."""
        with CompressionContext(self.logger) as encoder, CompressionContext(self.logger) as decoder:
            result = self.compression.Compress(encoder, Buffer(PAYLOADS[0]))
            self.assertEqual(
                self.compression.Decompress(decoder, result.Bytes, len(PAYLOADS[0]), False, result.CompressionType).Get(),
                PAYLOADS[0],
            )
            compressor = encoder.Compressor
            decompressor = decoder.Decompressor
        with CompressionContext(self.logger) as encoder, CompressionContext(self.logger) as decoder:
            encoder.SetTotalCompressedSizeOfData(len(PAYLOADS[2]))
            result = self.compression.Compress(encoder, Buffer(PAYLOADS[2]))
            decoded = self.compression.Decompress(decoder, result.Bytes, len(PAYLOADS[2]), True, result.CompressionType)
            self.assertEqual(decoded.Get(), PAYLOADS[2])
            self.assertIs(encoder.Compressor, compressor)
            self.assertIs(decoder.Decompressor, decompressor)


if __name__ == "__main__":
    unittest.main()
