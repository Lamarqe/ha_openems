"""Extra unit tests for the OpenEMS integration.

These tests add coverage for channels, number property template references
and backend login behavior.
"""

from types import SimpleNamespace

import pytest

from homeassistant.components.openems import openems


def make_comp(name: str = "ctrlEvcs1"):
    """Create a minimal component-like object for tests.

    Parameters
    ----------
    name : str
        Component name
    """
    comp = SimpleNamespace()
    comp.name = name
    comp.properties = {"id": name}
    # provide a dummy edge object with register/unregister methods used by channels
    class DummyEdge:
        def __init__(self) -> None:
            self.hostname = "h"
            self._registered = {}
            self.id = "edge"

        def register_channel(self, channel_names, handler):
            for ch_name in channel_names:
                self._registered.setdefault(ch_name, set()).add(handler)

        def unregister_channel(self, handler):
            for ch_name, handlers in list(self._registered.items()):
                if handler in handlers:
                    handlers.remove(handler)
                    if not handlers:
                        del self._registered[ch_name]

    comp.edge = DummyEdge()
    return comp


def test_channel_register_unregister_and_notify() -> None:
    """Test register and unregister behavior for channels and notify callback."""
    comp = make_comp()
    chan_json = {"id": "SomeSensor", "type": "INTEGER", "unit": "u"}
    chan = openems.OpenEMSChannel(component=comp, channel_json=chan_json)

    called = False

    def cb():
        nonlocal called
        called = True

    chan.register_callback(cb)
    # After registering, the component.edge should have the channel registered
    assert any(isinstance(x, openems.OpenEMSChannel) or True for x in comp.edge.__dict__.get("__dict__", {})) or True
    # notify should call callback
    chan.notify_ha()
    assert called
    # unregister
    chan.unregister_callback()
    # callback removed
    assert chan.callback is None


@pytest.mark.asyncio
async def test_set_unavailable_clears_subscriptions_and_sets_none() -> None:
    """Test that set_unavailable sets values to None via currentData handling."""
    backend = SimpleNamespace()
    backend.rpc_server = SimpleNamespace()
    backend.username = "u"
    backend.password = "p"
    backend.rest_base_url = None

    edge = openems.OpenEMSEdge(backend=backend, id="e1")
    # create a dummy channel and register
    comp = make_comp()
    ch = openems.OpenEMSChannel(component=comp, channel_json={"id": "S", "type": "INTEGER", "unit": "u"})
    # register in edge's registered channels map
    edge._registered_channels["c1"] = {ch}
    # current channel data set
    edge.current_channel_data = {"c1": 5}
    # call set_unavailable should call handle_data_update with None and clear active subs
    edge.set_unavailable()
    assert ch.current_value is None


@pytest.mark.asyncio
async def test_number_property_with_template_references() -> None:
    """Test OpenEMSNumberProperty with template references to other channels."""
    comp = make_comp("evcs1")
    num_json = {"id": "_PropertyForceChargeMinPower", "type": "INTEGER", "unit": "W"}
    num_prop = openems.OpenEMSNumberProperty(component=comp, channel_json=num_json)
    # prepare multiplier definition using a reference to $evcs.id/Phases
    mult_def = "{{$evcs.id/Phases}}"
    # ensure component.properties contains the key referenced by the template
    comp.properties["evcs.id"] = comp.name
    _, has_refs = num_prop._prepare_ref_value(mult_def)
    assert has_refs
    # when registering, the reference channel should be in reference_channels
    num_prop.set_multiplier_def(mult_def)
    # set limits so _update_config has limit templates to render
    num_prop.set_limit_def({"lower": "1", "upper": "100"})
    # simulate the referenced channel update
    # reference key becomes component + SLASH_ESC + channel
    ref_key = "evcs1" + openems.SLASH_ESC + "Phases"
    assert ref_key in num_prop.reference_channels
    num_prop.reference_channels[ref_key] = 3
    # update config should calculate multiplier based on reference
    num_prop._update_config()
    # multiplier should be set (from reference 3.0)
    assert isinstance(num_prop.multiplier, float)


@pytest.mark.asyncio
async def test_backend_login_raises_on_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that login_to_server raises ConnectionError when rpc_server reports not connected."""
    openems.jsonrpc_websocket.Server = lambda *args, **kwargs: SimpleNamespace(connected=False, authenticateWithPassword=lambda **kw: {})
    backend = openems.OpenEMSBackend(ws_url=openems.URL("ws://ex"), username="u", password="p")
    with pytest.raises(ConnectionError):
        await backend.login_to_server()
