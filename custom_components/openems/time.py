"""Component providing support for OpenEMS time entities."""

from dataclasses import dataclass
from datetime import time
import logging

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .const import DEFAULT_EDGE_CHANNELS
from .helpers import component_device
from .openems import OpenEMSBackend, OpenEMSComponent, OpenEMSEdge, OpenEMSTimeProperty

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS time entities."""

    def _create_time_entities(component: OpenEMSComponent) -> None:
        """Create Sensor Entities from channel list."""
        device = component_device(component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSTimeEntity] = []
        channel: OpenEMSTimeProperty
        channel_list: list[OpenEMSTimeProperty] = component.time_properties
        for channel in channel_list:
            entity_enabled = (
                component.name + "/" + channel.name in DEFAULT_EDGE_CHANNELS
            )
            entity_description = OpenEMSTimeDescription(
                key=channel.unique_id(),
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=entity_enabled,
                # remove "_Property" prefix
                name=channel.name[9:],
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

    backend: OpenEMSBackend = entry.runtime_data.backend
    # for all edges
    edge: OpenEMSEdge
    for edge in backend.edges.values():
        component: OpenEMSComponent
        for component in edge.components.values():
            if component.create_entities:
                _create_time_entities(component)

    # prepare callback for creating in new entities during options config flow
    entry.runtime_data.add_component_callbacks[Platform.TIME.value] = (
        _create_time_entities
    )


@dataclass(frozen=True, kw_only=True)
class OpenEMSTimeDescription(TimeEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSTimeEntity(TimeEntity):
    """Time entity class for OpenEMS channels."""

    entity_description: OpenEMSTimeDescription

    def __init__(
        self,
        channel: OpenEMSTimeProperty,
        entity_description,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize OpenEMS time entity."""
        self._channel: OpenEMSTimeProperty = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_extra_state_attributes = channel.orig_json
        self._raw_value: str = None

    def handle_current_value(self, new_value: str) -> None:
        """Handle a state update."""
        if self._raw_value != new_value:
            self._raw_value = new_value
            self.async_schedule_update_ha_state()

    @property
    def native_value(self) -> time | None:
        """Return the current time."""
        if self._raw_value is None:
            return None
        try:
            hour_str, minute_str = self._raw_value.split(":")
            return time(int(hour_str), int(minute_str))
        except ValueError:
            return None

    async def async_set_value(self, value: time) -> None:
        """Update the selected time."""
        time_str = value.strftime("%H:%M")
        await self._channel.update_value(time_str)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        self._channel.register_callback(
            self.handle_current_value,
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Entity removed."""
        self._channel.unregister_callback()
        await super().async_will_remove_from_hass()
