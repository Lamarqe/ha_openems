"""Tests for small helpers used by the OpenEMS integration."""

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openems import openems
from custom_components.openems.const import DOMAIN, SLASH_ESC
from custom_components.openems.helpers_ha import (component_from_device,
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


def test_translation_key(dummy_backend) -> None:
    """Create a component/channel and ensure the helper returns its translation key."""
    component_json = {
        "_PropertyAlias": "a",
        "properties": {},
        "channels": [{"id": "Some", "type": "INTEGER", "unit": "u"}],
    }
    component_config = {"_host": {"Hostname": "h1"}, "comp1": component_json}
    edge = openems.OpenEMSEdge(dummy_backend, "e1", component_config)
    try:
        channel = next(iter(edge.components["comp1"].sensors))
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


async def test_component_from_device_multi_edge(hass: HomeAssistant) -> None:
    """Hostnames containing the edge id (multi edge setups) are resolved correctly."""
    component = MagicMock()
    backend = MagicMock()
    backend.the_edge.hostname = "h1 edge0"
    backend.the_edge.components = {"ctrlEvcs0": component}
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = MagicMock(backend=backend)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "h1 edge0 ctrlEvcs0")}
    )
    edge_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "h1 edge0")}
    )
    try:
        assert component_from_device(hass, device.id) is component
        with pytest.raises(ServiceValidationError):
            component_from_device(hass, edge_device.id)
        with pytest.raises(ServiceValidationError):
            component_from_device(hass, "unknown")
    finally:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
