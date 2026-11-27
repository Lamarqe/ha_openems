"""Component providing support for OpenEMS time entities."""

from dataclasses import dataclass
from datetime import time
from typing import override

from openems_client.model import ChannelKind
from openems_client.openems import OpenEMSComponent, OpenEMSTimeProperty

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .entity import OpenEMSChannelEntity
from .helpers_ha import OpenEMSConfigEntry, component_device, handle_write_errors

# writes of update groups also send the current values of other properties
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS time entities."""

    def _create_time_entities(component: OpenEMSComponent) -> None:
        """Create time entities from channel list."""
        device = component_device(entry.runtime_data.edge_device.entry, component)

        entities: list[OpenEMSTimeEntity] = []
        channel: OpenEMSTimeProperty
        channel_list: list[OpenEMSTimeProperty] = component.channels_of(
            ChannelKind.TIME, OpenEMSTimeProperty
        )
        for channel in channel_list:
            entity_enabled = channel.primary
            entity_description = OpenEMSTimeDescription(
                key=channel.unique_id,
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=entity_enabled,
                # primary channels have a translated name, others a generated one
                name=UNDEFINED if entity_enabled else channel.display_name,
                translation_key=channel.key,
            )
            entities.append(
                OpenEMSTimeEntity(
                    channel,
                    entity_description,
                    device,
                )
            )
        async_add_entities(entities)

    component: OpenEMSComponent
    for component in entry.runtime_data.enabled_components:
        _create_time_entities(component)


@dataclass(frozen=True, kw_only=True)
class OpenEMSTimeDescription(TimeEntityDescription):
    """Definition of OpenEMS time attributes."""

    has_entity_name = True


class OpenEMSTimeEntity(OpenEMSChannelEntity[OpenEMSTimeProperty], TimeEntity):
    """Time entity class for OpenEMS channels."""

    entity_description: OpenEMSTimeDescription

    @property
    @override
    def native_value(self) -> time | None:
        """Return the current time."""
        return self._channel.value

    @handle_write_errors
    @override
    async def async_set_value(self, value: time) -> None:
        """Update the selected time."""
        await self._channel.update_value(value)
        self.async_write_ha_state()
