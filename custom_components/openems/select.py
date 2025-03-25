"""Component providing support for OpenEMS select entities."""

from dataclasses import dataclass
import logging

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .const import DEFAULT_EDGE_CHANNELS
from .helpers import component_device, edge_device
from .openems import OpenEMSBackend, OpenEMSComponent, OpenEMSEdge, OpenEMSEnumProperty

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS select entities."""
    backend: OpenEMSBackend = config_entry.runtime_data
    entities: list[OpenEMSSelectEntity] = []
    # for all edges
    edge: OpenEMSEdge
    for edge in backend.edges.values():
        component: OpenEMSComponent
        for component in edge.components.values():
            device = component_device(component)
            component_entities = create_select_entities(component, device)
            entities.extend(component_entities)

        device = edge_device(edge)
        edge_entities = create_select_entities(edge.edge_component, device)
        entities.extend(edge_entities)

    async_add_entities(entities)


@dataclass(frozen=True, kw_only=True)
class OpenEMSSelectDescription(SelectEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSSelectEntity(SelectEntity):
    """Select entity class for OpenEMS channels."""

    entity_description: OpenEMSSelectDescription

    def __init__(
        self,
        channel: OpenEMSEnumProperty,
        entity_description,
        device_info,
    ) -> None:
        """Initialize OpenEMS switch entity."""
        self._channel: OpenEMSEnumProperty = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_extra_state_attributes = channel.orig_json
        self._raw_value = None

    def handle_currentData(self, _, value) -> None:
        """Handle a state update."""
        if self._raw_value != value:
            previous_option = self.current_option
            self._raw_value = value
            if previous_option != self.current_option:
                self.async_schedule_update_ha_state()

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        if (
            isinstance(self._raw_value, str)
            and self._raw_value in self.entity_description.options
        ):
            return self._raw_value
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self._channel.update_value(option)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        self._channel.register_callback(
            self.handle_currentData,
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Entity removed."""
        self._channel.unregister_callback()
        await super().async_will_remove_from_hass()


def create_select_entities(
    component: OpenEMSComponent,
    device_info: DeviceInfo,
) -> list[OpenEMSSelectEntity]:
    """Create Sensor Entities from channel list."""
    entities: list[OpenEMSSelectEntity] = []
    channel: OpenEMSEnumProperty
    channel_list: list[OpenEMSEnumProperty] = component.enum_properties
    for channel in channel_list:
        entity_enabled = component.name + "/" + channel.name in DEFAULT_EDGE_CHANNELS
        entity_description = OpenEMSSelectDescription(
            key=channel.unique_id(),
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=entity_enabled,
            options=channel.options,
            # remove "_Property" prefix
            name=channel.name[9:],
        )
        entities.append(
            OpenEMSSelectEntity(
                channel,
                entity_description,
                device_info,
            )
        )
    return entities
