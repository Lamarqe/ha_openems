"""Tests for the OpenEMS sensor platform."""

from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.openems.const import CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING
from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform

GRID_BUY_ENERGY = "sensor.fems12345_sum_grid_buy_active_energy"
GRID_POWER = "sensor.fems12345_sum_grid_active_power"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the sensor platform only."""
    return [Platform.SENSOR]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the sensor entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_state_follows_channel_data(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """Channel data sent by the edge updates the state."""
    connection = mock_openems_client.backend.connection

    connection.rpc_server.push_current_data({"_sum/GridActivePower": 1500})
    await hass.async_block_till_done()
    assert hass.states.get(GRID_POWER).state == "1500"

    # the library reports the connection loss, the entities become unavailable
    connection.enable_reconnect.call_args.args[0]()
    await hass.async_block_till_done()
    assert hass.states.get(GRID_POWER).state == STATE_UNAVAILABLE

    connection.rpc_server.push_current_data({"_sum/GridActivePower": 1600})
    await hass.async_block_till_done()
    assert hass.states.get(GRID_POWER).state == "1600"


@pytest.mark.parametrize(
    ("mock_config_entry", "expected"),
    [
        pytest.param(
            {CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: True}, "7006850", id="ignored"
        ),
        pytest.param(
            {CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False},
            "7000000",
            id="accepted",
        ),
    ],
    indirect=["mock_config_entry"],
)
@pytest.mark.usefixtures("init_integration")
async def test_decreasing_total_increasing_value(
    hass: HomeAssistant, mock_openems_client: MagicMock, expected: str
) -> None:
    """A decreasing total increasing value is dropped if the option is set."""
    mock_openems_client.backend.connection.rpc_server.push_current_data(
        {"_sum/GridBuyActiveEnergy": 7000000}
    )
    await hass.async_block_till_done()

    assert hass.states.get(GRID_BUY_ENERGY).state == expected
