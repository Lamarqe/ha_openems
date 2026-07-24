"""Unit tests for the OpenEMS integration helpers.

These tests exercise the OpenEMS channel/property/component logic without
network calls by using small dummy objects.
"""
from datetime import time
from unittest.mock import MagicMock

from yarl import URL

from custom_components.openems import openems
from custom_components.openems.helpers import wrap_jsonrpc


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
        assert any(p.name == "_PropertyEnabledCharging" for p in comp.boolean_properties)

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
    prop = openems.OpenEMSEnumProperty(component=comp, channel_json=enum_json, options=options)
    prop.handle_data_update("_PropertyChargeMode", "EXCESS_POWER")
    assert prop.current_option == "EXCESS_POWER"
    # unknown option is ignored
    prop.handle_data_update("_PropertyChargeMode", "UNKNOWN")
    assert prop.current_option is None


def test_time_property_behavior() -> None:
    """Test time property parsing."""
    comp = _make_component()
    time_json = {"id": "_PropertyManualTargetTime", "type": "STRING", "unit": "u"}
    prop = openems.OpenEMSTimeProperty(component=comp, channel_json=time_json)
    prop.handle_data_update("_PropertyManualTargetTime", "12:34")
    assert prop.native_value == time(12, 34)
    # invalid time string
    prop.handle_data_update("_PropertyManualTargetTime", "nope")
    assert prop.native_value is None


def test_number_property_multiplier_and_limits() -> None:
    """Test number property applies multiplier on data update."""
    comp = _make_component()
    num_json = {"id": "_PropertyEnergySessionLimit", "type": "INTEGER", "unit": "W"}
    prop = openems.OpenEMSNumberProperty(component=comp, channel_json=num_json)
    prop.set_multiplier_def("2")
    prop.set_limit_def({"lower": "1", "upper": "100"})

    assert prop.multiplier == 2.0
    assert prop.lower_limit >= 0
    assert prop.upper_limit >= prop.lower_limit

    prop.handle_data_update("comp/_PropertyEnergySessionLimit", 10)
    assert prop.current_value == 20
