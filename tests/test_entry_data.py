"""Tests for entry_data module (OpenEMSWebSocketConnection)."""

import pytest

from custom_components.openems.entry_data import OpenEMSWebSocketConnection


async def test_login_raises_on_not_connected() -> None:
    """Test that login_to_server raises ConnectionError when not connected."""
    conn_props = {
        "host": "localhost",
        "password": "p",
        "type": "direct_edge",
        "url": None,
        "username": "u",
    }
    conn = OpenEMSWebSocketConnection(conn_props)
    conn.rpc_server.connected = False
    with pytest.raises(ConnectionError):
        await conn.login_to_server()
    await conn.rpc_server.close()
