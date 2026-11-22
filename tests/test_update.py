"""Tests for the OpenEMS update platform."""

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

from openems_client.exceptions import (
    OpenEMSConnectionError,
    OpenEMSError,
    OpenEMSTimeoutError,
)
from openems_client.system_update import SystemUpdateState, UpdateStatus
import pytest

from custom_components.openems import update as update_module
from custom_components.openems.update import (
    OpenEMSUpdateDescription,
    OpenEMSUpdateEntity,
)
from homeassistant.components.update import UpdateDeviceClass, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

UP_TO_DATE = SystemUpdateState(
    status=UpdateStatus.UP_TO_DATE, installed_version="2.0.0", latest_version="2.0.0"
)
AVAILABLE = SystemUpdateState(
    status=UpdateStatus.AVAILABLE, installed_version="1.0.0", latest_version="2.0.0"
)
RUNNING = SystemUpdateState(status=UpdateStatus.RUNNING, percent_completed=75)
UNKNOWN = SystemUpdateState(status=UpdateStatus.UNKNOWN)


def _make_entity(has_release_notes: bool = True) -> OpenEMSUpdateEntity:
    edge = MagicMock()
    edge.hostname = "host"
    edge.id = "edge-1"
    edge.has_release_notes = has_release_notes
    edge.release_url = "http://192.0.2.1/_/changelog"
    desc = OpenEMSUpdateDescription(key="host/edge-1/update")
    device_info = DeviceInfo(name="host", identifiers={("openems", "host")})
    entity = OpenEMSUpdateEntity(edge, desc, device_info)
    entity.async_write_ha_state = MagicMock()
    return entity


@pytest.mark.parametrize(
    ("state", "available", "installed", "latest", "in_progress", "percentage"),
    [
        pytest.param(UP_TO_DATE, True, "2.0.0", "2.0.0", False, None, id="up_to_date"),
        pytest.param(AVAILABLE, True, "1.0.0", "2.0.0", False, None, id="available"),
        pytest.param(RUNNING, True, None, None, True, 75, id="running"),
        pytest.param(UNKNOWN, False, None, None, False, None, id="unknown"),
    ],
)
async def test_async_update(
    state: SystemUpdateState,
    available: bool,
    installed: str | None,
    latest: str | None,
    in_progress: bool,
    percentage: int | None,
) -> None:
    """The update state of the edge is shown by the entity."""
    entity = _make_entity()
    entity._edge.get_system_update_state = AsyncMock(return_value=state)

    await entity.async_update()

    assert entity.available is available
    assert entity.installed_version == installed
    assert entity.latest_version == latest
    assert bool(entity.in_progress) is in_progress
    assert entity.update_percentage == percentage


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(OpenEMSConnectionError("connection lost"), id="connection_lost"),
        pytest.param(OpenEMSError("rejected"), id="rejected"),
    ],
)
async def test_async_update_error_marks_unavailable(error: Exception) -> None:
    """Errors while polling make the entity unavailable instead of raising."""
    entity = _make_entity()
    entity._edge.get_system_update_state = AsyncMock(side_effect=error)

    await entity.async_update()

    assert entity.available is False


async def test_unknown_state_keeps_update_in_progress() -> None:
    """A running update stays in progress while the restarting edge does not respond."""
    entity = _make_entity()
    entity._edge.get_system_update_state = AsyncMock(side_effect=[RUNNING, UNKNOWN])

    await entity.async_update()
    await entity.async_update()

    assert entity.available is False
    assert entity.in_progress


async def test_async_setup_entry_creates_update_entity(hass: HomeAssistant) -> None:
    """One update entity is created for the edge."""
    mock_entry = MagicMock()
    mock_entry.runtime_data.backend.the_edge.hostname = "host"
    mock_entry.runtime_data.backend.the_edge.id = "edge-1"
    mock_entry.runtime_data.edge_device.info = DeviceInfo(
        name="host", identifiers={("openems", "host")}
    )
    created: list[OpenEMSUpdateEntity] = []

    def _add_entities(entities: list[OpenEMSUpdateEntity], **kwargs: bool) -> None:
        created.extend(entities)

    await update_module.async_setup_entry(hass, mock_entry, _add_entities)

    assert len(created) == 1
    assert created[0].unique_id == "host/edge-1/update"


async def test_install_shows_progress_until_finished() -> None:
    """Every state reported during the installation is written to the entity."""
    entity = _make_entity()
    written: list[tuple[bool, bool, int | None, str | None]] = []
    entity.async_write_ha_state = MagicMock(
        side_effect=lambda: written.append(
            (
                entity.available,
                bool(entity.in_progress),
                entity.update_percentage,
                entity.installed_version,
            )
        )
    )

    async def _install(
        on_progress: Callable[[SystemUpdateState], None],
    ) -> SystemUpdateState:
        for state in (RUNNING, UNKNOWN, UP_TO_DATE):
            on_progress(state)
        return UP_TO_DATE

    entity._edge.install_system_update = _install

    await entity.async_install(None, False)

    assert written == [
        (True, True, 75, None),
        (False, True, None, None),
        (True, False, None, "2.0.0"),
    ]


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        pytest.param(OpenEMSError("rejected"), "update_failed", id="rejected"),
        pytest.param(
            OpenEMSTimeoutError("not finished"), "update_not_finished", id="timeout"
        ),
    ],
)
async def test_install_errors_raise_translated_error(
    error: Exception, translation_key: str
) -> None:
    """Rejected and unfinished updates raise a translated error."""
    entity = _make_entity()
    entity._edge.install_system_update = AsyncMock(side_effect=error)

    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.async_install(None, False)

    assert exc_info.value.translation_key == translation_key
    assert exc_info.value.translation_placeholders == {"edge": "host"}


@pytest.mark.parametrize(
    ("has_release_notes", "features"),
    [
        pytest.param(
            True,
            UpdateEntityFeature.INSTALL
            | UpdateEntityFeature.PROGRESS
            | UpdateEntityFeature.RELEASE_NOTES,
            id="fems",
        ),
        pytest.param(
            False,
            UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS,
            id="openems",
        ),
    ],
)
def test_release_information(
    has_release_notes: bool, features: UpdateEntityFeature
) -> None:
    """The entity is a firmware update with the release information of the edge."""
    entity = _make_entity(has_release_notes)

    assert entity.device_class == UpdateDeviceClass.FIRMWARE
    assert entity.release_url == "http://192.0.2.1/_/changelog"
    assert entity.supported_features == features


async def test_release_notes(hass: HomeAssistant) -> None:
    """The release notes of the edge are requested in the language of Home Assistant."""
    entity = _make_entity()
    entity.hass = hass
    hass.config.language = "de"
    entity._edge.get_release_notes = AsyncMock(return_value="## 2.0.0\n\n- Neu")

    assert await entity.async_release_notes() == "## 2.0.0\n\n- Neu"
    entity._edge.get_release_notes.assert_awaited_once_with("de")
