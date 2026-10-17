"""Tests for the openems.update_component_config service."""

from collections.abc import Generator

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
import voluptuous as vol

from custom_components.openems.const import DOMAIN
from custom_components.openems.services import (
    SERVICE_UPDATE_COMPONENT_CONFIG,
    async_setup_services,
)

from .helpers_component import LoadedComponent, setup_evcs_component


@pytest.fixture(name="loaded")
def loaded_fixture(hass: HomeAssistant) -> Generator[LoadedComponent]:
    """Provide a loaded ctrlEvcs0 component and the registered services."""
    loaded_component = setup_evcs_component(hass)
    async_setup_services(hass)
    yield loaded_component
    loaded_component.stop()


async def _call(hass: HomeAssistant, data: dict) -> None:
    await hass.services.async_call(
        DOMAIN, SERVICE_UPDATE_COMPONENT_CONFIG, data, blocking=True
    )


async def test_device_target_sends_single_request(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """All properties of one device are sent in one updateComponentConfig request."""
    await _call(
        hass,
        {
            "device_id": loaded.component_device.id,
            "properties": {"enabledCharging": True, "chargeMode": "force_charge"},
        },
    )

    loaded.update_config.assert_awaited_once_with(
        [("enabledCharging", True), ("chargeMode", "FORCE_CHARGE")]
    )


async def test_device_target_adds_update_group_members(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """Update group members not given in the call are sent with their current value."""
    enabled_charging = loaded.component.get_property("enabledCharging")
    assert enabled_charging is not None
    enabled_charging.handle_data_update("_PropertyEnabledCharging", 1)

    await _call(
        hass,
        {
            "device_id": loaded.component_device.id,
            "properties": {"_PropertyChargeMode": "EXCESS_POWER"},
        },
    )

    loaded.update_config.assert_awaited_once_with(
        [("chargeMode", "EXCESS_POWER"), ("enabledCharging", True)]
    )


async def test_unknown_property_raises_and_sends_nothing(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """An unknown property name is rejected before anything is sent."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _call(
            hass,
            {
                "device_id": loaded.component_device.id,
                "properties": {"chargeMode": "force_charge", "unknown": 1},
            },
        )

    assert exc_info.value.translation_key == "unknown_property"
    assert "chargeMode" in exc_info.value.translation_placeholders["valid_properties"]
    loaded.update_config.assert_not_awaited()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("chargeMode", "invalid"),
        ("enabledCharging", "maybe"),
        ("energySessionLimit", "abc"),
    ],
)
async def test_invalid_value_raises(
    hass: HomeAssistant, loaded: LoadedComponent, name: str, value: str
) -> None:
    """Values which cannot be converted are rejected."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _call(
            hass,
            {"device_id": loaded.component_device.id, "properties": {name: value}},
        )

    assert exc_info.value.translation_key == "invalid_property_value"
    loaded.update_config.assert_not_awaited()


async def test_edge_device_is_rejected(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """The main device of an edge does not represent a component."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _call(
            hass,
            {
                "device_id": loaded.edge_device.id,
                "properties": {"enabledCharging": True},
            },
        )

    assert exc_info.value.translation_key == "no_component_device"


async def test_legacy_number_entity_with_value(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """The legacy form with a number entity target and value still works."""
    await _call(hass, {"entity_id": loaded.number_entity_id, "value": 500})

    loaded.update_config.assert_awaited_once_with([("energySessionLimit", 500.0)])


async def test_legacy_unknown_entity_is_rejected(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """Entities which are not OpenEMS number entities are rejected."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _call(hass, {"entity_id": "number.unknown", "value": 1})

    assert exc_info.value.translation_key == "no_number_entity"


@pytest.mark.parametrize(
    "data",
    [
        {"properties": {"enabledCharging": True}},
        {"device_id": "abc", "value": 1},
        {"entity_id": "number.x", "properties": {"enabledCharging": True}},
        {"entity_id": "number.x"},
    ],
)
async def test_schema_requires_matching_target_and_fields(
    hass: HomeAssistant, loaded: LoadedComponent, data: dict
) -> None:
    """Properties require a device target, value requires an entity target."""
    with pytest.raises(vol.Invalid):
        await _call(hass, data)
