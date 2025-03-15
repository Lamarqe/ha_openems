"""Component providing support for OpenEMS select entities."""

from dataclasses import dataclass
import logging

from homeassistant.components.select import SelectEntity, SelectEntityDescription
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
class OpenEMSSelectDescription(SelectEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSSelectEntity(SelectEntity):
    """Select entity class for OpenEMS channels."""

    entity_description: OpenEMSSelectDescription

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
        """Initialize OpenEMS select entity."""
        self.entity_description = entity_description
        self._attr_options = entity_description.get_options  # Todo

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self.entity_description.value

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.entity_description.method(self._host.api, self._channel, option)
        self.async_write_ha_state()
