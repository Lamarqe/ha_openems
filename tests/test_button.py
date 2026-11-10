"""Tests for the OpenEMS button platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.openems import button as button_module
from custom_components.openems.button import BUTTON_DESCRIPTIONS, OpenEMSButtonEntity
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


def _make_check_update_button(hass: HomeAssistant) -> OpenEMSButtonEntity:
    device_info = DeviceInfo(name="host", identifiers={(DOMAIN, "host")})
    entity = OpenEMSButtonEntity(_make_edge(), BUTTON_DESCRIPTIONS[0], device_info)
    entity.hass = hass
    return entity


async def test_async_setup_entry_creates_buttons(hass: HomeAssistant) -> None:
    """Setup creates the check update button attached to the edge device."""
    mock_entry = MagicMock()
    mock_entry.runtime_data.backend.the_edge = _make_edge()
    device_info = DeviceInfo(name="host", identifiers={(DOMAIN, "host")})
    mock_entry.runtime_data.edge_device.info = device_info

    added: list[OpenEMSButtonEntity] = []
    await button_module.async_setup_entry(hass, mock_entry, added.extend)

    assert len(added) == 1
    entity = added[0]
    assert entity.unique_id == "host/edge-1/check_update"
    assert entity.device_info == device_info
    assert entity.entity_description.device_class == ButtonDeviceClass.UPDATE
    assert entity.entity_description.entity_category == EntityCategory.CONFIG


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
