"""Tests for the OpenEMS button platform."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.openems import button as button_module
from custom_components.openems.button import (
    OpenEMSRestartButtonDescription,
    OpenEMSRestartButtonEntity,
)
from custom_components.openems.const import DOMAIN
from homeassistant.components.button import ButtonDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from openems_client.exceptions import OpenEMSConnectionError, OpenEMSError


def _make_edge():
    edge = MagicMock()
    edge.hostname = "host"
    edge.id = "edge-1"
    return edge


async def test_async_setup_entry_creates_buttons(hass: HomeAssistant) -> None:
    """Setup creates the edge buttons attached to the edge device."""
    mock_entry = MagicMock()
    mock_entry.runtime_data.backend.the_edge = _make_edge()
    device_info = DeviceInfo(name="host", identifiers={(DOMAIN, "host")})
    mock_entry.runtime_data.edge_device.info = device_info

    added: list = []
    await button_module.async_setup_entry(hass, mock_entry, added.extend)

    assert len(added) == 1
    (restart,) = added
    assert isinstance(restart, OpenEMSRestartButtonEntity)
    assert restart.unique_id == "host/edge-1/restart"
    assert restart.device_info == device_info
    assert restart.entity_description.device_class == ButtonDeviceClass.RESTART
    assert restart.entity_description.entity_category == EntityCategory.CONFIG
    assert restart.entity_description.entity_registry_enabled_default is False


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
        OpenEMSConnectionError("connection lost"),
        OpenEMSError("rejected"),
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
