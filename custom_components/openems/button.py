"""Component providing support for OpenEMS buttons."""

from dataclasses import dataclass
import logging

import jsonrpc_base

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
from .openems import OpenEMSBackend, OpenEMSEdge
from .update import update_unique_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS button entities."""
    backend: OpenEMSBackend = entry.runtime_data.backend
    entity_description = OpenEMSCheckUpdateButtonDescription(
        key=backend.the_edge.hostname + "/" + backend.the_edge.id + "/check_update"
    )

    check_update_entity = OpenEMSCheckUpdateButtonEntity(
        backend.the_edge, entity_description, entry.runtime_data.edge_device.info
    )

    entity_description = OpenEMSRestartButtonDescription(
        key=backend.the_edge.hostname + "/" + backend.the_edge.id + "/restart"
    )
    restart_entity = OpenEMSRestartButtonEntity(
        backend.the_edge, entity_description, entry.runtime_data.edge_device.info
    )
    async_add_entities([check_update_entity, restart_entity])


@dataclass(frozen=True, kw_only=True)
class OpenEMSCheckUpdateButtonDescription(ButtonEntityDescription):
    """Definition of OpenEMS check update button attributes."""

    has_entity_name = True
    translation_key = "check_update"
    device_class = ButtonDeviceClass.UPDATE
    entity_category = EntityCategory.CONFIG


class OpenEMSCheckUpdateButtonEntity(ButtonEntity):
    """Button entity to manually trigger the OpenEMS update check."""

    entity_description: OpenEMSCheckUpdateButtonDescription

    def __init__(
        self,
        edge: OpenEMSEdge,
        entity_description: OpenEMSCheckUpdateButtonDescription,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the button."""
        self._edge: OpenEMSEdge = edge
        self.entity_description = entity_description
        self._attr_unique_id = entity_description.key
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Refresh the update entity of the edge. Fails silently."""
        unique_id = update_unique_id(self._edge)
        entity_id = er.async_get(self.hass).async_get_entity_id(
            Platform.UPDATE, DOMAIN, unique_id
        )
        if entity_id is None:
            _LOGGER.debug("No update entity found for %s", unique_id)
            return
        try:
            await async_update_entity(self.hass, entity_id)
        except HomeAssistantError as ex:
            # e.g. the update entity is disabled
            _LOGGER.debug("Cannot refresh update entity %s: %s", entity_id, ex)


@dataclass(frozen=True, kw_only=True)
class OpenEMSRestartButtonDescription(ButtonEntityDescription):
    """Definition of OpenEMS restart button attributes."""

    has_entity_name = True
    translation_key = "restart"
    device_class = ButtonDeviceClass.RESTART
    entity_category = EntityCategory.CONFIG


class OpenEMSRestartButtonEntity(ButtonEntity):
    """Button entity to restart the OpenEMS edge."""

    entity_description: OpenEMSRestartButtonDescription

    def __init__(
        self,
        edge: OpenEMSEdge,
        entity_description: OpenEMSRestartButtonDescription,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the button."""
        self._edge: OpenEMSEdge = edge
        self.entity_description = entity_description
        self._attr_unique_id = entity_description.key
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Trigger a hard restart of the edge."""
        try:
            await self._edge.execute_system_restart()
        except (
            jsonrpc_base.jsonrpc.TransportError,
            jsonrpc_base.jsonrpc.ProtocolError,
        ) as ex:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="restart_failed",
                translation_placeholders={"edge": self._edge.hostname},
            ) from ex
        _LOGGER.info("Restart triggered for edge %s", self._edge.hostname)
