"""Component providing support for OpenEMS number entities."""

from dataclasses import dataclass
import logging

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .helpers import (
    DeviceInfo,
    OpenEMSUnitClass,
    component_device,
    translation_key,
    unit_description,
)
from .openems import CONFIG, OpenEMSBackend, OpenEMSComponent, OpenEMSNumberProperty

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS number entities."""

    def _create_number_entities(component: OpenEMSComponent) -> None:
        """Create Number Entities from channel list."""
        device = component_device(component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSNumberEntity] = []
        channel: OpenEMSNumberProperty
        channel_list: list[OpenEMSNumberProperty] = component.number_properties
        for channel in channel_list:
            entity_enabled = CONFIG.is_channel_enabled(component.name, channel.name)
            unit_desc: OpenEMSUnitClass = unit_description(channel.unit)
            entity_description = OpenEMSNumberDescription(
                key=channel.unique_id(),
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=entity_enabled,
                mode=NumberMode.SLIDER,
                # remove "_Property" prefix
                name=channel.name[9:],
                device_class=unit_desc.number_device_class,
                native_unit_of_measurement=unit_desc.unit,
                translation_key=translation_key(channel),
            )
            entities.append(
                OpenEMSNumberEntity(
                    channel,
                    entity_description,
                    device,
                )
            )
        async_add_entities(entities)

    ############ END MARKER _create_number_entities ##############

    backend: OpenEMSBackend = entry.runtime_data.backend
    component: OpenEMSComponent
    for component in backend.the_edge.components.values():
        if component.create_entities:
            _create_number_entities(component)

    # prepare callback for creating in new entities during options config flow
    entry.runtime_data.add_component_callbacks[Platform.NUMBER.value] = (
        _create_number_entities
    )


@dataclass(frozen=True, kw_only=True)
class OpenEMSNumberDescription(NumberEntityDescription):
    """Defintion of OpenEMS number attributes."""

    has_entity_name = True


class OpenEMSNumberEntity(NumberEntity):
    """Number entity class for OpenEMS channels."""

    entity_description: OpenEMSNumberDescription

    def __init__(
        self,
        channel: OpenEMSNumberProperty,
        entity_description,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize OpenEMS number entity."""
        self._channel: OpenEMSNumberProperty = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_extra_state_attributes = channel.orig_json

    @property
    def native_min_value(self):
        """Return minimum value."""
        return self._channel.lower_limit

    @property
    def native_max_value(self):
        """Return maximum value."""
        return self._channel.upper_limit

    @property
    def native_step(self):
        """Return step size."""
        return self._channel.step

    @property
    def native_value(self) -> float | None:
        """Return the value of the number entity."""
        return self._channel.native_value

    async def async_set_native_value(self, value: float) -> None:
        """Change the current value."""
        await self._channel.update_value(value)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        self._channel.register_callback(
            self.async_schedule_update_ha_state,
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Entity removed."""
        self._channel.unregister_callback()
        await super().async_will_remove_from_hass()
