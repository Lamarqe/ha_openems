"""Tests for the OpenEMS button platform."""

from unittest.mock import AsyncMock, MagicMock, patch

import jsonrpc_base
import pytest

from custom_components.openems import button as button_module
from custom_components.openems.button import (
    OpenEMSCheckUpdateButtonDescription,
    OpenEMSCheckUpdateButtonEntity,
    OpenEMSRestartButtonDescription,
    OpenEMSRestartButtonEntity,
)
from custom_components.openems.const import DOMAIN
from homeassistant.components.button import ButtonDeviceClass
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo


def _make_edge():
    edge = MagicMock()
    edge.hostname = "host"
    edge.id = "edge-1"
    return edge


def _make_check_update_button(hass: HomeAssistant) -> OpenEMSCheckUpdateButtonEntity:
    device_info = DeviceInfo(name="host", identifiers={(DOMAIN, "host")})
    description = OpenEMSCheckUpdateButtonDescription(key="host/edge-1/check_update")
    entity = OpenEMSCheckUpdateButtonEntity(_make_edge(), description, device_info)
    entity.hass = hass
    return entity


async def test_async_setup_entry_creates_buttons(hass: HomeAssistant) -> None:
    """Setup creates the check update and restart buttons attached to the edge device."""
    mock_entry = MagicMock()
    mock_entry.runtime_data.backend.the_edge = _make_edge()
    device_info = DeviceInfo(name="host", identifiers={(DOMAIN, "host")})
    mock_entry.runtime_data.edge_device.info = device_info

    added: list = []
    await button_module.async_setup_entry(hass, mock_entry, added.extend)

    assert len(added) == 2
    check_update, restart = added
    assert isinstance(check_update, OpenEMSCheckUpdateButtonEntity)
    assert check_update.unique_id == "host/edge-1/check_update"
    assert check_update.device_info == device_info
    assert check_update.entity_description.device_class == ButtonDeviceClass.UPDATE
    assert check_update.entity_description.entity_category == EntityCategory.CONFIG

    assert isinstance(restart, OpenEMSRestartButtonEntity)
    assert restart.unique_id == "host/edge-1/restart"
    assert restart.device_info == device_info
    assert restart.entity_description.device_class == ButtonDeviceClass.RESTART
    assert restart.entity_description.entity_category == EntityCategory.CONFIG


async def test_press_refreshes_update_entity(hass: HomeAssistant) -> None:
    """Pressing the button refreshes the update entity of the same edge."""
    registry_entry = er.async_get(hass).async_get_or_create(
        Platform.UPDATE, DOMAIN, "host/edge-1/update"
    )
    entity = _make_check_update_button(hass)

    with patch.object(button_module, "async_update_entity", AsyncMock()) as mock_update:
        await entity.async_press()

    mock_update.assert_awaited_once_with(hass, registry_entry.entity_id)


async def test_press_without_update_entity_does_nothing(hass: HomeAssistant) -> None:
    """Pressing the button without a registered update entity is a no-op."""
    entity = _make_check_update_button(hass)

    with patch.object(button_module, "async_update_entity", AsyncMock()) as mock_update:
        await entity.async_press()

    mock_update.assert_not_awaited()


async def test_press_fails_silently(hass: HomeAssistant) -> None:
    """Errors while refreshing the update entity are not raised."""
    er.async_get(hass).async_get_or_create(
        Platform.UPDATE, DOMAIN, "host/edge-1/update"
    )
    entity = _make_check_update_button(hass)

    with patch.object(
        button_module,
        "async_update_entity",
        AsyncMock(side_effect=HomeAssistantError("Entity not found")),
    ) as mock_update:
        await entity.async_press()

    mock_update.assert_awaited_once()


def _make_restart_button(edge) -> OpenEMSRestartButtonEntity:
    device_info = DeviceInfo(name="host", identifiers={(DOMAIN, "host")})
    description = OpenEMSRestartButtonDescription(key="host/edge-1/restart")
    return OpenEMSRestartButtonEntity(edge, description, device_info)


async def test_restart_press_triggers_restart() -> None:
    """Pressing the restart button triggers the restart of the edge."""
    edge = _make_edge()
    edge.execute_system_restart = AsyncMock()
    entity = _make_restart_button(edge)

    await entity.async_press()

    edge.execute_system_restart.assert_awaited_once_with()


@pytest.mark.parametrize(
    "error",
    [
        jsonrpc_base.TransportError("connection lost"),
        jsonrpc_base.ProtocolError(1, "rejected"),
    ],
)
async def test_restart_press_raises_on_error(error: Exception) -> None:
    """Backend errors are reported as HomeAssistantError."""
    edge = _make_edge()
    edge.execute_system_restart = AsyncMock(side_effect=error)
    entity = _make_restart_button(edge)

    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.async_press()

    assert exc_info.value.translation_key == "restart_failed"
    assert exc_info.value.translation_placeholders == {"edge": "host"}
