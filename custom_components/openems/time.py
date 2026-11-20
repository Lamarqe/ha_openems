"""Component providing support for OpenEMS time entities."""

from dataclasses import dataclass
from datetime import time
import logging

from openems_client.openems import OpenEMSComponent, OpenEMSTimeProperty

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .helpers_ha import (
    OpenEMSConfigEntry,
    component_device,
    handle_write_errors,
    is_channel_enabled,
    translation_key,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS time entities."""

    def _create_time_entities(component: OpenEMSComponent) -> None:
        """Create Sensor Entities from channel list."""
        device = component_device(entry.runtime_data.edge_device.entry, component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSTimeEntity] = []
        channel: OpenEMSTimeProperty
        channel_list: list[OpenEMSTimeProperty] = component.time_properties
        for channel in channel_list:
            entity_enabled = is_channel_enabled(component.name, channel.name)
            entity_description = OpenEMSTimeDescription(
                key=channel.unique_id(),
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=entity_enabled,
                # channels enabled by default have a translated name, others
                # use the channel name without "_Property" prefix
                name=UNDEFINED if entity_enabled else channel.name[9:],
                translation_key=translation_key(channel),
            )
            entities.append(
                OpenEMSTimeEntity(
                    channel,
                    entity_description,
                    device,
                )
            )
        async_add_entities(entities)

    ############ END MARKER _create_time_entities ##############

    component: OpenEMSComponent
    for component in entry.runtime_data.enabled_components:
        _create_time_entities(component)


@dataclass(frozen=True, kw_only=True)
class OpenEMSTimeDescription(TimeEntityDescription):
    """Definition of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSTimeEntity(TimeEntity):
    """Time entity class for OpenEMS channels."""

    entity_description: OpenEMSTimeDescription

    def __init__(
        self,
        channel: OpenEMSTimeProperty,
        entity_description,
        device_info: dr.DeviceInfo,
    ) -> None:
        """Initialize OpenEMS time entity."""
        self._channel: OpenEMSTimeProperty = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False

    @property
    def native_value(self) -> time | None:
        """Return the current time."""
        return self._channel.value

    @handle_write_errors
    async def async_set_value(self, value: time) -> None:
        """Update the selected time."""
        await self._channel.update_value(value)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        self.async_on_remove(
            self._channel.add_listener(self.async_schedule_update_ha_state)
        )
        await super().async_added_to_hass()
