"""Component providing support for OpenEMS number entities."""

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .const import DEFAULT_EDGE_CHANNELS
from .helpers import component_device
from .openems import (
    OpenEMSBackend,
    OpenEMSBooleanProperty,
    OpenEMSComponent,
    OpenEMSEdge,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS switch entities."""

    def _create_switch_entities(component: OpenEMSComponent) -> None:
        """Create Sensor Entities from channel list."""
        device = component_device(component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSSwitchEntity] = []
        channel: OpenEMSBooleanProperty
        channel_list: list[OpenEMSBooleanProperty] = component.boolean_properties
        for channel in channel_list:
            entity_enabled = (
                component.name + "/" + channel.name in DEFAULT_EDGE_CHANNELS
            )
            entity_description = OpenEMSSwitchDescription(
                key=channel.unique_id(),
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=entity_enabled,
                # remove "_Property" prefix
                name=channel.name[9:],
            )
            entities.append(
                OpenEMSSwitchEntity(
                    channel,
                    entity_description,
                    device,
                )
            )
        async_add_entities(entities)

    ############ END MARKER _create_switch_entities ##############

    backend: OpenEMSBackend = entry.runtime_data.backend
    # for all edges
    edge: OpenEMSEdge
    for edge in backend.edges.values():
        component: OpenEMSComponent
        for component in edge.components.values():
            if component.create_entities:
                _create_switch_entities(component)

    # prepare callback for creating in new entities during options config flow
    entry.runtime_data.add_component_callbacks[Platform.SWITCH.value] = (
        _create_switch_entities
    )


@dataclass(frozen=True, kw_only=True)
class OpenEMSSwitchDescription(SwitchEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSSwitchEntity(SwitchEntity):
    """Number entity class for OpenEMS channels."""

    entity_description: OpenEMSSwitchDescription

    def __init__(
        self,
        channel: OpenEMSBooleanProperty,
        entity_description,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize OpenEMS switch entity."""
        self._channel: OpenEMSBooleanProperty = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_extra_state_attributes = channel.orig_json
        self._raw_value = None

    def handle_current_value(self, value) -> None:
        """Handle a state update."""
        if self._raw_value != value:
            was_on = self.is_on
            self._raw_value = value
            if was_on != self.is_on:
                self.async_schedule_update_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        if isinstance(self._raw_value, int):
            return bool(self._raw_value)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._channel.update_value(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._channel.update_value(False)
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
