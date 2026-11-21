"""Component providing support for OpenEMS sensors."""

from dataclasses import dataclass
import logging
from typing import Any

from openems_client.model import ChannelKind, Quantity
from openems_client.openems import (
    OpenEMSBackend,
    OpenEMSChannel,
    OpenEMSComponent,
    OpenEMSDataHandler,
)

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .const import (
    ATTR_TIMEOUT,
    ATTR_UPDATE_CYCLE,
    ATTR_VALUE,
    CONF_ADVANCED_OPTIONS,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
    DOMAIN,
)
from .helpers_ha import OpenEMSConfigEntry, component_device

_LOGGER = logging.getLogger(__name__)

DEVICE_CLASSES: dict[Quantity, SensorDeviceClass] = {
    Quantity.APPARENT_POWER: SensorDeviceClass.APPARENT_POWER,
    Quantity.CURRENT: SensorDeviceClass.CURRENT,
    Quantity.DURATION: SensorDeviceClass.DURATION,
    Quantity.ENERGY: SensorDeviceClass.ENERGY,
    Quantity.FREQUENCY: SensorDeviceClass.FREQUENCY,
    Quantity.POWER: SensorDeviceClass.POWER,
    Quantity.PRESSURE: SensorDeviceClass.PRESSURE,
    Quantity.REACTIVE_POWER: SensorDeviceClass.REACTIVE_POWER,
    Quantity.STATE_OF_CHARGE: SensorDeviceClass.BATTERY,
    Quantity.TEMPERATURE: SensorDeviceClass.TEMPERATURE,
    Quantity.VOLTAGE: SensorDeviceClass.VOLTAGE,
}


def _state_class(channel: OpenEMSDataHandler) -> SensorStateClass:
    if channel.cumulative:
        return SensorStateClass.TOTAL_INCREASING
    if channel.quantity is Quantity.ENERGY:
        return SensorStateClass.TOTAL
    return SensorStateClass.MEASUREMENT


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenEMS sensor entities."""

    def _create_sensor_entities(component: OpenEMSComponent) -> None:
        """Create Sensor Entities from channel list."""
        device = component_device(entry.runtime_data.edge_device.entry, component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSSensorEntity] = [
            OpenEMSSensorEntity(
                channel,
                OpenEMSSensorDescription(
                    key=channel.unique_id,
                    entity_registry_enabled_default=channel.primary,
                    # primary channels have a translated name
                    name=UNDEFINED if channel.primary else channel.name,
                    device_class=SensorDeviceClass.ENUM,
                    options=channel.option_keys,
                    translation_key=channel.key,
                ),
                device,
            )
            for channel in component.channels_of(ChannelKind.ENUM_SENSOR)
        ]
        entities.extend(
            OpenEMSSensorEntity(
                channel,
                OpenEMSSensorDescription(
                    key=channel.unique_id,
                    entity_registry_enabled_default=channel.primary,
                    # primary channels have a translated name
                    name=UNDEFINED if channel.primary else channel.name,
                    device_class=DEVICE_CLASSES.get(channel.quantity)
                    if channel.quantity
                    else None,
                    state_class=_state_class(channel),
                    native_unit_of_measurement=channel.unit,
                    translation_key=channel.key,
                    ignore_decreasing=ignore_decreasing,
                ),
                device,
            )
            for channel in component.channels_of(ChannelKind.SENSOR)
        )
        async_add_entities(entities)

    ############ END MARKER _create_sensor_entities ##############

    backend: OpenEMSBackend = entry.runtime_data.backend
    ignore_decreasing: bool = entry.options[CONF_ADVANCED_OPTIONS][
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING
    ]
    device_registry = dr.async_get(hass)
    # Create the edge device
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        name=backend.the_edge.hostname,
        identifiers={(DOMAIN, backend.the_edge.hostname)},
    )
    component: OpenEMSComponent
    for component in entry.runtime_data.enabled_components:
        _create_sensor_entities(component)


@dataclass(frozen=True, kw_only=True)
class OpenEMSSensorDescription(SensorEntityDescription):
    """Definition of OpenEMS sensor attributes."""

    has_entity_name = True
    # drop decreasing values of total increasing sensors, see advanced options
    ignore_decreasing: bool = False


class OpenEMSSensorEntity(SensorEntity):
    """Representation of a sensor."""

    entity_description: OpenEMSSensorDescription

    def __init__(
        self,
        channel: OpenEMSDataHandler,
        entity_description,
        device_info: dr.DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        self._channel: OpenEMSDataHandler = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self.previous_increasing_value_not_null: float | None = None

    @property
    def native_value(self) -> str | float | int | None:
        """Return the value of the sensor."""
        val = self._channel.value
        if val is None or isinstance(val, str):
            return val
        if self._ignore_decrease(val):
            return self.previous_increasing_value_not_null
        return val

    def _ignore_decrease(self, val: float) -> bool:
        """Return True if val is a decrease of a total increasing sensor to be ignored."""
        return (
            self.entity_description.state_class == SensorStateClass.TOTAL_INCREASING
            and self.entity_description.ignore_decreasing
            and self.previous_increasing_value_not_null is not None
            and self.previous_increasing_value_not_null > val
        )

    @callback
    def _handle_channel_update(self) -> None:
        """Remember the last accepted value of total increasing sensors, write the state."""
        val = self._channel.value
        if (
            self.entity_description.state_class == SensorStateClass.TOTAL_INCREASING
            and val is not None
            and not isinstance(val, str)
        ):
            if self._ignore_decrease(val):
                _LOGGER.debug(
                    "Ignoring new backend value %s for %s since it is smaller than the previous value %.1f and state class is total_increasing",
                    val,
                    self.entity_id,
                    self.previous_increasing_value_not_null,
                )
            else:
                self.previous_increasing_value_not_null = val
        self.async_schedule_update_ha_state()

    async def update_value(self, **kwargs: Any) -> None:
        """Service callback to change value via REST call."""
        if isinstance(self._channel, OpenEMSChannel):
            await self._channel.write_via_rest(
                float(kwargs[ATTR_VALUE]),
                kwargs.get(ATTR_UPDATE_CYCLE),  # pyright: ignore[reportArgumentType]
                kwargs.get(ATTR_TIMEOUT),  # pyright: ignore[reportArgumentType]
            )

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        self.async_on_remove(self._channel.add_listener(self._handle_channel_update))
        await super().async_added_to_hass()
