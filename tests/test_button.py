"""Tests for the OpenEMS button platform."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jsonrpc_base
import pytest

from custom_components.openems import button as button_module
from custom_components.openems.button import (
    OpenEMSRefreshConfigurationButtonDescription,
    OpenEMSRefreshConfigurationButtonEntity,
    OpenEMSRestartButtonDescription,
    OpenEMSRestartButtonEntity,
)
from custom_components.openems.const import DOMAIN
from homeassistant.components.button import ButtonDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from openems_client.entry_data import OpenEMSConfigReader
from pytest_homeassistant_custom_component.common import MockConfigEntry


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

    assert len(added) == 2
    restart, refresh_configuration = added
    assert isinstance(restart, OpenEMSRestartButtonEntity)
    assert restart.unique_id == "host/edge-1/restart"
    assert restart.device_info == device_info
    assert restart.entity_description.device_class == ButtonDeviceClass.RESTART
    assert restart.entity_description.entity_category == EntityCategory.CONFIG
    assert restart.entity_description.entity_registry_enabled_default is False

    assert isinstance(refresh_configuration, OpenEMSRefreshConfigurationButtonEntity)
    assert refresh_configuration.unique_id == "host/edge-1/refresh_configuration"
    assert refresh_configuration.device_info == device_info
    assert (
        refresh_configuration.entity_description.entity_category
        == EntityCategory.CONFIG
    )


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


def _make_refresh_configuration_button(
    hass: HomeAssistant, entry: MockConfigEntry
) -> OpenEMSRefreshConfigurationButtonEntity:
    device_info = DeviceInfo(name="host", identifiers={(DOMAIN, "host")})
    description = OpenEMSRefreshConfigurationButtonDescription(
        key="host/edge-1/refresh_configuration"
    )
    entity = OpenEMSRefreshConfigurationButtonEntity(
        _make_edge(), description, device_info
    )
    entity.hass = hass
    entity.platform = MagicMock(config_entry=entry)
    return entity


async def test_refresh_configuration_press_caches_configuration_and_reloads(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Pressing the button caches the configuration read from the edge and reloads."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entity = _make_refresh_configuration_button(hass, entry)

    with (
        patch.object(
            OpenEMSConfigReader,
            "read_edge_components",
            AsyncMock(return_value={"comp1": {"channels": []}}),
        ),
        patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload,
    ):
        await entity.async_press()

    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"] == {
        "comp1": {"channels": []}
    }
    schedule_reload.assert_called_once_with(entry.entry_id)


@pytest.mark.parametrize(
    "error",
    [
        jsonrpc_base.TransportError("connection lost"),
        jsonrpc_base.ProtocolError(1, "rejected"),
    ],
)
async def test_refresh_configuration_press_raises_on_error(
    hass: HomeAssistant, hass_storage: dict[str, Any], error: Exception
) -> None:
    """Backend errors are reported as HomeAssistantError, the cache stays untouched."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entity = _make_refresh_configuration_button(hass, entry)

    with (
        patch.object(
            OpenEMSConfigReader, "read_edge_components", AsyncMock(side_effect=error)
        ),
        patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload,
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await entity.async_press()

    assert exc_info.value.translation_key == "refresh_configuration_failed"
    assert exc_info.value.translation_placeholders == {"edge": "host"}
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage
    schedule_reload.assert_not_called()
