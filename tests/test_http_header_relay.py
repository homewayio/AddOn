"""Exercise real HTTP response framing and header preservation through the relay."""

# pylint: disable=import-error,no-name-in-module,protected-access
# Generated FlatBuffers annotations do not describe their builder methods fully.
# pyright: reportUnknownMemberType=false

import gzip
import logging
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Dict, List, Tuple
from unittest.mock import Mock, patch

import octoflatbuffers
import requests
from urllib3 import HTTPHeaderDict

from homeway_linuxhost.webrequestresponsehandler import ResponseHandlerContext, WebRequestResponseHandler
from homeway.buffer import Buffer
from homeway.httpheaderpolicy import HttpHeaderPolicy
from homeway.httpresult import HttpResult
from homeway.streammsgbuilder import StreamMsgBuilder
from homeway.Proto import HttpHeader, HttpInitialContext, StreamMessage, WebStreamMsg
from homeway.Proto.DataCompression import DataCompression
from homeway.WebStream.headerimpl import BaseProtocol, HeaderHelper
from homeway.WebStream.webstreamhttphelper import WebStreamHttpHelper


class HeaderFixtureHandler(BaseHTTPRequestHandler):
    payload = b'{"tiles":["/api/map_tiles/vector/{z}/{x}/{y}.mvt"]}'
    compressed = gzip.compress(payload, mtime=0)
    multipart = gzip.compress(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\nabc\r\n--frame--\r\n", mtime=0)
    cookies = ["first=1; Expires=Wed, 21 Oct 2030 07:28:00 GMT; Path=/", "second=2; Path=/"]
    requestHeaders:Dict[str, str] = {}
    requestBody = b""

    def log_message(self, format:str, *args:object) -> None: #pylint: disable=redefined-builtin
        del format, args

    def do_GET(self) -> None:
        if self.path.startswith("/conditional/"):
            self.SendConditional(int(self.path.rsplit("/", 1)[1]))
            return
        if self.path == "/encoded-multipart":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(self.multipart)))
            self.end_headers()
            self.wfile.write(self.multipart)
            return
        if self.path == "/not-modified":
            self.SendNoBody(304)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Vary", "Origin")
        self.send_header("Expires", "Wed, 21 Oct 2030 07:28:00 GMT")
        for cookie in self.cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_header("X-Proxy-Feature", "end-to-end")
        self.send_header("X-Legacy-Text", "caf\u00e9")
        self.send_header("X-Private", "hop-only")
        self.send_header("Connection", "X-Private, Keep-Alive")
        self.send_header("Keep-Alive", "timeout=5")
        self.send_header("Alt-Svc", 'h3=":443"')
        if self.path in ("/chunked", "/chunked-with-length"):
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Trailer", "X-Checksum")
            if self.path == "/chunked-with-length":
                self.send_header("Content-Length", "1")
        elif self.path != "/unknown-length":
            self.send_header("Content-Length", str(len(self.compressed)))
            if self.path == "/duplicate-length":
                self.send_header("Content-Length", str(len(self.compressed)))
        self.end_headers()
        if self.path in ("/chunked", "/chunked-with-length"):
            for chunk in (self.compressed[:11], self.compressed[11:]):
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.write(b"0\r\nX-Checksum: trailer-only\r\n\r\n")
        else:
            self.wfile.write(self.compressed)

    def do_HEAD(self) -> None:
        self.SendNoBody(200)

    def SendNoBody(self, status:int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "8192")
        self.send_header("Content-Encoding", "gzip")
        self.end_headers()

    def do_POST(self) -> None:
        HeaderFixtureHandler.requestHeaders = dict(self.headers.items())
        HeaderFixtureHandler.requestBody = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path.startswith("/conditional/"):
            self.SendConditional(int(self.path.rsplit("/", 1)[1]))
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def SendConditional(self, status:int) -> None:
        body = b"origin response body"
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"same"')
        self.send_header("Last-Modified", "Wed, 21 Oct 2030 07:28:00 GMT")
        self.end_headers()
        self.wfile.write(body)


def MakeContext(headers:List[Tuple[str, str]], method:str="GET") -> HttpInitialContext.HttpInitialContext:
    builder = octoflatbuffers.Builder(1024)
    offsets:List[int] = []
    for name, value in headers:
        nameOffset = builder.CreateString(name)
        valueOffset = builder.CreateString(value)
        HttpHeader.Start(builder)
        HttpHeader.AddKey(builder, nameOffset)
        HttpHeader.AddValue(builder, valueOffset)
        offsets.append(HttpHeader.End(builder))
    HttpInitialContext.StartHeadersVector(builder, len(offsets))
    for offset in reversed(offsets):
        builder.PrependUOffsetTRelative(offset)
    vector = builder.EndVector()
    host = builder.CreateString("fixture.homeway.io")
    methodOffset = builder.CreateString(method)
    path = builder.CreateString("/")
    HttpInitialContext.Start(builder)
    HttpInitialContext.AddHost(builder, host)
    HttpInitialContext.AddMethod(builder, methodOffset)
    HttpInitialContext.AddPath(builder, path)
    HttpInitialContext.AddHeaders(builder, vector)
    builder.Finish(HttpInitialContext.End(builder))
    return HttpInitialContext.HttpInitialContext.GetRootAs(builder.Output())


class HttpHeaderRelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), HeaderFixtureHandler)
        cls.serverThread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.serverThread.start()
        cls.baseUrl = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.serverThread.join(timeout=2)

    def setUp(self) -> None:
        self.logger = logging.getLogger("test-http-header-relay")
        self.helper = WebStreamHttpHelper.__new__(WebStreamHttpHelper)
        self.helper.Id = 1
        self.helper.Logger = self.logger
        self.helper.BodyReadUseReadInto = True
        self.helper.BodyReadContentFallbackOffset = 0

    def WireHeaders(self, result:HttpResult) -> List[Tuple[str, str]]:
        builder = octoflatbuffers.Builder(1024)
        vector = self.helper.buildHeaderVector(builder, result)
        HttpInitialContext.Start(builder)
        if vector is not None:
            HttpInitialContext.AddHeaders(builder, vector)
        builder.Finish(HttpInitialContext.End(builder))
        context = HttpInitialContext.HttpInitialContext.GetRootAs(builder.Output())
        headers:List[Tuple[str, str]] = []
        for i in range(context.HeadersLength()):
            header = context.Headers(i)
            assert header is not None
            name = StreamMsgBuilder.BytesToString(header.Key())
            value = StreamMsgBuilder.BytesToString(header.Value())
            assert name is not None and value is not None
            headers.append((name, value))
        return headers

    def ExecuteResult(self, result:HttpResult, method:str, headers:List[Tuple[str, str]], handler:Mock, useCompressionPolicy:bool=False) -> WebStreamMsg.WebStreamMsg:
        context = MakeContext(headers, method)
        stream = Mock()
        logger = Mock(spec=logging.Logger)
        logger.isEnabledFor.return_value = False
        helper = WebStreamHttpHelper(1, logger, stream, SimpleNamespace(
            FullStreamDataSize=lambda: 0, HttpInitialContext=lambda: context), 0) #pyright: ignore[reportArgumentType]
        self.addCleanup(helper.UploadBody.Cleanup)
        self.addCleanup(helper.CompressionContext.__exit__, None, None, None)
        with patch("homeway.WebStream.webstreamhttphelper.CommandHandler.Get",
                   return_value=SimpleNamespace(IsCommandRequest=lambda _: False)), \
             patch("homeway.WebStream.webstreamhttphelper.CustomFileServer.Get",
                   return_value=SimpleNamespace(IsCustomFileRequest=lambda *_: False)), \
             patch("homeway.WebStream.webstreamhttphelper.HttpRequest.MakeHttpCallStreamHelper", return_value=result), \
             patch("homeway.WebStream.webstreamhttphelper.Compat.GetWebRequestResponseHandler", return_value=handler), \
             patch.object(helper, "shouldCompressBody", side_effect=helper.shouldCompressBody if useCompressionPolicy else None, return_value=False):
            helper.executeHttpRequest()
        logger.warning.assert_not_called()
        logger.error.assert_not_called()
        stream.SendToStream.assert_called_once()
        buffer, start, _, isLast, _ = stream.SendToStream.call_args.args
        self.assertTrue(isLast)
        envelope = StreamMessage.StreamMessage.GetRootAs(buffer.GetBytesLike(), start + 4)
        table = envelope.Context()
        assert table is not None
        message = WebStreamMsg.WebStreamMsg()
        message.Init(table.Bytes, table.Pos)
        return message

    def test_real_gzip_response_stays_encoded_through_stream_and_full_body_reads(self) -> None:
        for path in ("/length", "/unknown-length", "/chunked", "/duplicate-length", "/chunked-with-length"):
            for buffered in (False, True):
                with self.subTest(path=path, buffered=buffered):
                    with requests.get(self.baseUrl + path, headers={"Accept-Encoding": "identity"}, stream=True, timeout=2) as response:
                        result = HttpResult.BuildFromRequestLibResponse(response, self.baseUrl + path)
                        if buffered:
                            result.ReadAllContentFromStreamResponse(self.logger)
                            assert result.FullBodyBuffer is not None
                            body = bytes(result.FullBodyBuffer.GetBytesLike())
                        else:
                            chunks:List[bytes] = []
                            self.helper.BodyReadContentFallbackOffset = 0
                            while True:
                                target = bytearray(11)
                                count = self.helper.doBodyReadInto(result, target, 0, len(target))
                                if count == 0:
                                    break
                                chunks.append(bytes(target[:count]))
                            body = b"".join(chunks)
                        self.assertEqual(body, HeaderFixtureHandler.compressed)
                        self.assertEqual(gzip.decompress(body), HeaderFixtureHandler.payload)
                        headers = self.WireHeaders(result)
                        self.assertIn(("Content-Encoding", "gzip"), headers)
                        self.assertEqual([value for name, value in headers if name.lower() == "set-cookie"], HeaderFixtureHandler.cookies)
                        self.assertEqual([value for name, value in headers if name.lower() == "vary"], ["Accept-Encoding", "Origin"])
                        self.assertIn(("X-Proxy-Feature", "end-to-end"), headers)
                        self.assertEqual(response.raw.headers["X-Legacy-Text"], "caf\u00e9")
                        self.assertIn(("X-Legacy-Text", "caf\u00e9"), headers)
                        self.assertIn(("Expires", "Wed, 21 Oct 2030 07:28:00 GMT"), headers)
                        excluded = HttpHeaderPolicy.c_HopByHopHeaders | {"x-private", "x-checksum"}
                        self.assertFalse(any(name.lower() in excluded for name, _ in headers))
                        if path in ("/chunked", "/chunked-with-length", "/unknown-length"):
                            self.assertNotIn("Content-Length", result.Headers)
                        else:
                            self.assertEqual(result.Headers.getlist("Content-Length"), [str(len(HeaderFixtureHandler.compressed))])

    def test_replay_header_copy_keeps_duplicates_and_is_independently_mutable(self) -> None:
        headers = HTTPHeaderDict()
        for cookie in HeaderFixtureHandler.cookies:
            headers.add("Set-Cookie", cookie)
        result = HttpResult(200, headers, "/", False, fullBodyBuffer=Buffer(b"body"))
        replay = result.CreateReplayCopy()
        self.assertEqual(replay.Headers.getlist("set-cookie"), HeaderFixtureHandler.cookies)
        replay.Headers["Set-Cookie"] = "replacement=1"
        self.assertEqual(replay.Headers.getlist("set-cookie"), ["replacement=1"])
        self.assertEqual(result.Headers.getlist("set-cookie"), HeaderFixtureHandler.cookies)
        del replay.Headers["SET-cookie"]
        self.assertNotIn("set-cookie", replay.Headers)

    def test_encoded_http_body_skips_tunnel_compression_and_preserves_protocol_flags(self) -> None:
        encodedBody = gzip.compress(bytes(range(256)) * 20, mtime=0)
        self.assertGreater(len(encodedBody), 200)
        for encoding in ("gzip", "br", "deflate", "gzip, br"):
            result = HttpResult(200, {"Content-Encoding": encoding}, "/", False)
            self.assertFalse(self.helper.shouldCompressBody("application/octet-stream", result, len(encodedBody)))
        for encoding in ("", "identity"):
            result = HttpResult(200, {"Content-Encoding": encoding}, "/", False)
            self.assertTrue(self.helper.shouldCompressBody("application/octet-stream", result, len(encodedBody)))

        for precompressed in (False, True):
            with self.subTest(precompressed=precompressed):
                result = HttpResult(200, {"Content-Type": "application/octet-stream", "Content-Encoding": "gzip",
                                         "Content-Length": str(len(encodedBody)), "ETag": '"gzip-origin"'}, "/", False)
                data = zlib.compress(encodedBody) if precompressed else encodedBody
                result.SetFullBodyBuffer(Buffer(data), DataCompression.Zlib if precompressed else DataCompression.None_,
                                         len(encodedBody) if precompressed else 0)
                handler = Mock()
                handler.CheckIfResponseNeedsToBeHandled.return_value = None
                with patch("homeway.WebStream.webstreamhttphelper.Compression.Get",
                           side_effect=AssertionError("Encoded bytes must not be compressed again")):
                    message = self.ExecuteResult(result, "GET", [], handler, useCompressionPolicy=True)
                self.assertEqual(bytes(message.DataAsByteArray()), data)
                self.assertEqual(message.DataCompression(), DataCompression.Zlib if precompressed else DataCompression.None_)
                self.assertEqual(message.OriginalDataSize(), len(encodedBody) if precompressed else 0)
                self.assertEqual(message.FullStreamDataSize(), len(encodedBody))
                self.assertEqual(result.Headers["Content-Encoding"], "gzip")
                self.assertEqual(result.Headers["Content-Length"], str(len(encodedBody)))
                self.assertEqual(result.Headers["ETag"], '"gzip-origin"')

    def test_request_hops_are_removed_and_requests_recomputes_upload_framing(self) -> None:
        context = MakeContext([
            ("X-Private", "hop-only"), ("Connection", "X-Private, Keep-Alive"),
            ("Keep-Alive", "timeout=5"), ("TE", "trailers"), ("Trailer", "X-Checksum"),
            ("Transfer-Encoding", "chunked"), ("Upgrade", "other-protocol"),
            ("Proxy-Authorization", "fixture"), ("Content-Length", "999"),
            ("Content-Type", "application/octet-stream"), ("Content-Encoding", "gzip"),
            ("X-Proxy-Feature", "keep"), ("Accept-Encoding", "br"), ("X-Legacy-Text", "caf\u00e9"),
        ])
        headers = HeaderHelper.GatherRequestHeaders(self.logger, context, BaseProtocol.Http)
        self.assertNotIn("Content-Length", headers)
        self.assertFalse(any(name.lower() in HttpHeaderPolicy.c_HopByHopHeaders | {"x-private"} for name in headers))
        self.assertEqual(headers["Accept-Encoding"], "identity")
        with requests.post(self.baseUrl, headers=headers, data=HeaderFixtureHandler.compressed, timeout=2) as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(HeaderFixtureHandler.requestBody, HeaderFixtureHandler.compressed)
        self.assertEqual(HeaderFixtureHandler.requestHeaders["Content-Length"], str(len(HeaderFixtureHandler.compressed)))
        self.assertEqual(HeaderFixtureHandler.requestHeaders["Content-Encoding"], "gzip")
        self.assertEqual(HeaderFixtureHandler.requestHeaders["X-Proxy-Feature"], "keep")
        self.assertEqual(HeaderFixtureHandler.requestHeaders["X-Legacy-Text"], "caf\u00e9")

    def test_invalid_fields_are_rejected_without_losing_other_headers(self) -> None:
        context = MakeContext([("X-Good", "value"), ("Bad Name", "value"), ("X-Bad", "one\r\ntwo"), ("X-Unencodable", "\u0100")])
        headers = HeaderHelper.GatherRequestHeaders(self.logger, context, BaseProtocol.Http)
        self.assertEqual(headers["X-Good"], "value")
        self.assertNotIn("Bad Name", headers)
        self.assertNotIn("X-Bad", headers)
        self.assertNotIn("X-Unencodable", headers)
        result = HttpResult(200, {"X-Good": "value", "Bad Name": "value", "X-Bad": "one\r\ntwo", "X-Unencodable": "\u0100"}, "/", False)
        self.assertEqual(self.WireHeaders(result), [("X-Good", "value")])

    def test_representation_length_is_preserved_as_metadata_for_head_and_304(self) -> None:
        for method, path in (("HEAD", "/"), ("GET", "/not-modified")):
            for buffered in (False, True):
                with self.subTest(method=method, buffered=buffered):
                    with requests.request(method, self.baseUrl + path, stream=True, timeout=2) as response:
                        result = HttpResult.BuildFromRequestLibResponse(response, self.baseUrl + path)
                        if buffered:
                            result.ReadAllContentFromStreamResponse(self.logger)
                            assert result.FullBodyBuffer is not None
                            self.assertEqual(len(result.FullBodyBuffer), 0)
                        else:
                            self.assertEqual(self.helper.doBodyReadInto(result, bytearray(11), 0, 11), 0)
                        self.assertIn(("Content-Length", "8192"), self.WireHeaders(result))
                        self.assertIn(("Content-Encoding", "gzip"), self.WireHeaders(result))

    def test_bodyless_responses_send_zero_relay_payload_and_keep_representation_length(self) -> None:
        for method, path in (("HEAD", "/"), ("GET", "/not-modified")):
            for buffered in (False, True):
                with self.subTest(method=method, buffered=buffered):
                    with requests.request(method, self.baseUrl + path, stream=True, timeout=2) as response:
                        result = HttpResult.BuildFromRequestLibResponse(response, self.baseUrl + path)
                        if buffered:
                            with patch.object(self.logger, "warning") as warning:
                                result.ReadAllContentFromStreamResponse(self.logger, maxBodySizeBytes=1)
                            warning.assert_not_called()
                        handler = Mock()
                        message = self.ExecuteResult(result, method, [], handler)
                        self.assertEqual(message.FullStreamDataSize(), 0)
                        self.assertEqual(message.DataLength(), 0)
                        context = message.HttpInitialContext()
                        assert context is not None
                        fields = []
                        for i in range(context.HeadersLength()):
                            field = context.Headers(i)
                            assert field is not None
                            fields.append((StreamMsgBuilder.BytesToString(field.Key()), StreamMsgBuilder.BytesToString(field.Value())))
                        self.assertIn(("Content-Length", "8192"), fields)
                        handler.CheckIfResponseNeedsToBeHandled.assert_not_called()

        for status in (101, 103, 204, 205):
            with self.subTest(status=status):
                result = HttpResult(status, {"Content-Length": "8192"}, "/", False, fullBodyBuffer=Buffer(b"must not send"))
                handler = Mock()
                message = self.ExecuteResult(result, "GET", [], handler)
                self.assertEqual(message.FullStreamDataSize(), 0)
                self.assertEqual(message.DataLength(), 0)
                self.assertNotIn("Content-Length", result.Headers)
                handler.CheckIfResponseNeedsToBeHandled.assert_not_called()

    def test_request_no_transform_skips_html_rewriter_during_relay_execution(self) -> None:
        body = b"<html><head></head><body>unchanged</body></html>"
        result = HttpResult(200, {"Content-Type": "text/html", "ETag": '"original"'}, "/", False, fullBodyBuffer=Buffer(body))
        handler = Mock()
        message = self.ExecuteResult(result, "GET", [("Cache-Control", "private, No-Transform")], handler)
        handler.CheckIfResponseNeedsToBeHandled.assert_not_called()
        self.assertEqual(bytes(message.DataAsByteArray()), body)
        self.assertEqual(message.FullStreamDataSize(), len(body))
        self.assertEqual(result.Headers["ETag"], '"original"')

    def test_live_origin_conditional_decisions_preserve_status_and_body(self) -> None:
        headers = [("If-None-Match", '"same"'), ("If-Modified-Since", "Wed, 21 Oct 2030 07:28:00 GMT")]
        for method, status in (("GET", 200), ("GET", 412), ("GET", 404), ("POST", 200)):
            with self.subTest(method=method, status=status):
                url = f"{self.baseUrl}/conditional/{status}"
                with requests.request(method, url, headers=dict(headers), stream=True, timeout=2) as response:
                    result = HttpResult.BuildFromRequestLibResponse(response, url)
                    handler = Mock()
                    handler.CheckIfResponseNeedsToBeHandled.return_value = None
                    message = self.ExecuteResult(result, method, headers, handler)
                    self.assertEqual(message.StatusCode(), status)
                    self.assertEqual(bytes(message.DataAsByteArray()), b"origin response body")

    def test_encoded_multipart_bypasses_decoded_boundary_parser(self) -> None:
        url = self.baseUrl + "/encoded-multipart"
        with requests.get(url, stream=True, timeout=2) as response:
            result = HttpResult.BuildFromRequestLibResponse(response, url)
            handler = Mock()
            handler.CheckIfResponseNeedsToBeHandled.return_value = None
            with patch.object(WebStreamHttpHelper, "readStreamChunk", side_effect=AssertionError("Encoded body must stay raw")) as parse:
                message = self.ExecuteResult(result, "GET", [], handler)
            parse.assert_not_called()
            self.assertEqual(bytes(message.DataAsByteArray()), HeaderFixtureHandler.multipart)
            self.assertEqual(message.FullStreamDataSize(), len(HeaderFixtureHandler.multipart))
            self.assertEqual(result.Headers["Content-Encoding"], "gzip")

    def test_response_no_transform_skips_rewriter_setup_before_streaming(self) -> None:
        chunks = [Buffer(b"data: unmodified\n\n"), None]
        closed = Mock()
        result = HttpResult(200, {"Content-Type": "text/event-stream", "Cache-Control": "no-transform"},
                            "/map", False, customBodyStreamCallback=lambda: chunks.pop(0), customBodyStreamClosedCallback=closed)
        handler = Mock()
        context = MakeContext([])
        stream = Mock()
        helper = WebStreamHttpHelper(1, self.logger, stream, SimpleNamespace(
            FullStreamDataSize=lambda: 0, HttpInitialContext=lambda: context), 0) #pyright: ignore[reportArgumentType]
        self.addCleanup(helper.UploadBody.Cleanup)
        self.addCleanup(helper.CompressionContext.__exit__, None, None, None)
        with patch("homeway.WebStream.webstreamhttphelper.CommandHandler.Get",
                   return_value=SimpleNamespace(IsCommandRequest=lambda _: False)), \
             patch("homeway.WebStream.webstreamhttphelper.CustomFileServer.Get",
                   return_value=SimpleNamespace(IsCustomFileRequest=lambda *_: False)), \
             patch("homeway.WebStream.webstreamhttphelper.HttpRequest.MakeHttpCallStreamHelper", return_value=result), \
             patch("homeway.WebStream.webstreamhttphelper.Compat.GetWebRequestResponseHandler", return_value=handler), \
             patch.object(helper, "shouldCompressBody", return_value=False):
            helper.executeHttpRequest()
        handler.CheckIfResponseNeedsToBeHandled.assert_not_called()
        self.assertEqual(stream.SendToStream.call_count, 2)
        closed.assert_called_once()

    def test_websocket_header_and_subprotocol_handling_is_unchanged(self) -> None:
        context = MakeContext([("Connection", "Upgrade"), ("Upgrade", "websocket"),
                               ("Cookie", "session=fixture"), ("User-Agent", "fixture"),
                               ("Sec-WebSocket-Protocol", "first,second")])
        headers = HeaderHelper.GatherWebsocketRequestHeaders(self.logger, context)
        self.assertEqual(headers, {"Cookie": "session=fixture", "User-Agent": "fixture"})
        self.assertEqual(HeaderHelper.GetWebSocketSubProtocols(self.logger, context), ["first", "second"])


class HtmlResponseMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = WebRequestResponseHandler(logging.getLogger("test-html-metadata"))
        self.context = ResponseHandlerContext(ResponseHandlerContext.HomeAssistantHtmlPage)
        self.body = Buffer(b"<html><head></head><body>test</body></html>")
        self.headers = {"Content-Type": "text/html; charset=utf-8", "ETag": '"original"',
                        "Content-Digest": "sha-256=:old:", "Last-Modified": "Wed, 21 Oct 2030 07:28:00 GMT",
                        "Accept-Ranges": "bytes", "Cache-Control": "private, max-age=0"}

    def test_changes_invalidate_representation_metadata_and_update_length(self) -> None:
        result = HttpResult(200, self.headers, "/", False)
        with patch("homeway_linuxhost.webrequestresponsehandler.CustomFileServer.Get",
                   return_value=SimpleNamespace(GetCustomHtmlHeaderIncludeBytes=lambda: b"<script></script>")):
            body = self.handler.HandleResponse(self.context, result, self.body)
        self.assertIn(b"<script></script>", body.GetBytesLike())
        for name in ("ETag", "Content-Digest", "Last-Modified", "Accept-Ranges"):
            self.assertNotIn(name, result.Headers)
        self.assertEqual(result.Headers["Content-Length"], str(len(body)))
        self.assertEqual(result.Headers["Cache-Control"], self.headers["Cache-Control"])

    def test_encoded_no_transform_partial_and_non_html_responses_are_not_modified(self) -> None:
        for overrides, status in (({"Content-Encoding": "gzip"}, 200),
                                  ({"Cache-Control": "private, No-Transform"}, 200),
                                  ({"Signature": "sig1=:fixture:"}, 200),
                                  ({"Signature-Input": 'sig1=("content-digest")'}, 200),
                                  ({}, 206), ({"Content-Type": "application/json"}, 200)):
            with self.subTest(overrides=overrides, status=status):
                result = HttpResult(status, {**self.headers, **overrides}, "/", False)
                before = list(result.Headers.items())
                with patch.object(self.handler, "_HandleHomeAssistantHtmlPage") as rewrite:
                    body = self.handler.HandleResponse(self.context, result, self.body)
                rewrite.assert_not_called()
                self.assertIs(body, self.body)
                self.assertEqual(list(result.Headers.items()), before)

    def test_unmodified_html_preserves_validators(self) -> None:
        result = HttpResult(200, self.headers, "/", False)
        body = Buffer(b"no head tag")
        self.assertIs(self.handler.HandleResponse(self.context, result, body), body)
        self.assertEqual(result.Headers["ETag"], self.headers["ETag"])
        self.assertEqual(result.Headers["Content-Digest"], self.headers["Content-Digest"])


if __name__ == "__main__":
    unittest.main()
