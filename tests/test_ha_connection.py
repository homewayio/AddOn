"""Tests for the persistent Home Assistant WebSocket connection."""

# pylint: disable=import-error,no-name-in-module,protected-access

import logging
import threading
import unittest
from typing import Any, Dict, List, Optional, cast
from unittest.mock import patch

from homeway_linuxhost.ha.connection import Connection, PendingContexts
from homeway_linuxhost.ha.eventhandler import EventHandler
from homeway.buffer import Buffer
from homeway.interfaces import IWebSocketClient, WebSocketOpCode


class FakeWebSocket:
    """WebSocket fake that records when a message is sent."""

    def __init__(self) -> None:
        self.MessageSent = threading.Event()

    def Send(
        self,
        buffer: Buffer,
        msgStartOffsetBytes: Optional[int] = None,
        msgSize: Optional[int] = None,
        isData: bool = True,
    ) -> None:
        del buffer, msgStartOffsetBytes, msgSize, isData
        self.MessageSent.set()


class HomeAssistantConnectionTests(unittest.TestCase):
    """Exercise connection teardown behavior."""

    def test_close_wakes_pending_send_and_receive(self) -> None:
        connection = Connection(
            logging.getLogger("test_ha_connection"), cast(EventHandler, object())
        )
        websocket = FakeWebSocket()
        connection.Ws = cast(Any, websocket)
        connection.IsConnected = True
        responses: List[Optional[Dict[str, Any]]] = []

        worker = threading.Thread(
            target=lambda: responses.append(
                connection.SendAndReceiveMsg(
                    {"type": "test/pending"}, timeoutSec=5.0
                )
            ),
            daemon=True,
        )
        worker.start()
        self.assertTrue(websocket.MessageSent.wait(1.0))

        connection.Closed(cast(IWebSocketClient, websocket))
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(responses, [None])
        self.assertFalse(connection.IsConnected)
        self.assertIsNone(connection.Ws)
        self.assertEqual(connection.PendingContexts, {})

    def test_stale_close_does_not_reset_current_connection(self) -> None:
        connection = Connection(
            logging.getLogger("test_ha_connection"), cast(EventHandler, object())
        )
        currentWebsocket = FakeWebSocket()
        oldWebsocket = FakeWebSocket()
        connection.Ws = cast(Any, currentWebsocket)
        connection.IsConnected = True

        connection.Closed(cast(IWebSocketClient, oldWebsocket))

        self.assertTrue(connection.IsConnected)
        self.assertIs(connection.Ws, currentWebsocket)

    def test_stale_data_does_not_authenticate_current_connection(self) -> None:
        connection = Connection(
            logging.getLogger("test_ha_connection"), cast(EventHandler, object())
        )
        currentWebsocket = FakeWebSocket()
        oldWebsocket = FakeWebSocket()
        connection.Ws = cast(Any, currentWebsocket)

        connection._OnData(
            cast(IWebSocketClient, oldWebsocket),
            Buffer(b'{"type":"auth_ok"}'),
            WebSocketOpCode.TEXT,
        )

        self.assertFalse(connection.IsConnected)

    def test_result_that_becomes_stale_cannot_complete_a_new_request(self) -> None:
        connection = Connection(
            logging.getLogger("test_ha_connection"), cast(EventHandler, object())
        )
        oldWebsocket = FakeWebSocket()
        currentWebsocket = FakeWebSocket()
        connection.Ws = cast(Any, oldWebsocket)
        connection.IsConnected = True
        pendingContext = PendingContexts()
        connection.PendingContexts[1] = pendingContext

        class SwitchingBuffer:
            """Switch connections after the callback's initial identity check."""

            def GetBytesLike(self) -> bytes:
                with connection.PendingContextsLock:
                    connection.Ws = cast(Any, currentWebsocket)
                return b'{"id":1,"type":"result","success":true,"result":{}}'

        connection._OnData(
            cast(IWebSocketClient, oldWebsocket),
            cast(Buffer, SwitchingBuffer()),
            WebSocketOpCode.TEXT,
        )

        self.assertFalse(pendingContext.Event.is_set())
        self.assertIsNone(pendingContext.Response)

    def test_auth_keeps_expected_restart_armed_until_config_is_resolved(self) -> None:
        connection = Connection(
            logging.getLogger("test_ha_connection"), cast(EventHandler, object())
        )
        websocket = FakeWebSocket()
        connection.Ws = cast(Any, websocket)
        connection.SetServerRestartExpected()

        with patch.object(connection, "_OnConnected"):
            connection._OnData(
                cast(IWebSocketClient, websocket),
                Buffer(b'{"type":"auth_ok"}'),
                WebSocketOpCode.TEXT,
            )

        self.assertTrue(connection.IsConnected)
        self.assertTrue(connection.ServerRestartExpected.is_set())

    def test_expected_restart_reconnect_state_has_a_bounded_budget(self) -> None:
        connection = Connection(
            logging.getLogger("test_ha_connection"), cast(EventHandler, object())
        )
        connection.SetServerRestartExpected()

        for _ in range(Connection.c_ExpectedServerRestartReconnectMaxAttempts):
            self.assertTrue(connection._ShouldUseExpectedServerRestartReconnect())

        self.assertFalse(connection._ShouldUseExpectedServerRestartReconnect())
        self.assertFalse(connection.ServerRestartExpected.is_set())
        self.assertEqual(connection.ServerRestartReconnectAttempt, 0)


if __name__ == "__main__":
    unittest.main()
