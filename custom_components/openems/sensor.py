"""Component providing support for OpenEMS sensors."""

from dataclasses import dataclass
import logging
import re
from typing import Any

from openems_client.const import CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING
from openems_client.openems import (
    DerivedChannelDefinition,
    OpenEMSBackend,
    OpenEMSChannel,
    OpenEMSComponent,
    OpenEMSDataHandler,
    OpenEMSDerivedChannel,
)

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .const import ATTR_TIMEOUT, ATTR_UPDATE_CYCLE, ATTR_VALUE, DOMAIN
from .helpers_ha import (
    OpenEMSConfigEntry,
    OpenEMSUnitClass,
    component_device,
    is_channel_enabled,
    to_snake_case,
    translation_key,
    unit_description,
)

_LOGGER = logging.getLogger(__name__)

# key: component regexp
COMBINED_SENSORS: dict[str, tuple[DerivedChannelDefinition, ...]] = {
    r"battery\d+": (
        DerivedChannelDefinition(
            id="VoltageDifference",
            sources={"max": "MaxCellVoltage", "min": "MinCellVoltage"},
            value_fn=lambda v: v["max"] - v["min"],
            unit="mV",
        ),
        DerivedChannelDefinition(
            id="Tower{tId}Module{mId}CellVoltageDifference",
            sources={"cells": "Tower{tId}Module{mId}Cell{cId}Voltage"},
            value_fn=lambda v: max(v["cells"]) - min(v["cells"]),
            unit="mV",
        ),
    ),
}


def combined_sensor_definitions(
    component_name: str,
) -> tuple[DerivedChannelDefinition, ...]:
    """Return the combined sensor definitions for the given component."""
    for component_regexp, definitions in COMBINED_SENSORS.items():
        if re.fullmatch(component_regexp, component_name):
            return definitions
    return ()


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

        entities: list[OpenEMSSensorEntity] = []
        channel: OpenEMSChannel
        channel_list: list[OpenEMSChannel] = component.sensors
        for channel in channel_list:
            if channel.options:
                device_class = SensorDeviceClass.ENUM
                state_class = None
                uom = None
            else:
                unit_desc: OpenEMSUnitClass = unit_description(channel.unit)
                device_class = unit_desc.sensor_device_class
                state_class = unit_desc.state_class
                uom = unit_desc.unit

            enable_by_default = is_channel_enabled(component.name, channel.name)
            entity_description = OpenEMSSensorDescription(
                key=channel.unique_id(),
                entity_registry_enabled_default=enable_by_default,
                # channels enabled by default have a translated name
                name=UNDEFINED if enable_by_default else channel.name,
                device_class=device_class,
                state_class=state_class,
                native_unit_of_measurement=uom,
                translation_key=translation_key(channel),
            )
            entities.append(
                OpenEMSSensorEntity(
                    channel,
                    entity_description,
                    device,
                )
            )

        derived_channel: OpenEMSDerivedChannel
        derived_channel_list: list[OpenEMSDerivedChannel] = (
            component.create_derived_channels(
                combined_sensor_definitions(component.name)
            )
        )
        for derived_channel in derived_channel_list:
            unit_desc: OpenEMSUnitClass = unit_description(derived_channel.unit)
            device_class = unit_desc.sensor_device_class
            state_class = unit_desc.state_class
            uom = unit_desc.unit

            enable_by_default = is_channel_enabled(component.name, derived_channel.name)
            entity_description = OpenEMSSensorDescription(
                key=derived_channel.unique_id(),
                entity_registry_enabled_default=enable_by_default,
                # channels enabled by default have a translated name
                name=UNDEFINED if enable_by_default else derived_channel.name,
                device_class=device_class,
                state_class=state_class,
                native_unit_of_measurement=uom,
                translation_key=translation_key(derived_channel),
            )
            entities.append(
                OpenEMSSensorEntity(
                    derived_channel,
                    entity_description,
                    device,
                )
            )
        async_add_entities(entities)

    ############ END MARKER _create_sensor_entities ##############

    backend: OpenEMSBackend = entry.runtime_data.backend
    device_registry = dr.async_get(hass)
    # Create the edge device
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        name=backend.the_edge.hostname,
        identifiers={(DOMAIN, backend.the_edge.hostname)},
    )
    component: OpenEMSComponent
    for component in backend.the_edge.components.values():
        if component.create_entities:
            _create_sensor_entities(component)


@dataclass(frozen=True, kw_only=True)
class OpenEMSSensorDescription(SensorEntityDescription):
    """Definition of OpenEMS sensor attributes."""

    has_entity_name = True


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
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self.previous_increasing_value_not_null: float | None = None

    @property
    def native_value(self) -> str | float | int | None:
        """Return the value of the sensor."""
        val = self._channel.native_value
        if val is None:
            return None
        if isinstance(val, str):
            # self.entity_description.device_class == SensorDeviceClass.ENUM
            return to_snake_case(val)

        if self.entity_description.state_class == SensorStateClass.TOTAL_INCREASING:
            if (
                self._channel.component.edge.advanced_options[
                    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING
                ]
                and val is not None
                and self.previous_increasing_value_not_null is not None
                and self.previous_increasing_value_not_null > val
            ):
                _LOGGER.debug(
                    "Ignoring new backend value %s for %s since it is smaller than the previous value %.1f and state class is total_increasing",
                    val,
                    self.entity_id,
                    self.previous_increasing_value_not_null,
                )
                return self.previous_increasing_value_not_null
            # else:
            self.previous_increasing_value_not_null = val

        return val

    async def update_value(self, **kwargs: Any) -> None:
        """Service callback to change value via REST call."""
        if isinstance(self._channel, OpenEMSChannel):
            await self._channel.update_value(
                float(kwargs[ATTR_VALUE]),
                kwargs.get(ATTR_UPDATE_CYCLE),  # pyright: ignore[reportArgumentType]
                kwargs.get(ATTR_TIMEOUT),  # pyright: ignore[reportArgumentType]
            )

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
