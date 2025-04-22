"""Component providing support for OpenEMS sensors."""

from dataclasses import dataclass
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .const import DOMAIN
from .helpers import (
    OpenEMSSensorUnitClass,
    component_device,
    translation_key,
    unit_description,
)
from .openems import (
    CONFIG,
    OpenEMSBackend,
    OpenEMSChannel,
    OpenEMSComponent,
    OpenEMSEdge,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenEMS sensor entities."""

    def _create_sensor_entities(component: OpenEMSComponent) -> None:
        """Create Sensor Entities from channel list."""
        device = component_device(component)
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
                unit_desc: OpenEMSSensorUnitClass = unit_description(channel.unit)
                device_class = unit_desc.device_class
                state_class = unit_desc.state_class
                uom = unit_desc.unit

            enable_by_default = CONFIG.is_channel_enabled(component.name, channel.name)
            entity_description = OpenEMSSensorDescription(
                key=channel.unique_id(),
                entity_registry_enabled_default=enable_by_default,
                name=channel.name,
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
        async_add_entities(entities)

    ############ END MARKER _create_sensor_entities ##############

    backend: OpenEMSBackend = entry.runtime_data.backend
    device_registry = dr.async_get(hass)
    # for all edges
    edge: OpenEMSEdge
    for edge in backend.edges.values():
        # Create the edge device
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            name=edge.hostname,
            identifiers={(DOMAIN, edge.hostname)},
        )
        component: OpenEMSComponent
        for component in edge.components.values():
            if component.create_entities:
                _create_sensor_entities(component)

    # prepare callback for creating in new entities during options config flow
    entry.runtime_data.add_component_callbacks[Platform.SENSOR.value] = (
        _create_sensor_entities
    )


@dataclass(frozen=True, kw_only=True)
class OpenEMSSensorDescription(SensorEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSSensorEntity(SensorEntity):
    """Representation of a sensor."""

    entity_description: OpenEMSSensorDescription

    def __init__(
        self,
        channel: OpenEMSChannel,
        entity_description,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        self._channel: OpenEMSChannel = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_extra_state_attributes = channel.orig_json
        self._state: int = None

    def handle_current_value(self, value) -> None:
        """Handle a state update."""
        if value in self._channel.options:
            value = self._channel.options[value]

        if self._state != value:
            self._state = value
            self.async_schedule_update_ha_state()

    @property
    def native_value(self) -> int | None:
        """Return the value of the sensor."""
        return self._state

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
