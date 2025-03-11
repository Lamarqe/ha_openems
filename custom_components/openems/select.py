"""Component providing support for OpenEMS select entities."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .openems import OpenEMSBackend


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
