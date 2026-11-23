"""Tests for the OpenEMS update platform."""

from typing import Any
from unittest.mock import MagicMock

from openems_client.exceptions import OpenEMSError, OpenEMSTimeoutError
from openems_client.fems import FEMS_CHANGELOG_URL
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.openems.const import DOMAIN
from homeassistant.components.homeassistant import (
    DOMAIN as HOMEASSISTANT_DOMAIN,
    SERVICE_UPDATE_ENTITY,
)
from homeassistant.components.update import (
    ATTR_IN_PROGRESS,
    ATTR_INSTALLED_VERSION,
    ATTR_LATEST_VERSION,
    ATTR_UPDATE_PERCENTAGE,
    DOMAIN as UPDATE_DOMAIN,
    SERVICE_INSTALL,
    UpdateEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant.typing import WebSocketGenerator

from .conftest import INSTALLED_VERSION

ENTITY_ID = "update.fems12345_firmware"
LATEST_VERSION = "2026.12.0"
UPDATE_AVAILABLE = {
    "available": {"currentVersion": INSTALLED_VERSION, "latestVersion": LATEST_VERSION}
}


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the update platform only."""
    return [Platform.UPDATE]


async def _refresh(hass: HomeAssistant, rpc_server: Any, result: Any) -> None:
    """Let the edge report the given update state and refresh the entity."""
    rpc_server.results["getSystemUpdateState"] = result
    await async_setup_component(hass, HOMEASSISTANT_DOMAIN, {})
    await hass.services.async_call(
        HOMEASSISTANT_DOMAIN,
        SERVICE_UPDATE_ENTITY,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )


@pytest.mark.usefixtures("init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the update entity of an up to date edge."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    ("result", "state", "attributes"),
    [
        pytest.param(
            UPDATE_AVAILABLE,
            STATE_ON,
            {
                ATTR_INSTALLED_VERSION: INSTALLED_VERSION,
                ATTR_LATEST_VERSION: LATEST_VERSION,
                ATTR_IN_PROGRESS: False,
            },
            id="available",
        ),
        pytest.param(
            {"running": {"percentCompleted": 75}},
            STATE_OFF,
            {ATTR_IN_PROGRESS: True, ATTR_UPDATE_PERCENTAGE: 75},
            id="running",
        ),
        pytest.param(OpenEMSError("no answer"), STATE_UNAVAILABLE, {}, id="error"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_update_state(
    hass: HomeAssistant,
    mock_openems_client: MagicMock,
    result: Any,
    state: str,
    attributes: dict[str, Any],
) -> None:
    """The entity shows the update state reported by the edge."""
    await _refresh(hass, mock_openems_client.backend.connection.rpc_server, result)

    entity_state = hass.states.get(ENTITY_ID)
    assert entity_state.state == state
    assert attributes.items() <= entity_state.attributes.items()


@pytest.mark.usefixtures("init_integration")
async def test_follows_the_connection(
    hass: HomeAssistant, mock_openems_client: MagicMock, channel_data: dict[str, Any]
) -> None:
    """The entity is unavailable while disconnected and refreshed after reconnecting."""
    connection = mock_openems_client.backend.connection

    connection.enable_reconnect.call_args.args[0]()
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    connection.rpc_server.results["getSystemUpdateState"] = UPDATE_AVAILABLE
    connection.rpc_server.push_current_data(channel_data)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_ON


@pytest.mark.usefixtures("init_integration")
async def test_install(hass: HomeAssistant, mock_openems_client: MagicMock) -> None:
    """Installing the update waits until the edge reports to be up to date."""
    rpc_server = mock_openems_client.backend.connection.rpc_server
    await _refresh(hass, rpc_server, UPDATE_AVAILABLE)
    rpc_server.results["getSystemUpdateState"] = {
        "updated": {"version": LATEST_VERSION}
    }

    await hass.services.async_call(
        UPDATE_DOMAIN, SERVICE_INSTALL, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    assert "executeSystemUpdate" in rpc_server.request_methods()
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_INSTALLED_VERSION] == LATEST_VERSION


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        pytest.param(
            OpenEMSTimeoutError("too long"), "update_not_finished", id="timeout"
        ),
        pytest.param(OpenEMSError("rejected"), "update_failed", id="rejected"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_install_error(
    hass: HomeAssistant,
    mock_openems_client: MagicMock,
    error: Exception,
    translation_key: str,
) -> None:
    """A failed installation raises a translated error."""
    rpc_server = mock_openems_client.backend.connection.rpc_server
    await _refresh(hass, rpc_server, UPDATE_AVAILABLE)
    rpc_server.results["executeSystemUpdate"] = error

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            UPDATE_DOMAIN, SERVICE_INSTALL, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
        )

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == translation_key
    assert exc_info.value.translation_placeholders == {"edge": "fems12345"}


async def test_release_notes(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """The release notes list the changes up to the latest version."""
    aioclient_mock.get(
        FEMS_CHANGELOG_URL,
        json=[
            {"version": LATEST_VERSION, "entries": {"en": ["New feature"]}},
            {"version": INSTALLED_VERSION, "entries": {"en": ["Installed"]}},
        ],
    )
    await _refresh(
        hass, mock_openems_client.backend.connection.rpc_server, UPDATE_AVAILABLE
    )
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "update/release_notes", "entity_id": ENTITY_ID}
    )

    result = await client.receive_json()
    assert result["result"] == f"## {LATEST_VERSION}\n\n- New feature"


@pytest.mark.parametrize(
    "edge_config",
    [
        pytest.param(
            {"_host": {"Hostname": "openems", "properties": {}, "channels": []}},
            id="openems",
        )
    ],
    indirect=True,
)
@pytest.mark.usefixtures("init_integration")
async def test_no_release_notes_without_fems(hass: HomeAssistant) -> None:
    """Only FEMS publish release notes."""
    assert hass.states.get("update.openems_firmware").attributes[
        ATTR_SUPPORTED_FEATURES
    ] == (UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS)
