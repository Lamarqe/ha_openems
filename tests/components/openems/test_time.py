"""Tests for the OpenEMS time platform."""

from datetime import time
from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.time import (
    ATTR_TIME,
    DOMAIN as TIME_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the time platform only."""
    return [Platform.TIME]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the time entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_set_value(hass: HomeAssistant, mock_openems_client: MagicMock) -> None:
    """Setting the time sends it as HH:MM."""
    await hass.services.async_call(
        TIME_DOMAIN,
        SERVICE_SET_VALUE,
        {
            ATTR_ENTITY_ID: "time.fems12345_ctrlgridoptimizedcharge0_manual_target_time",
            ATTR_TIME: time(7, 15),
        },
        blocking=True,
    )

    assert mock_openems_client.backend.connection.rpc_server.component_updates() == [
        ("ctrlGridOptimizedCharge0", {"manualTargetTime": "07:15"})
    ]
