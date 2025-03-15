"""Component providing support for OpenEMS number entities."""

from dataclasses import dataclass
import logging

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .openems import OpenEMSBackend, OpenEMSEdge

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS select entities."""
    entities: list = []
    backend: OpenEMSBackend = config_entry.runtime_data
    # for all edges
    for edge in backend.edges.values():
        pass


@dataclass(frozen=True, kw_only=True)
class OpenEMSNumberDescription(NumberEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSNumberEntity(NumberEntity):
    """Number entity class for OpenEMS channels."""

    entity_description: OpenEMSNumberDescription

    # From here: ToDo

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
        """Initialize OpenEMS number entity."""
        self.entity_description = entity_description
        self._attr_mode = NumberMode.SLIDER  # Todo

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self.entity_description.value

    async def async_set_native_value(self, value: float) -> None:
        """Change the current value."""
        await self.entity_description.method(self._host.api, self._channel, value)
        self.async_write_ha_state()
