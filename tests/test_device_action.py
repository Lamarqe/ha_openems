"""Tests for the OpenEMS device actions."""

from collections.abc import Generator

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import selector
import pytest

from custom_components.openems import device_action
from custom_components.openems.const import DOMAIN
from custom_components.openems.openems import OpenEMSBooleanProperty
from custom_components.openems.services import async_setup_services

from .helpers_component import LoadedComponent, setup_evcs_component


@pytest.fixture(name="loaded")
def loaded_fixture(hass: HomeAssistant) -> Generator[LoadedComponent]:
    """Provide a loaded ctrlEvcs0 component and the registered services."""
    loaded_component = setup_evcs_component(hass)
    async_setup_services(hass)
    yield loaded_component
    loaded_component.stop()


def _action_config(device_id: str, **fields) -> dict:
    return device_action.ACTION_SCHEMA(
        {
            "domain": DOMAIN,
            "device_id": device_id,
            "type": device_action.ACTION_TYPE_UPDATE_COMPONENT_CONFIG,
            **fields,
        }
    )


async def test_get_actions_for_component_device(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """A component device provides the update_component_config action."""
    actions = await device_action.async_get_actions(hass, loaded.component_device.id)

    assert actions == [
        {
            "domain": DOMAIN,
            "device_id": loaded.component_device.id,
            "type": "update_component_config",
        }
    ]


async def test_get_actions_for_edge_device(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """The main device of an edge provides no actions."""
    assert await device_action.async_get_actions(hass, loaded.edge_device.id) == []


async def test_capabilities_contain_one_field_per_property(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """Each property becomes an optional field with a matching selector."""
    capabilities = await device_action.async_get_action_capabilities(
        hass, _action_config(loaded.component_device.id)
    )

    fields = {
        str(key): value for key, value in capabilities["extra_fields"].schema.items()
    }
    assert set(fields) == {"chargeMode", "enabledCharging", "energySessionLimit"}
    assert isinstance(fields["enabledCharging"], selector.BooleanSelector)
    assert isinstance(fields["chargeMode"], selector.SelectSelector)
    assert fields["chargeMode"].config["options"] == ["force_charge", "excess_power"]
    assert (
        fields["chargeMode"].config["translation_key"]
        == "ctrlevcs_s_l_a_s_h_chargemode"
    )
    assert isinstance(fields["energySessionLimit"], selector.NumberSelector)
    assert fields["energySessionLimit"].config["unit_of_measurement"] == "Wh"


async def test_call_action_sends_single_request(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """The filled in fields are sent in one request via the service."""
    config = _action_config(
        loaded.component_device.id,
        enabledCharging=True,
        chargeMode="force_charge",
        alias="Force charging",
    )

    await device_action.async_call_action_from_config(hass, config, {}, None)

    loaded.update_config.assert_awaited_once_with(
        [("enabledCharging", True), ("chargeMode", "FORCE_CHARGE")]
    )


async def test_call_action_with_stale_field_raises(
    hass: HomeAssistant, loaded: LoadedComponent
) -> None:
    """A field of a property which does not exist anymore is rejected."""
    config = _action_config(loaded.component_device.id, removedProperty=1)

    with pytest.raises(ServiceValidationError):
        await device_action.async_call_action_from_config(hass, config, {}, None)

    loaded.update_config.assert_not_awaited()


def test_field_name_avoids_reserved_keys() -> None:
    """A property named like a script action key falls back to its channel name."""
    prop = OpenEMSBooleanProperty(
        component=None,
        channel_json={"id": "_PropertyEnabled", "type": "BOOLEAN", "unit": ""},
    )

    assert device_action.field_name(prop) == "_PropertyEnabled"
