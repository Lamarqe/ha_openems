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
from .helpers import device_to_state_class, unit_to_deviceclass
from .openems import OpenEMSBackend, OpenEMSEdge


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up an OpenEMS Backend."""
    entities: list = []
    backend: OpenEMSBackend = config_entry.runtime_data
    # for all edges
    for edge in backend.edges.values():
        edge_device_name = edge.config["_host"]["Hostname"]
        if backend.multi_edge:
            edge_device_name += " " + edge.id_str
        edge_device = DeviceInfo(
            name=edge_device_name,
            identifiers={(DOMAIN, edge_device_name)},
        )
        # for all components
        for component_str in edge.config:
            if component_str.startswith(("_", "ctrl")):
                if component_str != "_sum":
                    continue
            alias = edge.config[component_str]["_PropertyAlias"]
            if alias:
                # If the component has a property alias,
                # create the entities within a service which linked to the edge device
                device_info = DeviceInfo(
                    name=edge_device_name + " " + component_str,
                    model=alias,
                    identifiers={(DOMAIN, alias)},
                    via_device=(
                        DOMAIN,
                        edge_device_name,
                    ),
                    entry_type=DeviceEntryType.SERVICE,
                )
            elif component_str == "_sum":
                # all entities of the _sum component are created within the edge device
                device_info = edge_device
            else:
                # dont create entities for components which dont define their property alias (assumed internal in openems)
                continue

            # for all channels
            for channel in edge.config[component_str]["channels"]:
                # add an entity
                channel_address = channel["id"]
                entity_enabled = (
                    component_str + "/" + channel_address
                    in OpenEMSEdge.DEFAULT_CHANNELS
                )
                unique_id = (
                    edge.config["_host"]["Hostname"]
                    + "/"
                    + edge.id_str
                    + "/"
                    + component_str
                    + "/"
                    + channel_address
                )
                if (
                    "category" in channel
                    and channel["category"] == "ENUM"
                    and "options" in channel
                ):
                    device_class = SensorDeviceClass.ENUM
                    enum_dict = {v: k for k, v in channel["options"].items()}
                    del channel["options"]
                else:
                    device_class = unit_to_deviceclass(channel["unit"])
                    enum_dict = {}
                entity_description = OpenEMSEntityDescription(
                    key=unique_id,
                    entity_registry_enabled_default=entity_enabled,
                    name=channel_address,
                    device_class=device_class,
                    state_class=device_to_state_class(device_class),
                    native_unit_of_measurement=(
                        None
                        if device_class == SensorDeviceClass.ENUM
                        else channel["unit"]
                    ),
                )

                entities.append(
                    OpenEMSSensorEntity(
                        entity_description,
                        unique_id,
                        device_info,
                        edge,
                        component_str,
                        channel,
                        enum_dict,
                    )
                )

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
