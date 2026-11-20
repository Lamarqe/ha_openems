"""Component providing support for OpenEMS buttons."""

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from openems_client.connection import OpenEMSConfigReader
from openems_client.exceptions import OpenEMSError
from openems_client.openems import OpenEMSBackend, OpenEMSEdge

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .helpers_ha import OpenEMSConfigEntry, component_store

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS button entities."""
    backend: OpenEMSBackend = entry.runtime_data.backend
    entity_description = OpenEMSRestartButtonDescription(
        key=backend.the_edge.hostname + "/" + backend.the_edge.id + "/restart"
    )
    restart_entity = OpenEMSRestartButtonEntity(
        backend.the_edge, entity_description, entry.runtime_data.edge_device.info
    )

    entity_description = OpenEMSRefreshConfigurationButtonDescription(
        key=backend.the_edge.hostname
        + "/"
        + backend.the_edge.id
        + "/refresh_configuration"
    )
    refresh_configuration_entity = OpenEMSRefreshConfigurationButtonEntity(
        backend.the_edge, entity_description, entry.runtime_data.edge_device.info
    )
    async_add_entities([restart_entity, refresh_configuration_entity])


@dataclass(frozen=True, kw_only=True)
class OpenEMSRestartButtonDescription(ButtonEntityDescription):
    """Definition of OpenEMS restart button attributes."""

    has_entity_name = True
    translation_key = "restart"
    device_class = ButtonDeviceClass.RESTART
    entity_category = EntityCategory.CONFIG
    entity_registry_enabled_default = False


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
        except OpenEMSError as ex:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="restart_failed",
                translation_placeholders={"edge": self._edge.hostname},
            ) from ex
        _LOGGER.info("Restart triggered for edge %s", self._edge.hostname)


@dataclass(frozen=True, kw_only=True)
class OpenEMSRefreshConfigurationButtonDescription(ButtonEntityDescription):
    """Definition of OpenEMS refresh configuration button attributes."""

    has_entity_name = True
    translation_key = "refresh_configuration"
    entity_category = EntityCategory.CONFIG


class OpenEMSRefreshConfigurationButtonEntity(ButtonEntity):
    """Button entity to read the edge configuration again."""

    entity_description: OpenEMSRefreshConfigurationButtonDescription

    def __init__(
        self,
        edge: OpenEMSEdge,
        entity_description: OpenEMSRefreshConfigurationButtonDescription,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the button."""
        self._edge: OpenEMSEdge = edge
        self.entity_description = entity_description
        self._attr_unique_id = entity_description.key
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Read the edge configuration into the cache and reload the config entry."""
        config_entry = self.platform.config_entry
        if TYPE_CHECKING:
            assert config_entry is not None
        config_reader = OpenEMSConfigReader(
            self._edge.backend.connection, self._edge.id
        )
        try:
            components = await config_reader.read_edge_components()
        except OpenEMSError as ex:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="refresh_configuration_failed",
                translation_placeholders={"edge": self._edge.hostname},
            ) from ex
        await component_store(self.hass, config_entry.entry_id).async_save(components)
        self.hass.config_entries.async_schedule_reload(config_entry.entry_id)
