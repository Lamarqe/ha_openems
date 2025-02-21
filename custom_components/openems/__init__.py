"""The HA OpenEMS integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .openems import OpenEMSBackend

DOMAIN = "openems"

_PLATFORMS: list[Platform] = [Platform.SENSOR]

type OpenEMSConfigEntry = ConfigEntry[OpenEMSBackend]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
) -> bool:
    """Set up HA OpenEMS from a config entry."""

    # 1. Create API instance

    backend = OpenEMSBackend(hass, config_entry.data)
    # 2. Validate the API connection (and authentication)
    await backend.do_login()
    await backend.read_edge_config()

    config_entry.runtime_data = backend
    await hass.config_entries.async_forward_entry_setups(config_entry, _PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> bool:
    """Unload a config entry."""
    backend: OpenEMSBackend = config_entry.runtime_data
    await backend.stop()
    return await hass.config_entries.async_unload_platforms(config_entry, _PLATFORMS)


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> bool:
    """Migrate old entry."""
    return True
