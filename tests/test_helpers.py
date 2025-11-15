"""Tests for small helpers used by the OpenEMS integration."""

import asyncio

from homeassistant.components.openems import helpers, openems


def test_unit_description_various() -> None:
    """Unit description returns expected units and device classes."""
    # energy
    ud = helpers.unit_description("kWh")
    assert ud.device_class is not None
    # special Wh_Σ
    ud = helpers.unit_description("Wh_Σ")
    assert ud.unit == "Wh"
    # temperature
    ud = helpers.unit_description("C")
    assert ud.unit == "°C"


def test_translation_key_and_find_channel(dummy_backend, dummy_edge) -> None:
    """Create a component/channel and ensure helper finds it and returns translation key."""
    # create component with channel and attach to backend.edge
    component_json = {
        "_PropertyAlias": "a",
        "properties": {},
        "channels": [{"id": "Some", "type": "INTEGER", "unit": "u"}],
    }
    edge = openems.OpenEMSEdge(dummy_backend, "e1")
    edge.set_component_config({"_host": {"Hostname": "h1"}, "comp1": component_json})
    # prepare entities

    async def _prep() -> None:
        await edge.prepare_entities()

    asyncio.get_event_loop().run_until_complete(_prep())

    backend = dummy_backend
    backend.the_edge = edge
    # find the channel unique id
    channel = next(iter(edge.components["comp1"].sensors))
    uid = channel.unique_id()
    found = helpers.find_channel_in_backend(backend, uid)
    assert found is channel
    # translation key
    tk = helpers.translation_key(channel)
    assert "/" in tk
