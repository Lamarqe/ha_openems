"""Tests for small helpers used by the OpenEMS integration."""

import json
from pathlib import Path
from datetime import time
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant.common import MockConfigEntry

from openems_client import openems
from openems_client.exceptions import OpenEMSError
from custom_components.openems.const import DEFAULT_CHANNELS, DOMAIN
from custom_components.openems.number import (
    OpenEMSNumberDescription,
    OpenEMSNumberEntity,
)
from custom_components.openems.select import (
    OpenEMSSelectDescription,
    OpenEMSSelectEntity,
)
from custom_components.openems.switch import (
    OpenEMSSwitchDescription,
    OpenEMSSwitchEntity,
)
from custom_components.openems.time import OpenEMSTimeDescription, OpenEMSTimeEntity
from custom_components.openems.helpers_ha import (
    CAMEL_CASE_PATTERN,
    component_from_device,
    ha_options,
    to_snake_case,
    translation_key,
    unit_description,
)


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
        "channels": [{"id": "StateMachine", "type": "INTEGER", "unit": "u"}],
    }
    component_config = {"_host": {"Hostname": "h1"}, "comp1": component_json}
    edge = openems.OpenEMSEdge(dummy_backend, "e1", component_config)
    channel = next(iter(edge.components["comp1"].sensors))
    assert translation_key(channel) == "state_machine"


def test_default_channels_have_translated_names() -> None:
    """Every channel enabled by default has a name in strings.json."""
    entity_strings = json.loads(
        (
            Path(__file__).parents[1] / "custom_components/openems/strings.json"
        ).read_text(encoding="utf-8")
    )["entity"]
    named_keys = {
        key
        for platform_strings in entity_strings.values()
        for key, value in platform_strings.items()
        if "name" in value
    }
    # string property without enum or time options, no entity is created for it
    channel_names = set().union(*DEFAULT_CHANNELS.values()) - {"_PropertyTargetTime"}
    missing = {
        name
        for name in channel_names
        if to_snake_case(CAMEL_CASE_PATTERN.sub("_", name.removeprefix("_Property")))
        not in named_keys
    }
    assert not missing


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


def test_ha_options_snake_case_deduplicated_in_order() -> None:
    """Options are converted to snake_case, keeping the first of colliding names."""
    assert ha_options(["Force Charge", "EXCESS_POWER", "FORCE_CHARGE"]) == [
        "force_charge",
        "excess_power",
    ]


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


@pytest.mark.parametrize(
    ("entity_class", "description_class", "method", "value"),
    [
        pytest.param(
            OpenEMSNumberEntity,
            OpenEMSNumberDescription,
            "async_set_native_value",
            5.0,
            id="number",
        ),
        pytest.param(
            OpenEMSSelectEntity,
            OpenEMSSelectDescription,
            "async_select_option",
            "manual",
            id="select",
        ),
        pytest.param(
            OpenEMSSwitchEntity,
            OpenEMSSwitchDescription,
            "async_turn_on",
            None,
            id="switch",
        ),
        pytest.param(
            OpenEMSTimeEntity,
            OpenEMSTimeDescription,
            "async_set_value",
            time(8, 30),
            id="time",
        ),
    ],
)
async def test_write_errors_raise_translated_error(
    entity_class: type,
    description_class: type,
    method: str,
    value: object,
) -> None:
    """Errors while writing a value raise a translated HomeAssistantError."""
    channel = MagicMock()
    channel.property_options = ["MANUAL"]
    channel.update_value = AsyncMock(side_effect=OpenEMSError("rejected"))
    entity = entity_class(
        channel,
        description_class(key="h1/edge0/comp1/_PropertyMode"),
        dr.DeviceInfo(identifiers={(DOMAIN, "h1 comp1")}),
    )
    entity.entity_id = "platform.comp1_mode"
    args = () if value is None else (value,)

    with pytest.raises(HomeAssistantError) as exc_info:
        await getattr(entity, method)(*args)

    assert exc_info.value.translation_key == "write_failed"
    assert exc_info.value.translation_placeholders == {
        "entity_id": "platform.comp1_mode"
    }
