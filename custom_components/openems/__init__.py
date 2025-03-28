"""The HA OpenEMS integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .openems import OpenEMSBackend

_PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
]

type OpenEMSConfigEntry = ConfigEntry[OpenEMSBackend]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
) -> bool:
    """Set up HA OpenEMS from a config entry."""

    # 1. Create API instance
    config = config_entry.data
    backend = OpenEMSBackend(
        config[CONF_HOST], config[CONF_USERNAME], config[CONF_PASSWORD]
    )
    # 2. Trigger the API connection (and authentication)
    backend.start()
    await backend.wait_for_login()

    store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    if config_data := await store.async_load():
        backend.set_config(config_data)
    else:
        config_data = await backend.read_config()
        await store.async_save(config_data)
    await backend.prepare_entities()
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
