"""Tests for the OpenEMS binary sensor platform."""

from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the binary sensor platform only."""
    return [Platform.BINARY_SENSOR]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the binary sensor entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_state_follows_channel_data(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """Channel data sent by the edge updates the state."""
    mock_openems_client.backend.connection.rpc_server.push_current_data(
        {"ctrlEssLimiter14a0/RestrictionMode": 1}
    )
    await hass.async_block_till_done()

    state = hass.states.get(
        "binary_sensor.fems12345_ctrlesslimiter14a0_restriction_mode"
    )
    assert state.state == STATE_ON
