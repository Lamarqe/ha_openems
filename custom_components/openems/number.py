"""Component providing support for OpenEMS number entities."""

from dataclasses import dataclass
import logging

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .const import DEFAULT_EDGE_CHANNELS
from .helpers import (
    OpenEMSSensorUnitClass,
    component_device,
    edge_device,
    unit_description,
)
from .openems import (
    OpenEMSBackend,
    OpenEMSComponent,
    OpenEMSEdge,
    OpenEMSNumberProperty,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS number entities."""
    backend: OpenEMSBackend = config_entry.runtime_data
    entities: list[OpenEMSNumberEntity] = []
    # for all edges
    edge: OpenEMSEdge
    for edge in backend.edges.values():
        component: OpenEMSComponent
        for component in edge.components.values():
            device = component_device(component)
            component_entities = create_number_entities(component, device)
            entities.extend(component_entities)

        device = edge_device(edge)
        edge_entities = create_number_entities(edge.edge_component, device)
        entities.extend(edge_entities)

    async_add_entities(entities)


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
        device_info,
    ) -> None:
        """Initialize OpenEMS number entity."""
        self._channel: OpenEMSNumberProperty = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._value = None

    def handle_currentData(self, _, new_value) -> None:
        """Handle a number update."""
        if self._value != new_value:
            self._value = new_value
            self.async_schedule_update_ha_state()

    @property
    def native_value(self) -> int | None:
        """Return the value of the number entity."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Change the current value."""
        await self._channel.update_value(value)
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


def create_number_entities(
    component: OpenEMSComponent,
    device_info: DeviceInfo,
) -> list[OpenEMSNumberEntity]:
    """Create Number Entities from channel list."""
    entities: list[OpenEMSNumberEntity] = []
    channel: OpenEMSNumberProperty
    channel_list: list[OpenEMSNumberProperty] = component.number_properties
    for channel in channel_list:
        entity_enabled = component.name + "/" + channel.name in DEFAULT_EDGE_CHANNELS
        unit_desc: OpenEMSSensorUnitClass = unit_description(channel.unit)
        entity_description = OpenEMSNumberDescription(
            key=channel.unique_id(),
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=entity_enabled,
            mode=NumberMode.SLIDER,
            native_min_value=channel.lower_limit,
            native_max_value=channel.upper_limit,
            native_step=channel.step,
            # remove "_Property" prefix
            name=channel.name[9:],
            device_class=unit_desc.device_class,
            # state_class=unit_desc.state_class,
            native_unit_of_measurement=unit_desc.unit,
        )
        entities.append(
            OpenEMSNumberEntity(
                channel,
                entity_description,
                device_info,
            )
        )
    return entities
