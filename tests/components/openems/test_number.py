"""Tests for the OpenEMS number platform."""

from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform

FORCE_CHARGE_MIN_POWER = "number.fems12345_ctrlevcs0_force_charge_min_power"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the number platform only."""
    return [Platform.NUMBER]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the number entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_set_value(hass: HomeAssistant, mock_openems_client: MagicMock) -> None:
    """Setting a value sends it unscaled, the force charge power is per phase."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: FORCE_CHARGE_MIN_POWER, ATTR_VALUE: 12000},
        blocking=True,
    )

    assert mock_openems_client.backend.connection.rpc_server.component_updates() == [
        ("ctrlEvcs0", {"forceChargeMinPower": 4000.0})
    ]


@pytest.mark.usefixtures("init_integration")
async def test_limits_follow_referenced_channels(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """The limits are calculated from the hardware limits of the charging station."""
    state = hass.states.get(FORCE_CHARGE_MIN_POWER)
    assert (state.attributes[ATTR_MIN], state.attributes[ATTR_MAX]) == (4200, 22100)

    mock_openems_client.backend.connection.rpc_server.push_current_data(
        {"evcs0/MaximumHardwarePower": 11040}
    )
    await hass.async_block_till_done()

    state = hass.states.get(FORCE_CHARGE_MIN_POWER)
    assert (state.attributes[ATTR_MIN], state.attributes[ATTR_MAX]) == (4200, 11100)
