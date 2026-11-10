"""Component providing support for OpenEMS button entities."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .helpers_ha import OpenEMSConfigEntry
from .openems import OpenEMSEdge
from .update import update_unique_id

_LOGGER = logging.getLogger(__name__)


async def _check_for_update(hass: HomeAssistant, edge: OpenEMSEdge) -> None:
    """Refresh the update entity of the edge. Fails silently."""
    unique_id = update_unique_id(edge)
    entity_id = er.async_get(hass).async_get_entity_id(
        Platform.UPDATE, DOMAIN, unique_id
    )
    if entity_id is None:
        _LOGGER.debug("No update entity found for %s", unique_id)
        return
    try:
        await async_update_entity(hass, entity_id)
    except HomeAssistantError as ex:
        # e.g. the update entity is disabled
        _LOGGER.debug("Cannot refresh update entity %s: %s", entity_id, ex)


@dataclass(frozen=True, kw_only=True)
class OpenEMSButtonDescription(ButtonEntityDescription):
    """Definition of OpenEMS button attributes."""

    press_fn: Callable[[HomeAssistant, OpenEMSEdge], Awaitable[None]]


BUTTON_DESCRIPTIONS: tuple[OpenEMSButtonDescription, ...] = (
    OpenEMSButtonDescription(
        key="check_update",
        translation_key="check_update",
        device_class=ButtonDeviceClass.UPDATE,
        entity_category=EntityCategory.CONFIG,
        press_fn=_check_for_update,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS button entities."""
    edge = entry.runtime_data.backend.the_edge
    device_info = entry.runtime_data.edge_device.info
    async_add_entities(
        OpenEMSButtonEntity(edge, description, device_info)
        for description in BUTTON_DESCRIPTIONS
    )


class OpenEMSButtonEntity(ButtonEntity):
    """Representation of an OpenEMS button entity."""

    _attr_has_entity_name = True
    entity_description: OpenEMSButtonDescription

    def __init__(
        self,
        edge: OpenEMSEdge,
        entity_description: OpenEMSButtonDescription,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the button."""
        self._edge = edge
        self.entity_description = entity_description
        self._attr_unique_id = f"{edge.hostname}/{edge.id}/{entity_description.key}"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.entity_description.press_fn(self.hass, self._edge)
