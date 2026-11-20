"""Tests for the OpenEMS update platform."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from openems_client.exceptions import OpenEMSConnectionError, OpenEMSError
import pytest

from custom_components.openems import update as update_module
from custom_components.openems.update import (
    OpenEMSUpdateDescription,
    OpenEMSUpdateEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_edge():
    edge = MagicMock()
    edge.hostname = "host"
    edge.id = "edge-1"
    return edge


def _make_entity(edge=None) -> OpenEMSUpdateEntity:
    if edge is None:
        edge = _make_edge()
    desc = OpenEMSUpdateDescription(key="host/edge-1/update")
    device_info = DeviceInfo(name="host", identifiers={("openems", "host")})
    return OpenEMSUpdateEntity(edge, desc, device_info)


# ---------------------------------------------------------------------------
# _set_versions
# ---------------------------------------------------------------------------


def test_set_versions_up_to_date_no_update_available() -> None:
    """When only current_version is supplied the system is up to date."""
    entity = _make_entity()
    entity._set_versions("1.2.3")

    assert entity._attr_available is True
    assert entity._attr_installed_version == "1.2.3"
    assert entity._attr_latest_version == "1.2.3"
    assert not entity._attr_in_progress


def test_set_versions_update_available() -> None:
    """When both versions differ an update is available."""
    entity = _make_entity()
    entity._set_versions("1.0.0", "2.0.0")

    assert entity._attr_available is True
    assert entity._attr_installed_version == "1.0.0"
    assert entity._attr_latest_version == "2.0.0"


def test_set_versions_none_marks_unavailable() -> None:
    """Passing None marks the entity unavailable."""
    entity = _make_entity()
    entity._set_versions(None)

    assert entity._attr_available is False
    assert entity._attr_installed_version is None


# ---------------------------------------------------------------------------
# _set_progress_percentage
# ---------------------------------------------------------------------------


def test_set_progress_percentage_marks_in_progress() -> None:
    entity = _make_entity()
    entity._set_progress_percentage(42)

    assert entity._attr_available is True
    assert entity._attr_in_progress is True
    assert entity._attr_update_percentage == 42


# ---------------------------------------------------------------------------
# async_update state machine
# ---------------------------------------------------------------------------


async def test_async_update_status_updated() -> None:
    """'updated' status sets versions from the response."""
    edge = _make_edge()
    edge.get_system_update_state = AsyncMock(
        return_value={"updated": {"version": "3.0.0"}}
    )
    entity = _make_entity(edge)
    await entity.async_update()

    assert entity._attr_installed_version == "3.0.0"
    assert entity._attr_latest_version == "3.0.0"


async def test_async_update_status_available() -> None:
    """'available' status exposes both current and latest version."""
    edge = _make_edge()
    edge.get_system_update_state = AsyncMock(
        return_value={
            "available": {"currentVersion": "1.0.0", "latestVersion": "2.0.0"}
        }
    )
    entity = _make_entity(edge)
    await entity.async_update()

    assert entity._attr_installed_version == "1.0.0"
    assert entity._attr_latest_version == "2.0.0"


async def test_async_update_status_running() -> None:
    """'running' status sets the progress percentage."""
    edge = _make_edge()
    edge.get_system_update_state = AsyncMock(
        return_value={"running": {"percentCompleted": 75}}
    )
    entity = _make_entity(edge)
    await entity.async_update()

    assert entity._attr_in_progress is True
    assert entity._attr_update_percentage == 75


async def test_async_update_unknown_status_marks_unavailable() -> None:
    """An unknown status key is handled gracefully."""
    edge = _make_edge()
    edge.get_system_update_state = AsyncMock(return_value={"unknown": {}})
    entity = _make_entity(edge)
    await entity.async_update()

    assert entity._attr_available is False


async def test_async_update_connection_error_marks_unavailable() -> None:
    """A connection error during polling must not propagate."""
    edge = _make_edge()
    edge.get_system_update_state = AsyncMock(
        side_effect=OpenEMSConnectionError("connection lost")
    )
    entity = _make_entity(edge)
    await entity.async_update()

    assert entity._attr_available is False


async def test_async_update_rejected_request_marks_unavailable() -> None:
    """A request rejected during polling must not propagate."""
    edge = _make_edge()
    edge.get_system_update_state = AsyncMock(side_effect=OpenEMSError("error"))
    entity = _make_entity(edge)
    await entity.async_update()

    assert entity._attr_available is False


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_creates_update_entity(hass: HomeAssistant) -> None:
    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.id = "edge-1"

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.edge_device.info = DeviceInfo(
        name="host", identifiers={("openems", "host")}
    )

    created: list[OpenEMSUpdateEntity] = []

    def _add_entities(entities, **kwargs):
        created.extend(entities)

    await update_module.async_setup_entry(hass, mock_entry, _add_entities)

    assert len(created) == 1
    assert isinstance(created[0], OpenEMSUpdateEntity)


_AVAILABLE = {"available": {"currentVersion": "1.0.0", "latestVersion": "2.0.0"}}
_RUNNING = {"running": {"percentCompleted": 50}}
_UPDATED = {"updated": {"version": "2.0.0"}}


_real_sleep = asyncio.sleep


async def _yield_once(_delay: float) -> None:
    """Replace the waits during the installation, but let other tasks run."""
    await _real_sleep(0)


def _make_installing_entity(
    update_states: list, execute_system_update: AsyncMock
) -> OpenEMSUpdateEntity:
    edge = _make_edge()
    edge.get_system_update_state = AsyncMock(side_effect=update_states)
    edge.execute_system_update = execute_system_update
    entity = _make_entity(edge)
    entity.async_write_ha_state = MagicMock()
    return entity


async def test_install_waits_until_new_version_is_reported() -> None:
    """The installation succeeds when the edge reports the new version.

    The first poll happens before the edge started the update, and the request fails
    with a connection loss while the edge restarts. Neither ends the installation.
    """
    entity = _make_installing_entity(
        [_AVAILABLE, _RUNNING, OpenEMSConnectionError("restart"), _UPDATED],
        AsyncMock(side_effect=OpenEMSConnectionError("restart")),
    )

    with patch.object(update_module.asyncio, "sleep", _yield_once):
        await entity.async_install(None, False)

    assert entity.installed_version == "2.0.0"
    assert entity._edge.get_system_update_state.await_count == 4


async def test_install_raises_when_edge_rejects_the_update() -> None:
    """A rejected update request raises a translated error."""
    entity = _make_installing_entity(
        [_AVAILABLE], AsyncMock(side_effect=OpenEMSError("rejected"))
    )

    with (
        patch.object(update_module.asyncio, "sleep", _yield_once),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await entity.async_install(None, False)

    assert exc_info.value.translation_key == "update_failed"
    assert exc_info.value.translation_placeholders == {"edge": "host"}


async def test_install_raises_when_update_does_not_finish() -> None:
    """An update still running after 15 minutes raises and cancels the request."""
    pending = asyncio.Event()
    entity = _make_installing_entity(
        [_RUNNING, _RUNNING], AsyncMock(side_effect=pending.wait)
    )
    start = datetime(2026, 1, 1, 12, 0)
    times = [start, start + timedelta(minutes=5), start + timedelta(minutes=10)]
    times.append(start + timedelta(minutes=16))

    with (
        patch.object(update_module.asyncio, "sleep", _yield_once),
        patch.object(update_module.dt_util, "naive_now", side_effect=times),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await entity.async_install(None, False)

    assert exc_info.value.translation_key == "update_not_finished"
    assert entity._edge.get_system_update_state.await_count == 2
