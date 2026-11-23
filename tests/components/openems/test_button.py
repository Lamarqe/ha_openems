"""Tests for the OpenEMS button platform."""

from unittest.mock import MagicMock

from openems_client.exceptions import OpenEMSError
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.openems.const import DOMAIN
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform

RESTART = "button.fems12345_restart"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the button platform only."""
    return [Platform.BUTTON]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the button entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_restart(hass: HomeAssistant, mock_openems_client: MagicMock) -> None:
    """Pressing restart requests a hard restart of the edge."""
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: RESTART}, blocking=True
    )

    assert mock_openems_client.backend.connection.rpc_server.request_methods()[-1] == (
        "executeSystemRestart"
    )


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_restart_error(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """A failed restart request raises a translated error."""
    mock_openems_client.backend.connection.rpc_server.results[
        "executeSystemRestart"
    ] = OpenEMSError("rejected")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: RESTART}, blocking=True
        )

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "restart_failed"
    assert exc_info.value.translation_placeholders == {"edge": "fems12345"}
