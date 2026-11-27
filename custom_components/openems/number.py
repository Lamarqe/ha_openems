"""Component providing support for OpenEMS number entities."""

from dataclasses import dataclass
from typing import override

from openems_client.model import ChannelKind, Quantity
from openems_client.openems import OpenEMSComponent, OpenEMSNumberProperty

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .entity import OpenEMSChannelEntity
from .helpers_ha import OpenEMSConfigEntry, component_device, handle_write_errors

# writes of update groups also send the current values of other properties
PARALLEL_UPDATES = 1

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
        """Create number entities from channel list."""
        device = component_device(entry.runtime_data.edge_device.entry, component)

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
                # primary channels have a translated name, others a generated one
                name=UNDEFINED if entity_enabled else channel.display_name,
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
    @override
    def native_min_value(self) -> float:
        """Return minimum value."""
        return self._channel.lower_limit

    @property
    @override
    def native_max_value(self) -> float:
        """Return maximum value."""
        return self._channel.upper_limit

    @property
    @override
    def native_step(self) -> float:
        """Return step size."""
        return self._channel.step

    @property
    @override
    def native_value(self) -> float | None:
        """Return the value of the number entity."""
        return self._channel.value

    @handle_write_errors
    @override
    async def async_set_native_value(self, value: float) -> None:
        """Change the current value."""
        await self._channel.update_value(value)
        self.async_write_ha_state()
