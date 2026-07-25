"""Unit tests for the OpenEMS integration helpers.

These tests exercise the OpenEMS channel/property/component logic without
network calls by using small dummy objects.
"""
from datetime import time
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from yarl import URL

from custom_components.openems import openems
from custom_components.openems.const import SLASH_ESC
from custom_components.openems.helpers import wrap_jsonrpc
from custom_components.openems.helpers_openems import prepare_ref_value


def _make_backend():
    """Return a minimal mock backend for OpenEMSEdge construction."""
    backend = MagicMock()
    backend.multi_edge = False
    backend.connection.conn_url = URL("ws://localhost:8085/openems-backend-ui")
    backend.connection.notify_data_received = MagicMock()
    return backend


def _make_component(name="ctrlEvcs1"):
    """Return a minimal mock component for property construction."""
    comp = MagicMock()
    comp.name = name
    comp.properties = {}
    comp.edge.hostname = "host"
    comp.edge.id = "edgeid"
    return comp


def make_channel_json(id_, type_, unit="unit"):
    """Create a simple channel JSON dict for tests."""
    return {"id": id_, "type": type_, "unit": unit}


def test_wrap_jsonrpc() -> None:
    """Test that wrap_jsonrpc produces a valid JSON-RPC envelope."""
    env = wrap_jsonrpc("testMethod", a=1)
    assert env["method"] == "testMethod"
    assert "id" in env
    assert env["params"]["a"] == 1


def test_edge_dispatch_currentData() -> None:
    """Test that currentData updates current_channel_data on the edge."""
    backend = _make_backend()
    component_config = {"_host": {"Hostname": "h1"}}
    edge = openems.OpenEMSEdge(backend, "edge-1", component_config)
    try:
        edge._registered_handlers["a/b"] = set()
        edge.currentData({"a/b": 1})
        assert edge.current_channel_data == {"a/b": 1}
    finally:
        edge.stop()


def test_component_boolean_property() -> None:
    """Test that a BOOLEAN _Property channel is created and handles data."""
    backend = _make_backend()
    comp_json = {
        "_PropertyAlias": "alias",
        "properties": {},
        "channels": [
            make_channel_json("_PropertyEnabledCharging", "BOOLEAN"),
            make_channel_json("SomeSensor", "INTEGER"),
        ],
    }
    component_config = {"_host": {"Hostname": "h1"}, "comp1": comp_json}
    edge = openems.OpenEMSEdge(backend, "edge-1", component_config)
    try:
        assert "comp1" in edge.components
        comp = edge.components["comp1"]
        assert any(
            p.name == "_PropertyEnabledCharging" for p in comp.boolean_properties)

        prop = comp.boolean_properties[0]
        called = False

        def cb():
            nonlocal called
            called = True

        prop.register_callback(cb)
        edge.currentData({"comp1/_PropertyEnabledCharging": 1})
        assert prop.current_value is True
        assert called
        assert "/_PropertyEnabledCharging" in prop.unique_id()
    finally:
        edge.stop()


def test_enum_property_behavior() -> None:
    """Test enum property selection and update."""
    comp = _make_component()
    enum_json = {"id": "_PropertyChargeMode", "type": "STRING", "unit": "u"}
    options = ["EXCESS_POWER", "MANUAL", "OFF"]
    prop = openems.OpenEMSEnumProperty(
        component=comp, channel_json=enum_json, options=options)
    prop.handle_data_update("_PropertyChargeMode", "EXCESS_POWER")
    assert prop.current_option == "EXCESS_POWER"
    # unknown option is ignored
    prop.handle_data_update("_PropertyChargeMode", "UNKNOWN")
    assert prop.current_option is None


def test_time_property_behavior() -> None:
    """Test time property parsing."""
    comp = _make_component()
    time_json = {"id": "_PropertyManualTargetTime",
                 "type": "STRING", "unit": "u"}
    prop = openems.OpenEMSTimeProperty(component=comp, channel_json=time_json)
    prop.handle_data_update("_PropertyManualTargetTime", "12:34")
    assert prop.native_value == time(12, 34)
    # invalid time string
    prop.handle_data_update("_PropertyManualTargetTime", "nope")
    assert prop.native_value is None


def test_number_property_multiplier_and_limits() -> None:
    """Test number property applies multiplier on data update."""
    comp = _make_component()
    num_json = {"id": "_PropertyEnergySessionLimit",
                "type": "INTEGER", "unit": "W"}
    prop = openems.OpenEMSNumberProperty(component=comp, channel_json=num_json)
    prop.set_multiplier_def("2")
    prop.set_limit_def({"lower": "1", "upper": "100"})

    assert prop.multiplier == 2.0
    assert prop.lower_limit >= 0
    assert prop.upper_limit >= prop.lower_limit

    prop.handle_data_update("comp/_PropertyEnergySessionLimit", 10)
    assert prop.current_value == 20


def _make_component_with_edge(name: str = "ctrlEvcs1"):
    """Return a mock component with a real DummyEdge supporting register/unregister."""
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
    comp = _make_component_with_edge()
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
        comp = _make_component_with_edge()
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
    comp = _make_component_with_edge("evcs1")
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


async def test_entity_lifecycle_and_unique_id(hass: HomeAssistant, dummy_backend) -> None:
    """Test entities are prepared and unique_ids are stable."""
    comp = {
        "_PropertyAlias": "a",
        "properties": {},
        "channels": [
            {"id": "ch1", "type": "INTEGER", "unit": "u"},
            {"id": "ch2", "type": "DOUBLE", "unit": "kWh"},
        ],
    }
    component_config = {"_host": {"Hostname": "edge1"}, "c1": comp}
    edge = openems.OpenEMSEdge(dummy_backend, "edge1", component_config)
    try:
        comp_obj = edge.components["c1"]
        assert len(comp_obj.sensors) >= 1
        for s in comp_obj.sensors:
            assert s.unique_id().startswith("edge1")
    finally:
        edge.stop()
