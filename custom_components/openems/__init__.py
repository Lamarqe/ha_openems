"""The HA OpenEMS integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_BACKEND_CONFIG, STORAGE_KEY_HA_OPTIONS, STORAGE_VERSION
from .helpers import component_device
from .openems import OpenEMSBackend, OpenEMSEdge

_PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.TIME,
]

type OpenEMSConfigEntry = ConfigEntry[RuntimeData]


@dataclass
class RuntimeData:
    """Data class to store all relevant runtime data."""

    backend: OpenEMSBackend
    add_component_callbacks: ClassVar[dict[str:Callable]] = {}


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

    # 3. Load config
    store_conf: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY_BACKEND_CONFIG)
    if config_data := await store_conf.async_load():
        backend.set_config(config_data)
    else:
        # this should only happen when the config got deleted explicitly
        config_data = await backend.read_config()
        await store_conf.async_save(config_data)

    await backend.prepare_entities()

    # 4. Read and set config options
    options_key = STORAGE_KEY_HA_OPTIONS + "_" + backend.host
    store_options: Store = Store(hass, STORAGE_VERSION, options_key)
    if options := await store_options.async_load():
        edge: OpenEMSEdge
        for edge in backend.edges.values():
            for component_name, is_enabled in options.items():
                if component := edge.components.get(component_name):
                    component.create_entities = is_enabled

    config_entry.runtime_data = RuntimeData(backend=backend)
    config_entry.async_on_unload(config_entry.add_update_listener(update_config))

    await hass.config_entries.async_forward_entry_setups(config_entry, _PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> bool:
    """Unload a config entry."""
    backend: OpenEMSBackend = config_entry.runtime_data.backend
    await backend.stop()
    return await hass.config_entries.async_unload_platforms(config_entry, _PLATFORMS)


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> bool:
    """Migrate old entry."""
    return True


async def update_config(hass: HomeAssistant, entry: OpenEMSConfigEntry) -> None:
    """Handle options update."""

    if not entry.options:
        return

    backend: OpenEMSBackend = entry.runtime_data.backend
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    # for all edges
    edge: OpenEMSEdge
    for edge in backend.edges.values():
        for comp_name, component in edge.components.items():
            if not entry.options[comp_name] and component.create_entities:
                # remove entities
                comp_device = component_device(component)
                device = device_registry.async_get_device(comp_device["identifiers"])
                entities = er.async_entries_for_device(entity_registry, device.id, True)
                for entity in entities:
                    entity_registry.async_remove(entity.entity_id)
                # remove device
                device_registry.async_remove_device(device.id)

            # process newly enabled components
            if entry.options[comp_name] and not component.create_entities:
                for callback in entry.runtime_data.add_component_callbacks.values():
                    callback(component)

            component.create_entities = entry.options[comp_name]

    # store options to make them available after HA restart
    options_key = STORAGE_KEY_HA_OPTIONS + "_" + backend.host
    store_options: Store = Store(hass, STORAGE_VERSION, options_key)
    await store_options.async_save(entry.options.copy())
