"""Component providing support for OpenEMS sensors."""

from dataclasses import dataclass
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .const import DEFAULT_EDGE_CHANNELS
from .helpers import (
    OpenEMSSensorUnitClass,
    component_device,
    edge_device,
    unit_description,
)
from .openems import OpenEMSBackend, OpenEMSChannel, OpenEMSComponent, OpenEMSEdge

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenEMS sensor entities."""
    backend: OpenEMSBackend = config_entry.runtime_data
    entities: list[OpenEMSSensorEntity] = []
    # for all edges
    edge: OpenEMSEdge
    for edge in backend.edges.values():
        component: OpenEMSComponent
        for component in edge.components.values():
            device = component_device(component)
            component_entities = create_sensor_entities(component, device)
            entities.extend(component_entities)

        device = edge_device(edge)
        edge_entities = create_sensor_entities(edge.edge_component, device)
        entities.extend(edge_entities)

    async_add_entities(entities)


@dataclass(frozen=True, kw_only=True)
class OpenEMSSensorDescription(SensorEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSSensorEntity(SensorEntity):
    """Representation of a sensor."""

    def __init__(
        self,
        channel: OpenEMSChannel,
        entity_description,
        device_info,
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


def create_sensor_entities(
    component: OpenEMSComponent,
    device_info: DeviceInfo,
) -> list[OpenEMSSensorEntity]:
    """Create Sensor Entities from channel list."""
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

        enable_by_default = component.name + "/" + channel.name in DEFAULT_EDGE_CHANNELS
        entity_description = OpenEMSSensorDescription(
            key=channel.unique_id(),
            entity_registry_enabled_default=enable_by_default,
            name=channel.name,
            device_class=device_class,
            state_class=state_class,
            native_unit_of_measurement=uom,
        )
        entities.append(
            OpenEMSSensorEntity(
                channel,
                entity_description,
                device_info,
            )
        )
    return entities
