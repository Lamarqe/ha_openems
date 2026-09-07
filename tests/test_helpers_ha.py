"""Tests for small helpers used by the OpenEMS integration."""

from custom_components.openems import openems
from custom_components.openems.const import SLASH_ESC
from custom_components.openems.helpers_ha import (find_channel_in_backend,
                                                  to_snake_case,
                                                  translation_key,
                                                  unit_description)


def test_unit_description_various() -> None:
    """Unit description returns expected units and device classes."""
    # energy
    ud = unit_description("kWh")
    assert ud.sensor_device_class is not None
    # special Wh_Σ
    ud = unit_description("Wh_Σ")
    assert ud.unit == "Wh"
    # temperature
    ud = unit_description("C")
    assert ud.unit == "°C"


def test_translation_key_and_find_channel(dummy_backend) -> None:
    """Create a component/channel and ensure helper finds it and returns translation key."""
    component_json = {
        "_PropertyAlias": "a",
        "properties": {},
        "channels": [{"id": "Some", "type": "INTEGER", "unit": "u"}],
    }
    component_config = {"_host": {"Hostname": "h1"}, "comp1": component_json}
    edge = openems.OpenEMSEdge(dummy_backend, "e1", component_config)
    try:
        dummy_backend.the_edge = edge

        channel = next(iter(edge.components["comp1"].sensors))
        uid = channel.unique_id()
        found = find_channel_in_backend(dummy_backend, uid)
        assert found is channel

        tk = translation_key(channel)
        assert SLASH_ESC in tk
    finally:
        edge.stop()


def test_to_snake_case_matches_translation_key_format() -> None:
    """Ensure option keys match the translation string extraction format."""
    assert (
        to_snake_case(
            "Not active. The controller has no flexibility left to delay charge, "
            "because the limitation is nearly zero."
        )
        == "not_active_the_controller_has_no_flexibility_left_to_delay_charge_"
        "because_the_limitation_is_nearly_zero"
    )
