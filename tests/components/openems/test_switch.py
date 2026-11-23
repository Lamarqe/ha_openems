"""Tests for the OpenEMS switch platform."""

from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform

RESERVE_SOC_ENABLED = (
    "switch.fems12345_ctrlemergencycapacityreserve0_reserve_soc_enabled"
)


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the switch platform only."""
    return [Platform.SWITCH]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the switch entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    ("service", "value"),
    [
        pytest.param(SERVICE_TURN_ON, True, id="turn_on"),
        pytest.param(SERVICE_TURN_OFF, False, id="turn_off"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_turn_on_off(
    hass: HomeAssistant, mock_openems_client: MagicMock, service: str, value: bool
) -> None:
    """Turning the switch on or off sends the value."""
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: RESERVE_SOC_ENABLED}, blocking=True
    )

    assert mock_openems_client.backend.connection.rpc_server.component_updates() == [
        ("ctrlEmergencyCapacityReserve0", {"isReserveSocEnabled": value})
    ]
