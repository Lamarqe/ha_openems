"""Diagnostics for the HA OpenEMS integration."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.components.update import (
    ATTR_INSTALLED_VERSION,
    DOMAIN as UPDATE_DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .helpers_ha import OpenEMSConfigEntry

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
    update_entity = next(
        (
            entity
            for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
            if entity.domain == UPDATE_DOMAIN
        ),
        None,
    )
    update_state = hass.states.get(update_entity.entity_id) if update_entity else None

    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "options": dict(entry.options),
        "fems_version": (
            update_state.attributes.get(ATTR_INSTALLED_VERSION)
            if update_state
            else "Unknown"
        ),
        "registered_channels": list(
            entry.runtime_data.backend.the_edge.registered_channels.keys()
        ),
        "channel_data": entry.runtime_data.backend.the_edge.current_channel_data,
    }
