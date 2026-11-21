"""Component providing support for OpenEMS number entities."""

from dataclasses import dataclass
import logging

from openems_client.model import PROPERTY_PREFIX, ChannelKind, Quantity
from openems_client.openems import OpenEMSComponent, OpenEMSNumberProperty

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .entity import OpenEMSChannelEntity
from .helpers_ha import OpenEMSConfigEntry, component_device, handle_write_errors

_LOGGER = logging.getLogger(__name__)

DEVICE_CLASSES: dict[Quantity, NumberDeviceClass] = {
    Quantity.APPARENT_POWER: NumberDeviceClass.APPARENT_POWER,
    Quantity.CURRENT: NumberDeviceClass.CURRENT,
    Quantity.DURATION: NumberDeviceClass.DURATION,
    Quantity.ENERGY: NumberDeviceClass.ENERGY,
    Quantity.FREQUENCY: NumberDeviceClass.FREQUENCY,
    Quantity.POWER: NumberDeviceClass.POWER,
    Quantity.PRESSURE: NumberDeviceClass.PRESSURE,
    Quantity.REACTIVE_POWER: NumberDeviceClass.REACTIVE_POWER,
    Quantity.STATE_OF_CHARGE: NumberDeviceClass.BATTERY,
    Quantity.TEMPERATURE: NumberDeviceClass.TEMPERATURE,
    Quantity.VOLTAGE: NumberDeviceClass.VOLTAGE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS number entities."""

    def _create_number_entities(component: OpenEMSComponent) -> None:
        """Create Number Entities from channel list."""
        device = component_device(entry.runtime_data.edge_device.entry, component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSNumberEntity] = []
        channel: OpenEMSNumberProperty
        channel_list: list[OpenEMSNumberProperty] = component.channels_of(
            ChannelKind.NUMBER, OpenEMSNumberProperty
        )
        for channel in channel_list:
            entity_enabled = channel.primary
            # a fixed step is configured for precise values, which a slider cannot handle
            number_mode = (
                NumberMode.BOX if channel.fixed_step is not None else NumberMode.AUTO
            )
            entity_description = OpenEMSNumberDescription(
                key=channel.unique_id,
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=entity_enabled,
                mode=number_mode,
                # primary channels have a translated name, others
                # use the channel name without "_Property" prefix
                name=UNDEFINED
                if entity_enabled
                else channel.name.removeprefix(PROPERTY_PREFIX),
                device_class=DEVICE_CLASSES.get(channel.quantity)
                if channel.quantity
                else None,
                native_unit_of_measurement=channel.unit,
                translation_key=channel.key,
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

    component: OpenEMSComponent
    for component in entry.runtime_data.enabled_components:
        _create_number_entities(component)


@dataclass(frozen=True, kw_only=True)
class OpenEMSNumberDescription(NumberEntityDescription):
    """Definition of OpenEMS number attributes."""

    has_entity_name = True


class OpenEMSNumberEntity(OpenEMSChannelEntity[OpenEMSNumberProperty], NumberEntity):
    """Number entity class for OpenEMS channels."""

    entity_description: OpenEMSNumberDescription

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
        return self._channel.value

    @handle_write_errors
    async def async_set_native_value(self, value: float) -> None:
        """Change the current value."""
        await self._channel.update_value(value)
        self.async_write_ha_state()
