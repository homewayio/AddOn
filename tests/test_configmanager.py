"""Tests for Home Assistant HTTP configuration management."""

# pylint: disable=import-error,no-name-in-module,protected-access

import logging
import threading
import unittest
from typing import Any, Dict, List, Optional, cast

from homeway_linuxhost.ha.configmanager import ConfigManager
from homeway_linuxhost.ha.connection import Connection


class FakeConnection:
    """Minimal Home Assistant WebSocket connection fake."""

    def __init__(
        self,
        responses: List[Optional[Dict[str, Any]]],
        version: str = "2026.8.1",
    ) -> None:
        self.Responses = list(responses)
        self.Messages: List[Dict[str, Any]] = []
        self.Version = version
        self.ServerRestartExpected = False

    def GetHomeAssistantVersionString(self) -> Optional[str]:
        return self.Version

    def SetServerRestartExpected(self) -> None:
        self.ServerRestartExpected = True

    def ClearServerRestartExpected(self) -> None:
        self.ServerRestartExpected = False

    def SendAndReceiveMsg(
        self, msg: Dict[str, Any], timeoutSec: float = 10.0
    ) -> Optional[Dict[str, Any]]:
        del timeoutSec
        self.Messages.append(msg)
        return self.Responses.pop(0)

def _HttpConfigResponse(
    stable: Dict[str, Any],
    activeConfigType: str = "stable",
    pending: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "success": True,
        "result": {
            "stable": stable,
            "pending": pending,
            "active_config_type": activeConfigType,
            "default": stable,
        },
    }


def _CreateManager(connection: FakeConnection) -> ConfigManager:
    manager = object.__new__(ConfigManager)
    manager.Logger = logging.getLogger("test_configmanager")
    manager.HaConnection = cast(Connection, connection)
    manager.RestartRequired = True
    manager.HttpConfigUpdateStateLock = threading.Lock()
    manager.HttpConfigUpdateWakeEvent = threading.Event()
    manager.HttpConfigUpdateThreadRunning = False
    manager.HttpConfigUpdateRequested = False
    manager.PendingHttpConfigToPromote = None
    return manager


class ConfigManagerHttpConfigTests(unittest.TestCase):
    """Exercise the HA 2026.8 HTTP config API lifecycle."""

    def test_confirms_staged_config_before_queued_restart(self) -> None:
        stable: Dict[str, Any] = {
            "server_port": 8123,
            "trusted_proxies": ["10.0.0.0/24"],
        }
        connection = FakeConnection(
            [
                _HttpConfigResponse(stable),
                {"success": True, "result": {"restart": True}},
                {"success": True, "result": None},
            ]
        )
        manager = _CreateManager(connection)

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertFalse(shouldRetry)
        self.assertEqual(
            [msg["type"] for msg in connection.Messages],
            ["http/config", "http/config/configure", "http/config/promote"],
        )
        self.assertFalse(connection.ServerRestartExpected)
        self.assertFalse(manager.RestartRequired)

    def test_retries_confirmation_if_restart_closes_socket_first(self) -> None:
        stable: Dict[str, Any] = {
            "server_port": 8123,
            "trusted_proxies": [],
        }
        connection = FakeConnection(
            [
                _HttpConfigResponse(stable),
                {"success": True, "result": {"restart": True}},
                None,
            ]
        )
        manager = _CreateManager(connection)

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertTrue(shouldRetry)
        self.assertEqual(connection.Messages[-1]["type"], "http/config/promote")
        self.assertTrue(connection.ServerRestartExpected)

    def test_lost_configure_response_keeps_restart_recovery_armed(self) -> None:
        stable: Dict[str, Any] = {
            "server_port": 8123,
            "trusted_proxies": [],
        }
        connection = FakeConnection([_HttpConfigResponse(stable), None])
        manager = _CreateManager(connection)

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertTrue(shouldRetry)
        self.assertTrue(connection.ServerRestartExpected)
        self.assertTrue(manager._HasPendingHttpConfigOwnedByHomeway())
        self.assertEqual(
            [msg["type"] for msg in connection.Messages],
            ["http/config", "http/config/configure"],
        )

    def test_configure_api_error_clears_restart_recovery_state(self) -> None:
        stable: Dict[str, Any] = {
            "server_port": 8123,
            "trusted_proxies": [],
        }
        connection = FakeConnection(
            [
                _HttpConfigResponse(stable),
                {
                    "success": False,
                    "error": {"code": "not_running", "message": "starting"},
                },
            ]
        )
        manager = _CreateManager(connection)

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertTrue(shouldRetry)
        self.assertFalse(connection.ServerRestartExpected)
        self.assertFalse(manager._HasPendingHttpConfigOwnedByHomeway())

    def test_does_not_confirm_when_update_needs_no_restart(self) -> None:
        stable: Dict[str, Any] = {
            "server_port": 8123,
            "trusted_proxies": [],
        }
        connection = FakeConnection(
            [
                _HttpConfigResponse(stable),
                {"success": True, "result": {"restart": False}},
            ]
        )
        manager = _CreateManager(connection)

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertFalse(shouldRetry)
        self.assertEqual(
            [msg["type"] for msg in connection.Messages],
            ["http/config", "http/config/configure"],
        )
        self.assertFalse(connection.ServerRestartExpected)

    def test_promotes_only_matching_pending_config_after_reconnect(self) -> None:
        pending: Dict[str, Any] = {
            "server_port": 8123,
            "use_x_forwarded_for": True,
            "trusted_proxies": ["172.30.32.0/23", "127.0.0.1/32", "::1/128"],
            "created_at": "2026-08-14T00:00:00+00:00",
            "error": None,
            "error_message": None,
        }
        connection = FakeConnection(
            [
                _HttpConfigResponse(
                    pending, activeConfigType="pending", pending=pending
                ),
                {"success": True, "result": None},
            ]
        )
        manager = _CreateManager(connection)
        manager._SetPendingHttpConfigOwnedByHomeway(
            {
                "server_port": 8123,
                "use_x_forwarded_for": True,
                "trusted_proxies": ["172.30.32.0/23", "127.0.0.1", "::1"],
            }
        )

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertFalse(shouldRetry)
        self.assertEqual(
            [msg["type"] for msg in connection.Messages],
            ["http/config", "http/config/promote"],
        )
        self.assertFalse(manager._HasPendingHttpConfigOwnedByHomeway())
        self.assertFalse(manager.RestartRequired)

    def test_leaves_unowned_pending_config_for_user_confirmation(self) -> None:
        pending: Dict[str, Any] = {
            "server_port": 8443,
            "use_x_forwarded_for": True,
            "trusted_proxies": ["172.30.32.0/23", "127.0.0.1/32", "::1/128"],
            "created_at": "2026-08-14T00:00:00+00:00",
            "error": None,
            "error_message": None,
        }
        connection = FakeConnection(
            [
                _HttpConfigResponse(
                    pending, activeConfigType="pending", pending=pending
                )
            ]
        )
        manager = _CreateManager(connection)

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertFalse(shouldRetry)
        self.assertEqual(
            [msg["type"] for msg in connection.Messages], ["http/config"]
        )

    def test_does_not_overwrite_unowned_config_waiting_for_restart(self) -> None:
        stable: Dict[str, Any] = {
            "server_port": 8123,
            "trusted_proxies": [],
        }
        pending: Dict[str, Any] = {
            "server_port": 8443,
            "trusted_proxies": [],
            "error": None,
        }
        connection = FakeConnection(
            [_HttpConfigResponse(stable, activeConfigType="stable", pending=pending)]
        )
        manager = _CreateManager(connection)
        manager._SetPendingHttpConfigOwnedByHomeway(
            {
                "server_port": 8123,
                "use_x_forwarded_for": True,
                "trusted_proxies": ["172.30.32.0/23"],
            }
        )
        connection.SetServerRestartExpected()

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertFalse(shouldRetry)
        self.assertEqual(
            [msg["type"] for msg in connection.Messages], ["http/config"]
        )
        self.assertFalse(manager._HasPendingHttpConfigOwnedByHomeway())
        self.assertFalse(connection.ServerRestartExpected)

    def test_waits_until_owned_pending_config_is_active(self) -> None:
        stable: Dict[str, Any] = {
            "server_port": 8123,
            "trusted_proxies": [],
        }
        pending: Dict[str, Any] = {
            "server_port": 8123,
            "use_x_forwarded_for": True,
            "trusted_proxies": ["172.30.32.0/23", "127.0.0.1/32", "::1/128"],
            "error": None,
        }
        connection = FakeConnection(
            [_HttpConfigResponse(stable, activeConfigType="stable", pending=pending)]
        )
        manager = _CreateManager(connection)
        manager._SetPendingHttpConfigOwnedByHomeway(
            {
                "server_port": 8123,
                "use_x_forwarded_for": True,
                "trusted_proxies": ["172.30.32.0/23", "127.0.0.1", "::1"],
            }
        )

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertTrue(shouldRetry)
        self.assertEqual(
            [msg["type"] for msg in connection.Messages], ["http/config"]
        )
        self.assertTrue(manager._HasPendingHttpConfigOwnedByHomeway())
        self.assertTrue(manager.RestartRequired)

    def test_rejected_owned_pending_config_is_not_promoted(self) -> None:
        stable: Dict[str, Any] = {
            "server_port": 8123,
            "trusted_proxies": [],
        }
        pending: Dict[str, Any] = {
            "server_port": 8123,
            "use_x_forwarded_for": True,
            "trusted_proxies": ["172.30.32.0/23", "127.0.0.1/32", "::1/128"],
            "error": "bind_failed",
            "error_message": "Address is already in use",
        }
        connection = FakeConnection(
            [_HttpConfigResponse(stable, activeConfigType="stable", pending=pending)]
        )
        manager = _CreateManager(connection)
        manager._SetPendingHttpConfigOwnedByHomeway(
            {
                "server_port": 8123,
                "use_x_forwarded_for": True,
                "trusted_proxies": ["172.30.32.0/23", "127.0.0.1", "::1"],
            }
        )
        connection.SetServerRestartExpected()

        shouldRetry = manager._UpdateHttpConfigViaApiIfNeeded()

        self.assertFalse(shouldRetry)
        self.assertEqual(
            [msg["type"] for msg in connection.Messages], ["http/config"]
        )
        self.assertFalse(manager._HasPendingHttpConfigOwnedByHomeway())
        self.assertFalse(connection.ServerRestartExpected)
        self.assertTrue(manager.RestartRequired)

    def test_reconnect_wakes_worker_without_blindly_promoting(self) -> None:
        connection = FakeConnection([])
        manager = _CreateManager(connection)
        manager.HttpConfigUpdateThreadRunning = True
        manager._SetPendingHttpConfigOwnedByHomeway(
            {
                "server_port": 8123,
                "use_x_forwarded_for": True,
                "trusted_proxies": ["172.30.32.0/23"],
            }
        )

        manager._OnHaConnected()

        self.assertEqual(connection.Messages, [])
        self.assertTrue(manager.HttpConfigUpdateRequested)
        self.assertTrue(manager.HttpConfigUpdateWakeEvent.is_set())


if __name__ == "__main__":
    unittest.main()
