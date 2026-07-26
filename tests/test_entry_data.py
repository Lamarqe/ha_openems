"""Tests for entry_data module (OpenEMSWebSocketConnection and OpenEMSConfigReader)."""

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import jsonrpc_base.jsonrpc
import pytest

from custom_components.openems.const import CURRENT_DATA_TIMEOUT_SECONDS
from custom_components.openems.entry_data import (
    EdgeNotDefinedError,
    OpenEMSConfigReader,
    OpenEMSWebSocketConnection,
)

_CONN_PROPS = {
    "host": "testhost",
    "password": "pass",
    "type": "direct_edge",
    "url": None,
    "username": "user",
}


def _make_conn() -> tuple[OpenEMSWebSocketConnection, MagicMock]:
    """Return (connection, mock_rpc_server) without creating a real aiohttp session."""
    mock_server = MagicMock()
    mock_server.close = AsyncMock()
    mock_server.connected = True
    with patch(
        "custom_components.openems.entry_data.jsonrpc_websocket.Server",
        return_value=mock_server,
    ):
        conn = OpenEMSWebSocketConnection(_CONN_PROPS)
    return conn, mock_server


def _done_task_mock() -> MagicMock:
    """Return a mock asyncio.Task that reports done=True."""
    t = MagicMock(spec=asyncio.Task)
    t.done.return_value = True
    return t


def _pending_task_mock() -> MagicMock:
    """Return a mock asyncio.Task that reports done=False initially."""
    t = MagicMock(spec=asyncio.Task)
    t.done.return_value = False
    return t


# ---------------------------------------------------------------------------
# login_to_server
# ---------------------------------------------------------------------------


async def test_login_raises_on_not_connected() -> None:
    """login_to_server raises ConnectionError when the server is not connected."""
    conn, mock_server = _make_conn()
    mock_server.connected = False
    with pytest.raises(ConnectionError):
        await conn.login_to_server()


# ---------------------------------------------------------------------------
# enable_reconnect
# ---------------------------------------------------------------------------


async def test_enable_reconnect_creates_task() -> None:
    """enable_reconnect stores a running asyncio.Task and passes the callback."""
    conn, _ = _make_conn()
    callback = MagicMock()

    with patch.object(conn, "_reconnect_forever", new=AsyncMock()) as mock_rf:
        conn.enable_reconnect(callback)

        assert conn.reconnect_task is not None
        assert isinstance(conn.reconnect_task, asyncio.Task)
        mock_rf.assert_called_once_with(callback)

    conn.reconnect_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await conn.reconnect_task


# ---------------------------------------------------------------------------
# _reconnect_forever - connection-loss detection
# ---------------------------------------------------------------------------


async def test_reconnect_forever_no_task_calls_callback() -> None:
    """When rpc_server_task is None the callback is invoked immediately."""
    conn, _ = _make_conn()
    callback = MagicMock()
    conn.rpc_server_task = None

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    with (
        patch("asyncio.sleep", fake_sleep),
        patch.object(conn, "connect_to_server", AsyncMock()),
        patch.object(conn, "login_to_server", AsyncMock()),
    ):
        conn.enable_reconnect(callback)
        with contextlib.suppress(asyncio.CancelledError):
            await conn.reconnect_task

    callback.assert_called_once()


async def test_reconnect_forever_done_task_calls_callback() -> None:
    """When rpc_server_task is done the callback is invoked; close is not called."""
    conn, mock_server = _make_conn()
    callback = MagicMock()
    conn.rpc_server_task = _done_task_mock()
    conn.notify_data_received()  # mark data as recently received

    async def fake_wait(tasks, *, timeout=None):
        return (set(tasks), set())  # all tasks reported as done

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    with (
        patch("asyncio.wait", fake_wait),
        patch("asyncio.sleep", fake_sleep),
        patch.object(conn, "connect_to_server", AsyncMock()),
        patch.object(conn, "login_to_server", AsyncMock()),
    ):
        conn.enable_reconnect(callback)
        with contextlib.suppress(asyncio.CancelledError):
            await conn.reconnect_task

    callback.assert_called_once()
    mock_server.close.assert_not_called()


async def test_reconnect_forever_data_timeout_calls_close() -> None:
    """When data has not been received within the timeout, close() is called."""
    conn, mock_server = _make_conn()
    callback = MagicMock()

    pending_task = _pending_task_mock()
    conn.rpc_server_task = pending_task

    stale_time = time.time() - CURRENT_DATA_TIMEOUT_SECONDS - 1
    with patch(
        "custom_components.openems.entry_data.time.time", return_value=stale_time
    ):
        conn.notify_data_received()

    async def fake_close() -> None:
        # After close, the server task is considered done so the inner loop can break.
        pending_task.done.return_value = True

    mock_server.close = AsyncMock(side_effect=fake_close)

    async def fake_wait(tasks, *, timeout=None):
        return (set(), set(tasks))  # simulate wait timeout: nothing done yet

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    with (
        patch("asyncio.wait", fake_wait),
        patch("asyncio.sleep", fake_sleep),
        patch.object(conn, "connect_to_server", AsyncMock()),
        patch.object(conn, "login_to_server", AsyncMock()),
    ):
        conn.enable_reconnect(callback)
        with contextlib.suppress(asyncio.CancelledError):
            await conn.reconnect_task

    mock_server.close.assert_called_once()
    callback.assert_called_once()


# ---------------------------------------------------------------------------
# _reconnect_forever - reconnect attempts
# ---------------------------------------------------------------------------


async def test_reconnect_forever_calls_connect_and_login_after_loss() -> None:
    """After a connection loss connect_to_server and login_to_server are called."""
    conn, _ = _make_conn()
    callback = MagicMock()
    conn.rpc_server_task = _done_task_mock()
    conn.notify_data_received()

    connect_mock = AsyncMock()
    login_mock = AsyncMock(side_effect=asyncio.CancelledError)

    async def fake_wait(tasks, *, timeout=None):
        return (set(tasks), set())

    async def fake_sleep(_seconds: float) -> None:
        pass  # let the reconnect attempt proceed

    with (
        patch("asyncio.wait", fake_wait),
        patch("asyncio.sleep", fake_sleep),
        patch.object(conn, "connect_to_server", connect_mock),
        patch.object(conn, "login_to_server", login_mock),
    ):
        conn.enable_reconnect(callback)
        with contextlib.suppress(asyncio.CancelledError):
            await conn.reconnect_task

    callback.assert_called_once()
    connect_mock.assert_called_once()
    login_mock.assert_called_once()


async def test_reconnect_forever_extra_sleep_on_transport_error() -> None:
    """A TransportError during reconnect triggers an extra 10-second sleep."""
    conn, _ = _make_conn()
    callback = MagicMock()
    conn.rpc_server_task = _done_task_mock()
    conn.notify_data_received()

    connect_mock = AsyncMock(
        side_effect=jsonrpc_base.jsonrpc.TransportError("connection refused")
    )

    sleep_calls: list[float] = []

    async def fake_wait(tasks, *, timeout=None):
        return (set(tasks), set())

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    with (
        patch("asyncio.wait", fake_wait),
        patch("asyncio.sleep", fake_sleep),
        patch.object(conn, "connect_to_server", connect_mock),
        patch.object(conn, "login_to_server", AsyncMock()),
    ):
        conn.enable_reconnect(callback)
        with contextlib.suppress(asyncio.CancelledError):
            await conn.reconnect_task

    # First sleep: post-callback wait. Second sleep: extra delay after failure.
    assert sleep_calls == [10, 10]
    connect_mock.assert_called_once()


async def test_reconnect_forever_extra_sleep_on_protocol_error() -> None:
    """A ProtocolError during reconnect triggers an extra 10-second sleep."""
    conn, _ = _make_conn()
    callback = MagicMock()
    conn.rpc_server_task = _done_task_mock()
    conn.notify_data_received()

    connect_mock = AsyncMock(
        side_effect=jsonrpc_base.jsonrpc.ProtocolError(500, "Internal error", [])
    )

    sleep_calls: list[float] = []

    async def fake_wait(tasks, *, timeout=None):
        return (set(tasks), set())

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    with (
        patch("asyncio.wait", fake_wait),
        patch("asyncio.sleep", fake_sleep),
        patch.object(conn, "connect_to_server", connect_mock),
        patch.object(conn, "login_to_server", AsyncMock()),
    ):
        conn.enable_reconnect(callback)
        with contextlib.suppress(asyncio.CancelledError):
            await conn.reconnect_task

    assert sleep_calls == [10, 10]
    connect_mock.assert_called_once()


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


async def test_stop_cancels_reconnect_task_and_closes() -> None:
    """stop() cancels the reconnect task and closes the rpc_server."""
    conn, mock_server = _make_conn()
    task_started = asyncio.Event()

    async def block_forever(_callback) -> None:
        task_started.set()
        await asyncio.Event().wait()

    with patch.object(conn, "_reconnect_forever", block_forever):
        conn.enable_reconnect(MagicMock())
        await task_started.wait()
        await conn.stop()

    assert conn.reconnect_task is None
    mock_server.close.assert_called_once()


async def test_stop_without_reconnect_task_only_closes() -> None:
    """stop() without a reconnect task still closes the rpc_server."""
    conn, mock_server = _make_conn()
    assert conn.reconnect_task is None

    await conn.stop()

    mock_server.close.assert_called_once()


# ===========================================================================
# OpenEMSConfigReader tests
# Test data extracted from tests/data/traffic.jsonrpc
# ===========================================================================

# ---------------------------------------------------------------------------
# Test data (extracted from traffic.jsonrpc)
# ---------------------------------------------------------------------------

# From the getEdges response in traffic.jsonrpc
_EDGES_RESPONSE = {
    "edges": [
        {
            "id": "0",
            "comment": "",
            "producttype": "",
            "version": "2026.7.2",
            "role": "owner",
            "isOnline": True,
            "lastmessage": "2026-07-26T18:48:32.084536771Z",
            "sumState": "OK",
        }
    ]
}

# Simplified getEdgeConfig components (from traffic.jsonrpc ccfa3f81 response)
_GET_EDGE_CONFIG_RESPONSE = {
    "payload": {
        "result": {
            "components": {
                "_appManager": {
                    "alias": "Core.AppManager",
                    "factoryId": "Core.AppManager",
                    "properties": {},
                },
                "_componentManager": {
                    "alias": "Core.ComponentManager",
                    "factoryId": "Core.ComponentManager",
                    "properties": {},
                },
            }
        }
    }
}

# From the getChannelsOfComponent response for _appManager (id a98a0ebe in traffic.jsonrpc)
_APP_MANAGER_CHANNELS_RESPONSE = {
    "payload": {
        "result": {
            "channels": [
                {
                    "id": "WrongAppConfiguration",
                    "accessMode": "RO",
                    "persistencePriority": "HIGH",
                    "text": "App manager configuration is wrong",
                    "type": "BOOLEAN",
                    "unit": "",
                    "category": "STATE",
                    "level": "WARNING",
                },
                {
                    "id": "DefectiveApp",
                    "accessMode": "RO",
                    "persistencePriority": "HIGH",
                    "text": "Defective app detected",
                    "type": "BOOLEAN",
                    "unit": "",
                    "category": "STATE",
                    "level": "INFO",
                },
                {
                    "id": "State",
                    "accessMode": "RO",
                    "persistencePriority": "VERY_HIGH",
                    "text": "0:Ok, 1:Info, 2:Warning, 3:Fault",
                    "type": "INTEGER",
                    "unit": "",
                    "category": "ENUM",
                    "options": {"Ok": 0, "Info": 1, "Warning": 2, "Fault": 3},
                },
            ]
        }
    }
}

# From the getChannelsOfComponent response for _componentManager (id 7e5a360a in traffic.jsonrpc)
_COMPONENT_MANAGER_CHANNELS_RESPONSE = {
    "payload": {
        "result": {
            "channels": [
                {
                    "id": "ConfigNotActivated",
                    "accessMode": "RO",
                    "persistencePriority": "HIGH",
                    "text": "A configured OpenEMS Component was not activated",
                    "type": "BOOLEAN",
                    "unit": "",
                    "category": "STATE",
                    "level": "FAULT",
                },
                {
                    "id": "State",
                    "accessMode": "RO",
                    "persistencePriority": "VERY_HIGH",
                    "text": "0:Ok, 1:Info, 2:Warning, 3:Fault",
                    "type": "INTEGER",
                    "unit": "",
                    "category": "ENUM",
                    "options": {"Ok": 0, "Info": 1, "Warning": 2, "Fault": 3},
                },
            ]
        }
    }
}

# From the currentData push (edgeRpc method=currentData) in traffic.jsonrpc
_CURRENT_DATA_PARAMS = {
    "_host/Hostname": "fems1",
    "battery0/_PropertyAlias": "Battery",
    "ess0/_PropertyAlias": "Storage system",
}


class _MockJsonrpcServer:
    """Minimal stand-in for jsonrpc_websocket.Server used by get_channel_values_via_websocket.

    jsonrpc_base.Server separates notification-handler registration (via __setattr__)
    from outgoing RPC calls (via __getattr__ → Method proxy).  This class replicates
    that behaviour so that:
      - ``server.edgeRpc = callback``  stores the callback for later use
      - ``await server.edgeRpc(...)``  returns an AsyncMock (simulates sending the RPC)
    """

    def __init__(self) -> None:
        object.__setattr__(self, "_edgeRpc_handler", None)
        object.__setattr__(self, "_edgeRpc_send", AsyncMock())
        self.ws_connect = AsyncMock()
        self.authenticateWithPassword = AsyncMock()
        self.close = AsyncMock()
        self.subscribeEdges = AsyncMock()

    def __setattr__(self, name: str, value: object) -> None:
        if name == "edgeRpc":
            # Production code stores a notification handler this way.
            object.__setattr__(self, "_edgeRpc_handler", value)
        else:
            object.__setattr__(self, name, value)

    def __getattr__(self, name: str) -> object:
        if name == "edgeRpc":
            return object.__getattribute__(self, "_edgeRpc_send")
        raise AttributeError(name)

    def push_notification(self, method: str, params: dict) -> None:
        """Simulate a server-pushed edgeRpc notification (e.g. currentData)."""
        handler = object.__getattribute__(self, "_edgeRpc_handler")
        if handler is not None:
            handler(payload={"method": method, "params": params})

    def push_current_data(self, params: dict) -> None:
        """Convenience wrapper: push a currentData notification."""
        self.push_notification("currentData", params)


# ---------------------------------------------------------------------------
# read_edges
# ---------------------------------------------------------------------------


async def test_read_edges_returns_edges_dict() -> None:
    """read_edges returns the full dict from getEdges, matching traffic data."""
    conn, mock_server = _make_conn()
    mock_server.getEdges = AsyncMock(return_value=_EDGES_RESPONSE)

    reader = OpenEMSConfigReader(conn)
    result = await reader.read_edges()

    assert result == _EDGES_RESPONSE
    mock_server.getEdges.assert_called_once_with(page=0, limit=20, searchParams={})


async def test_read_edges_edge_fields_match_traffic() -> None:
    """Edge entry from read_edges contains the id, version and isOnline seen in traffic."""
    conn, mock_server = _make_conn()
    mock_server.getEdges = AsyncMock(return_value=_EDGES_RESPONSE)

    reader = OpenEMSConfigReader(conn)
    result = await reader.read_edges()

    edge = result["edges"][0]
    assert edge["id"] == "0"
    assert edge["version"] == "2026.7.2"
    assert edge["isOnline"] is True
    assert edge["role"] == "owner"
    assert edge["sumState"] == "OK"


# ---------------------------------------------------------------------------
# read_edge_components
# ---------------------------------------------------------------------------


async def test_read_edge_components_raises_without_edge_id() -> None:
    """read_edge_components raises EdgeNotDefinedError when no edge_id is set."""
    conn, _ = _make_conn()
    reader = OpenEMSConfigReader(conn)
    with pytest.raises(EdgeNotDefinedError):
        await reader.read_edge_components()


async def test_read_edge_components_returns_components_with_channels() -> None:
    """read_edge_components returns a dict with all components and their channels."""
    conn, mock_server = _make_conn()
    mock_server.edgeRpc = AsyncMock(
        side_effect=[
            _GET_EDGE_CONFIG_RESPONSE,
            _APP_MANAGER_CHANNELS_RESPONSE,
            _COMPONENT_MANAGER_CHANNELS_RESPONSE,
        ]
    )

    reader = OpenEMSConfigReader(conn, edge_id="0")
    with patch.object(
        reader, "get_channel_values_via_websocket", AsyncMock(return_value={})
    ):
        result = await reader.read_edge_components()

    assert "_appManager" in result
    assert "_componentManager" in result
    assert "channels" in result["_appManager"]
    assert "channels" in result["_componentManager"]


async def test_read_edge_components_channel_ids_match_traffic() -> None:
    """Channels in the returned components match the ids from the traffic capture."""
    conn, mock_server = _make_conn()
    mock_server.edgeRpc = AsyncMock(
        side_effect=[
            _GET_EDGE_CONFIG_RESPONSE,
            _APP_MANAGER_CHANNELS_RESPONSE,
            _COMPONENT_MANAGER_CHANNELS_RESPONSE,
        ]
    )

    reader = OpenEMSConfigReader(conn, edge_id="0")
    with patch.object(
        reader, "get_channel_values_via_websocket", AsyncMock(return_value={})
    ):
        result = await reader.read_edge_components()

    app_ids = {ch["id"] for ch in result["_appManager"]["channels"]}
    assert {"WrongAppConfiguration", "DefectiveApp", "State"} == app_ids

    comp_ids = {ch["id"] for ch in result["_componentManager"]["channels"]}
    assert {"ConfigNotActivated", "State"} == comp_ids


# ---------------------------------------------------------------------------
# _read_edge_channels
# ---------------------------------------------------------------------------


async def test_read_edge_channels_adds_channels_key() -> None:
    """_read_edge_channels adds a 'channels' list to each component dict."""
    conn, mock_server = _make_conn()
    mock_server.edgeRpc = AsyncMock(
        side_effect=[
            _APP_MANAGER_CHANNELS_RESPONSE,
            _COMPONENT_MANAGER_CHANNELS_RESPONSE,
        ]
    )

    reader = OpenEMSConfigReader(conn, edge_id="0")
    components: dict = {
        "_appManager": {
            "alias": "Core.AppManager",
            "factoryId": "Core.AppManager",
            "properties": {},
        },
        "_componentManager": {
            "alias": "Core.ComponentManager",
            "factoryId": "Core.ComponentManager",
            "properties": {},
        },
    }

    await reader.read_edge_channels(components)

    assert "channels" in components["_appManager"]
    assert len(components["_appManager"]["channels"]) == 3
    assert "channels" in components["_componentManager"]
    assert len(components["_componentManager"]["channels"]) == 2


async def test_read_edge_channels_skips_component_on_transport_error() -> None:
    """_read_edge_channels removes a component when its channel fetch raises TransportError."""
    conn, mock_server = _make_conn()
    mock_server.edgeRpc = AsyncMock(
        side_effect=[
            _APP_MANAGER_CHANNELS_RESPONSE,
            jsonrpc_base.jsonrpc.TransportError("simulated transport failure"),
        ]
    )

    reader = OpenEMSConfigReader(conn, edge_id="0")
    components: dict = {
        "_appManager": {
            "alias": "Core.AppManager",
            "factoryId": "Core.AppManager",
            "properties": {},
        },
        "_componentManager": {
            "alias": "Core.ComponentManager",
            "factoryId": "Core.ComponentManager",
            "properties": {},
        },
    }

    await reader.read_edge_channels(components)

    assert "_appManager" in components
    assert "_componentManager" not in components


async def test_read_edge_channels_skips_component_on_protocol_error() -> None:
    """_read_edge_channels removes a component when its channel fetch raises ProtocolError."""
    conn, mock_server = _make_conn()
    mock_server.edgeRpc = AsyncMock(
        side_effect=[
            jsonrpc_base.jsonrpc.ProtocolError(500, "Internal error", []),
            _COMPONENT_MANAGER_CHANNELS_RESPONSE,
        ]
    )

    reader = OpenEMSConfigReader(conn, edge_id="0")
    components: dict = {
        "_appManager": {
            "alias": "Core.AppManager",
            "factoryId": "Core.AppManager",
            "properties": {},
        },
        "_componentManager": {
            "alias": "Core.ComponentManager",
            "factoryId": "Core.ComponentManager",
            "properties": {},
        },
    }

    await reader.read_edge_channels(components)

    assert "_appManager" not in components
    assert "_componentManager" in components


# ---------------------------------------------------------------------------
# _read_component_info_channels
# ---------------------------------------------------------------------------


async def test_read_component_info_channels_stores_hostname() -> None:
    """Hostname from currentData (_host/Hostname) is stored on the _host component."""
    conn, _ = _make_conn()
    reader = OpenEMSConfigReader(conn, edge_id="0")

    components: dict = {
        "_host": {"channels": [{"id": "Hostname"}, {"id": "OsVersion"}]},
    }

    with patch.object(
        reader,
        "get_channel_values_via_websocket",
        AsyncMock(return_value={"_host/Hostname": "fems1"}),
    ):
        await reader.read_component_info_channels(components)

    assert components["_host"]["Hostname"] == "fems1"


async def test_read_component_info_channels_stores_alias() -> None:
    """_PropertyAlias from currentData is stored on the matching component."""
    conn, _ = _make_conn()
    reader = OpenEMSConfigReader(conn, edge_id="0")

    components: dict = {
        "battery0": {"channels": [{"id": "_PropertyAlias"}, {"id": "State"}]},
    }

    with patch.object(
        reader,
        "get_channel_values_via_websocket",
        AsyncMock(return_value={"battery0/_PropertyAlias": "Battery"}),
    ):
        await reader.read_component_info_channels(components)

    assert components["battery0"]["_PropertyAlias"] == "Battery"


async def test_read_component_info_channels_request_includes_hostname_and_aliases() -> (
    None
):
    """get_channel_values_via_websocket is called with _host/Hostname and _PropertyAlias channels."""
    conn, _ = _make_conn()
    reader = OpenEMSConfigReader(conn, edge_id="0")

    components: dict = {
        "_host": {"channels": [{"id": "Hostname"}]},
        "battery0": {"channels": [{"id": "_PropertyAlias"}, {"id": "State"}]},
        # no _PropertyAlias → not requested
        "ess0": {"channels": [{"id": "State"}]},
    }

    mock_get = AsyncMock(return_value={})
    with patch.object(reader, "get_channel_values_via_websocket", mock_get):
        await reader.read_component_info_channels(components)

    requested: list[str] = mock_get.call_args[0][0]
    assert "_host/Hostname" in requested
    assert "battery0/_PropertyAlias" in requested
    assert "ess0/_PropertyAlias" not in requested


async def test_read_component_info_channels_ignores_unknown_component() -> None:
    """Values for components not in the components dict are silently ignored."""
    conn, _ = _make_conn()
    reader = OpenEMSConfigReader(conn, edge_id="0")

    components: dict = {
        "battery0": {"channels": [{"id": "_PropertyAlias"}]},
    }

    # currentData includes a key for a component that isn't in components
    with patch.object(
        reader,
        "get_channel_values_via_websocket",
        AsyncMock(
            return_value={
                "battery0/_PropertyAlias": "Battery",
                "unknown0/_PropertyAlias": "Ghost",
            }
        ),
    ):
        await reader.read_component_info_channels(components)

    assert "unknown0" not in components
    assert components["battery0"]["_PropertyAlias"] == "Battery"


# ---------------------------------------------------------------------------
# get_channel_values_via_websocket
# ---------------------------------------------------------------------------


async def test_get_channel_values_raises_without_edge_id() -> None:
    """get_channel_values_via_websocket raises EdgeNotDefinedError when no edge_id is set."""
    conn, _ = _make_conn()
    reader = OpenEMSConfigReader(conn)
    with pytest.raises(EdgeNotDefinedError):
        await reader.get_channel_values_via_websocket(["_host/Hostname"])


async def test_get_channel_values_returns_current_data() -> None:
    """get_channel_values_via_websocket returns the params from the currentData push.

    Data from the currentData edgeRpc notification in traffic.jsonrpc.
    """
    conn, _ = _make_conn()
    mock_server = _MockJsonrpcServer()

    async def _subscribe_side_effect(edges: list) -> None:
        # The server responds to subscription by immediately pushing currentData.
        mock_server.push_current_data(_CURRENT_DATA_PARAMS)

    mock_server.subscribeEdges = AsyncMock(side_effect=_subscribe_side_effect)

    with patch(
        "custom_components.openems.entry_data.jsonrpc_websocket.Server",
        return_value=mock_server,
    ):
        reader = OpenEMSConfigReader(conn, edge_id="0")
        result = await reader.get_channel_values_via_websocket(
            list(_CURRENT_DATA_PARAMS)
        )

    assert result == _CURRENT_DATA_PARAMS
    mock_server.ws_connect.assert_called_once()
    mock_server.authenticateWithPassword.assert_called_once_with(
        username="user", password="pass"
    )
    mock_server.close.assert_called_once()


async def test_get_channel_values_ignores_non_current_data_push() -> None:
    """Pushed edgeRpc notifications with method != 'currentData' are ignored."""
    conn, _ = _make_conn()
    mock_server = _MockJsonrpcServer()

    async def _subscribe_side_effect(edges: list) -> None:
        # First push an unrelated method - must be ignored.
        mock_server.push_notification("someOtherMethod", {"irrelevant": True})
        # Then push the real currentData.
        mock_server.push_current_data(_CURRENT_DATA_PARAMS)

    mock_server.subscribeEdges = AsyncMock(side_effect=_subscribe_side_effect)

    with patch(
        "custom_components.openems.entry_data.jsonrpc_websocket.Server",
        return_value=mock_server,
    ):
        reader = OpenEMSConfigReader(conn, edge_id="0")
        result = await reader.get_channel_values_via_websocket(
            list(_CURRENT_DATA_PARAMS)
        )

    assert result == _CURRENT_DATA_PARAMS


async def test_get_channel_values_timeout_raises() -> None:
    """get_channel_values_via_websocket raises TimeoutError when no data arrives."""
    conn, _ = _make_conn()
    mock_server = _MockJsonrpcServer()
    # subscribeEdges does nothing → currentData is never pushed → wait_for times out

    async def _fake_wait_for(coro: object, *, timeout: float) -> None:
        # Close the coroutine to avoid 'coroutine never awaited' warnings.
        if hasattr(coro, "close"):
            coro.close()
        raise TimeoutError

    with (
        patch(
            "custom_components.openems.entry_data.jsonrpc_websocket.Server",
            return_value=mock_server,
        ),
        patch("asyncio.wait_for", new=_fake_wait_for),
    ):
        reader = OpenEMSConfigReader(conn, edge_id="0")
        with pytest.raises(TimeoutError):
            await reader.get_channel_values_via_websocket(["_host/Hostname"])
