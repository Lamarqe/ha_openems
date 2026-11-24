"""Diagnostics for the HA OpenEMS integration."""

import re
from typing import Any

from openems_client.openems import OpenEMSDataHandler

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .helpers_ha import OpenEMSConfigEntry, component_store

TO_REDACT = [
    CONF_HOST,
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
]

# keys of the edge configuration and channel data, compared in lower case
_KEYS_TO_REDACT = {
    "_lastchangeby",
    "address",
    "addresses",
    "apikey",
    "hostname",
    "ip",
    "keyforfreeapps",
    "latitude",
    "longitude",
    "networkconfiguration",
    "placename",
    "postcode",
    "proxyaddress",
    "subnetmask",
}
# keys and channel addresses containing these words, eg "Tower0Module0SerialNumber"
_KEY_PATTERN = re.compile(r"serialnumber|password|apikey|token|secret", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OpenEMSConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for OpenEMS config entry."""
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    entity_ids = {entity.unique_id: entity.entity_id for entity in entities}
    edge = entry.runtime_data.backend.the_edge
    components: dict[str, Any] = (
        await component_store(hass, entry.entry_id).async_load() or {}
    )
    # the hostname is the FEMS number, which identifies the customer
    hostname: str = components.get("_host", {}).get("Hostname", "")

    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "options": dict(entry.options),
        "components": _redact(components, hostname),
        "edge_version": edge.version,
        "registered_channels": _redact(
            {
                address: _entity_ids(handlers, entity_ids)
                for address, handlers in edge.registered_channels.items()
            },
            hostname,
        ),
        "channel_data": _redact(edge.current_channel_data, hostname),
    }


def _redact(value: Any, hostname: str) -> Any:
    """Redact personal data and secrets at any depth."""
    if isinstance(value, dict):
        return {
            key: REDACTED
            if key.lower() in _KEYS_TO_REDACT or _KEY_PATTERN.search(key)
            else _redact(item, hostname)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, hostname) for item in value]
    if isinstance(value, str):
        if hostname:
            # entity IDs contain the slugified hostname
            value = value.replace(hostname, REDACTED).replace(
                slugify(hostname), REDACTED
            )
        return _EMAIL.sub(REDACTED, value)
    return value


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
