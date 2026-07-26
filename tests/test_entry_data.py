"""Tests for entry_data module (OpenEMSWebSocketConnection)."""

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import jsonrpc_base.jsonrpc
import pytest

from custom_components.openems.const import CURRENT_DATA_TIMEOUT_SECONDS
from custom_components.openems.entry_data import OpenEMSWebSocketConnection

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
