"""Diagnostics for the HA OpenEMS integration."""

from typing import Any

from openems_client.openems import OpenEMSDataHandler

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.components.update import (
    ATTR_INSTALLED_VERSION,
    DOMAIN as UPDATE_DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .helpers_ha import OpenEMSConfigEntry, component_store

TO_REDACT = [
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OpenEMSConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for OpenEMS config entry."""
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    update_entity = next(
        (entity for entity in entities if entity.domain == UPDATE_DOMAIN), None
    )
    entity_ids = {entity.unique_id: entity.entity_id for entity in entities}
    edge = entry.runtime_data.backend.the_edge
    update_state = hass.states.get(update_entity.entity_id) if update_entity else None

    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "options": dict(entry.options),
        "components": async_redact_data(
            await component_store(hass, entry.entry_id).async_load(), TO_REDACT
        ),
        "fems_version": (
            update_state.attributes.get(ATTR_INSTALLED_VERSION)
            if update_state
            else "Unknown"
        ),
        "registered_channels": {
            address: _entity_ids(handlers, entity_ids)
            for address, handlers in edge.registered_channels.items()
        },
        "channel_data": edge.current_channel_data,
    }


def _entity_ids(
    handlers: set[OpenEMSDataHandler], entity_ids: dict[str, str]
) -> str | list[str] | None:
    """Return the entity ID of a channel, a list if several entities use it."""
    found = sorted(
        entity_ids[handler.unique_id]
        for handler in handlers
        if handler.unique_id in entity_ids
    )
    if len(found) > 1:
        return found
    return found[0] if found else None
