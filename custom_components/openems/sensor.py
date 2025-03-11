"""Component providing support for OpenEMS sensors."""

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .__init__ import DOMAIN, OpenEMSConfigEntry
from .const import DEFAULT_EDGE_CHANNELS
from .helpers import OpenEMSSensorUnitClass, unit_description
from .openems import OpenEMSBackend, OpenEMSChannel, OpenEMSComponent, OpenEMSEdge


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up an OpenEMS Backend."""
    backend: OpenEMSBackend = config_entry.runtime_data
    entities: list[OpenEMSSensorEntity] = []
    # for all edges
    for edge in backend.edges.values():
        component: OpenEMSComponent
        for component_name, component in edge.components.items():
            device = DeviceInfo(
                name=edge.hostname + " " + component_name,
                model=component.alias,
                identifiers={(DOMAIN, component.alias)},
                via_device=(
                    DOMAIN,
                    edge.hostname,
                ),
                entry_type=DeviceEntryType.SERVICE,
            )
            component_entities = create_sensor_entities(component, device)
            entities.extend(component_entities)

        edge_device = DeviceInfo(
            name=edge.hostname,
            identifiers={(DOMAIN, edge.hostname)},
        )
        edge_entities = create_sensor_entities(edge.edge_component, edge_device)
        entities.extend(edge_entities)

    async_add_entities(entities)


@dataclass(frozen=True, kw_only=True)
class OpenEMSEntityDescription(SensorEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSSensorEntity(SensorEntity):
    """Representation of a sensor."""

    def __init__(
        self,
        entity_description,
        unique_id,
        device_info,
        edge: OpenEMSEdge,
        component_name,
        extra_attributes,
        state_map,
    ) -> None:
        """Initialize the sensor."""
        self._state: int = None
        self._state_map = state_map
        self.entity_description = entity_description
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._edge: OpenEMSEdge = edge
        self._component_name = component_name
        self._attr_extra_state_attributes = extra_attributes

    def handle_currentData(self, _, value) -> None:
        """Handle a state update."""
        if value in self._state_map:
            value = self._state_map[value]

        if self._state != value:
            self._state = value
            self.async_schedule_update_ha_state()

    @property
    def native_value(self) -> int | None:
        """Return the value of the sensor."""
        return self._state

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        self._edge.register_callback(
            self._component_name + "/" + self.name, self.handle_currentData
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Entity removed."""
        self._edge.unregister_callback(self._component_name + "/" + self.name)
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

        entity_enabled = component.name + "/" + channel.name in DEFAULT_EDGE_CHANNELS
        entity_description = OpenEMSEntityDescription(
            key=channel.unique_id(),
            entity_registry_enabled_default=entity_enabled,
            name=channel.name,
            device_class=device_class,
            state_class=state_class,
            native_unit_of_measurement=uom,
        )
        entities.append(
            OpenEMSSensorEntity(
                entity_description,
                channel.unique_id(),
                device_info,
                channel.component.edge,
                component.name,
                channel.orig_json,
                channel.options,
            )
        )
    return entities
