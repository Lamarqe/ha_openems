"""Extra unit tests for the OpenEMS integration.

These tests add coverage for channels, number property template references
and connection login behavior.
"""

from unittest.mock import MagicMock

import pytest
from yarl import URL

from custom_components.openems import openems
from custom_components.openems.const import SLASH_ESC
from custom_components.openems.entry_data import OpenEMSWebSocketConnection
from custom_components.openems.helpers_openems import prepare_ref_value


def _make_backend():
    backend = MagicMock()
    backend.multi_edge = False
    backend.connection.conn_url = URL("ws://localhost:8085/openems-backend-ui")
    backend.connection.notify_data_received = MagicMock()
    return backend


def _make_component(name: str = "ctrlEvcs1"):
    """Create a minimal component-like object for property tests."""
    comp = MagicMock()
    comp.name = name
    comp.json_properties = {}

    class DummyEdge:
        def __init__(self) -> None:
            self.hostname = "h"
            self.id = "edge"
            self._registered_handlers: dict = {}

        def register_channel(self, channel_names, handler):
            for ch_name in channel_names:
                self._registered_handlers.setdefault(
                    ch_name, set()).add(handler)

        def unregister_channel(self, handler):
            for ch_name, handlers in list(self._registered_handlers.items()):
                handlers.discard(handler)

    comp.edge = DummyEdge()
    return comp


def test_channel_register_unregister_and_notify() -> None:
    """Test register and unregister behavior for channels and notify callback."""
    comp = _make_component()
    chan_json = {"id": "SomeSensor", "type": "INTEGER", "unit": "u"}
    chan = openems.OpenEMSChannel(component=comp, channel_json=chan_json)

    called = False

    def cb():
        nonlocal called
        called = True

    chan.register_callback(cb)
    chan.notify_ha()
    assert called
    chan.unregister_callback()
    assert chan.callback is None


def test_set_unavailable_clears_values() -> None:
    """Test that set_unavailable calls handle_data_update(None) for active channels."""
    backend = _make_backend()
    component_config = {"_host": {"Hostname": "h1"}}
    edge = openems.OpenEMSEdge(backend, "e1", component_config)
    try:
        comp = _make_component()
        chan_json = {"id": "S", "type": "INTEGER", "unit": "u"}
        ch = openems.OpenEMSChannel(component=comp, channel_json=chan_json)

        # Register the channel directly in the edge handler map
        edge._registered_handlers["c1/S"] = {ch}
        edge.current_channel_data = {"c1/S": 5}

        edge.set_unavailable()
        assert ch.current_value is None
    finally:
        edge.stop()


def test_number_property_with_template_references() -> None:
    """Test OpenEMSNumberProperty with template references to other channels."""
    comp = _make_component("evcs1")
    # needed for $evcs.id resolution
    comp.json_properties["evcs.id"] = comp.name

    num_json = {"id": "_PropertyForceChargeMinPower",
                "type": "INTEGER", "unit": "W"}
    num_prop = openems.OpenEMSNumberProperty(
        component=comp, channel_json=num_json)

    mult_def = "{{$evcs.id/Phases}}"
    _, refs = prepare_ref_value(mult_def, comp)
    assert refs  # has external references

    num_prop.set_multiplier_def(mult_def)
    num_prop.set_limit_def({"lower": "1", "upper": "100"})

    ref_key = "evcs1" + SLASH_ESC + "Phases"
    assert ref_key in num_prop.reference_channels

    num_prop.reference_channels[ref_key] = 3
    num_prop._update_config()
    assert num_prop.multiplier == 3.0


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
