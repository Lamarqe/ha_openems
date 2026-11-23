"""Tests for the OpenEMS select platform."""

from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the select platform only."""
    return [Platform.SELECT]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the select entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_select_option(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """Selecting an option sends the option of the edge."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {
            ATTR_ENTITY_ID: "select.fems12345_ctrlgridoptimizedcharge0_mode",
            ATTR_OPTION: "automatic",
        },
        blocking=True,
    )

    assert mock_openems_client.backend.connection.rpc_server.component_updates() == [
        ("ctrlGridOptimizedCharge0", {"mode": "AUTOMATIC"})
    ]
