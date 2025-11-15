"""Unit tests for the OpenEMS integration helpers.

These tests exercise the OpenEMS channel/property/component logic without
network calls by using small dummy server objects.
"""
from datetime import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from homeassistant.components.openems import openems


class DummyRpcServer:
    """Dummy RPC server used in tests to record calls and return fake responses."""

    def __init__(self) -> None:
        """Initialize the dummy RPC server state for tests."""
        self.connected = True
        self.calls = []

    async def edgeRpc(self, edgeId=None, payload=None):
        """Record an edgeRpc call and return a fake response."""
        self.calls.append((edgeId, payload))
        return {"payload": {"result": {}}}

    async def ws_connect(self):
        """Pretend to open a websocket connection and return self."""
        return self

    async def authenticateWithPassword(self, username=None, password=None):
        """Pretend to authenticate and return a user dict."""
        return {"user": {"hasMultipleEdges": False}}

    async def getEdges(self, page=0, limit=20, searchParams=None):
        """Return a fake empty edges response."""
        return {"edges": []}

    async def close(self):
        """Close the dummy server (mark not connected)."""
        self.connected = False


class DummyServer:
    """Minimal dummy replacement for jsonrpc_websocket.Server used in tests."""

    def __init__(self, url, session=None, heartbeat=5) -> None:
        """Initialize the dummy Server object.

        Parameters
        ----------
        url : str
            The server URL (unused).
        session : object | None
            Optional session placeholder (unused).
        heartbeat : int
            Heartbeat interval in seconds (unused).
        """
        self.connected = True
        self.edgeRpc = None

    async def ws_connect(self):
        """Simulate connecting the websocket and return self."""
        return self

    async def authenticateWithPassword(self, username=None, password=None):
        """Simulate authentication and return a user dict.

        Parameters
        ----------
        username : str | None
            Username for authentication (unused).
        password : str | None
            Password for authentication (unused).
        """
        return {"user": {"hasMultipleEdges": False}}

    async def subscribeEdges(self, edges=None):
        """Simulate subscribing to edges (no-op)."""
        # no-op

    async def close(self):
        """Close the dummy server (mark not connected)."""
        self.connected = False


@pytest.mark.asyncio
async def test_wrap_jsonrpc_and_edgeRpc_dispatch() -> None:
    """Test wrap_jsonrpc and dispatch of edgeRpc to an edge method."""
    env = openems.OpenEMSBackend.wrap_jsonrpc("testMethod", a=1)
    assert env["method"] == "testMethod"
    assert "id" in env
    # monkeypatch the Server used inside OpenEMSBackend to avoid real aiohttp usage
    openems.jsonrpc_websocket.Server = DummyServer
    backend = openems.OpenEMSBackend(ws_url=openems.URL("ws://example"), username="u", password="p")
    # backend.rpc_server is DummyServer instance created in constructor
    edge = backend.set_component_config("edge-1", {"_host": {}})
    # ensure registered channels dict contains key used in payload to avoid KeyError
    edge._registered_channels["a/b"] = set()

    # prepare a payload that calls currentData
    payload = {"method": "currentData", "params": {"a/b": 1}}
    # call edgeRpc via backend - should dispatch to edge.currentData
    backend.edgeRpc(edgeId=edge.id, payload=payload)
    # ensure edge stored current_channel_data
    assert edge.current_channel_data == {"a/b": 1}


def make_channel_json(id_, type_, unit="unit"):
    """Create a simple channel JSON dict for tests.

    Parameters
    ----------
    id_ : str
        Channel id
    type_ : str
        Channel type
    unit : str
        Unit string
    """
    return {"id": id_, "type": type_, "unit": unit}


@pytest.mark.asyncio
async def test_component_and_channels_init_and_properties(tmp_path: Path) -> None:
    """Test creating components and channels and basic property handling."""
    # minimal component config with various channel types
    comp_json = {
        "_PropertyAlias": "alias",
        "properties": {},
        "channels": [
            make_channel_json("_PropertyEnabledCharging", "BOOLEAN"),
            make_channel_json("_PropertyChargeMode", "STRING"),
            make_channel_json("SomeSensor", "INTEGER"),
        ],
    }

    backend = SimpleNamespace()
    backend.rpc_server = DummyRpcServer()
    backend.username = "u"
    backend.password = "p"
    backend.rest_base_url = None
    backend.multi_edge = False

    edge = openems.OpenEMSEdge(backend=backend, id="edge-1")
    # set component config so prepare_entities can use it
    edge.set_component_config({"comp1": comp_json, "_host": {"Hostname": "h"}})
    # read components should use backend.rpc_server; monkeypatch getChannelsOfComponent via wrap
    # instead of calling read_components (which calls RPCs), directly prepare entities
    await edge.prepare_entities()

    # components created
    assert "comp1" in edge.components
    comp = edge.components["comp1"]
    # boolean property created
    assert any(p.name == "_PropertyEnabledCharging" for p in comp.boolean_properties)

    # register a channel callback and trigger currentData update
    ch = comp.boolean_properties[0]
    called = False

    def cb():
        nonlocal called
        called = True

    ch.register_callback(cb)
    # simulate receiving data
    edge.currentData({"comp1/_PropertyEnabledCharging": 1})
    # boolean handle_data_update should convert int to bool True
    assert ch.current_value is True
    assert called

    # test unique id
    assert "/_PropertyEnabledCharging" in ch.unique_id()


@pytest.mark.asyncio
async def test_time_and_enum_and_number_property_behavior() -> None:
    """Test enum, time and number property handling and calculations."""
    # create component and number property with multiplier and limit definitions
    comp = SimpleNamespace()
    comp.name = "ctrlEvcs1"
    comp.properties = {}
    comp.edge = SimpleNamespace()
    comp.edge.hostname = "host"
    comp.edge.id = "edgeid"

    # enum property
    enum_json = {"id": "_PropertyChargeMode", "type": "STRING", "unit": "u", "options": {"EXCESS_POWER": 1}}
    enum_prop = openems.OpenEMSEnumProperty(component=comp, channel_json=enum_json)
    enum_prop.handle_data_update("_PropertyChargeMode", "EXCESS_POWER")
    assert enum_prop.current_option == "EXCESS_POWER"

    # time property
    time_json = {"id": "_PropertyManualTargetTime", "type": "STRING", "unit": "u"}
    time_prop = openems.OpenEMSTimeProperty(component=comp, channel_json=time_json)
    time_prop.handle_data_update("_PropertyManualTargetTime", "12:34")
    assert time_prop.native_value == time(12, 34)
    # invalid time
    time_prop.handle_data_update("_PropertyManualTargetTime", "nope")
    assert time_prop.native_value is None

    # number property with templates using no external refs
    num_json = {"id": "_PropertyEnergySessionLimit", "type": "INTEGER", "unit": "W"}
    num_prop = openems.OpenEMSNumberProperty(component=comp, channel_json=num_json)
    # set multiplier and limits as static templates
    num_prop.set_multiplier_def("2")
    num_prop.set_limit_def({"lower": "1", "upper": "100"})
    # after set, multiplier/limits should be set
    assert num_prop.multiplier == 2.0
    assert num_prop.lower_limit >= 0
    assert num_prop.upper_limit >= num_prop.lower_limit

    # handle_data_update numeric value
    num_prop.handle_data_update("comp/Some", 10)
    assert num_prop.current_value == 20
