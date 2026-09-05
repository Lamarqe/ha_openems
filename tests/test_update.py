"""Tests for the OpenEMS update platform."""

from unittest.mock import AsyncMock, MagicMock

import jsonrpc_base

from custom_components.openems import update as update_module
from custom_components.openems.update import (
    OpenEMSUpdateDescription,
    OpenEMSUpdateEntity,
)
from homeassistant.core import HomeAssistant
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


async def test_async_update_transport_error_marks_unavailable() -> None:
    """A TransportError during polling must not propagate."""
    edge = _make_edge()
    edge.get_system_update_state = AsyncMock(
        side_effect=jsonrpc_base.TransportError("connection lost")
    )
    entity = _make_entity(edge)
    await entity.async_update()

    assert entity._attr_available is False


async def test_async_update_protocol_error_marks_unavailable() -> None:
    """A ProtocolError during polling must not propagate."""
    edge = _make_edge()
    edge.get_system_update_state = AsyncMock(
        side_effect=jsonrpc_base.jsonrpc.ProtocolError(500, "error")
    )
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
