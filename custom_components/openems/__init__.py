"""The HA OpenEMS integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import ClassVar

from jsonrpc_base.jsonrpc import ProtocolError, TransportError
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.entity_registry import async_migrate_entries
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import CONF_EDGE, DOMAIN
from .helpers import component_device, find_channel_in_backend
from .openems import OpenEMSBackend

_LOGGER = logging.getLogger(__name__)

_PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.TIME,
]

type OpenEMSConfigEntry = ConfigEntry[RuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up services."""

    async def service_update_component_config(service_call: ServiceCall) -> None:
        entity_reg = er.async_get(hass)
        entity_id = service_call.data.get(ATTR_ENTITY_ID)
        value = service_call.data.get("value")
        entry: er.RegistryEntry = entity_reg.async_get(entity_id)
        if entry.platform != DOMAIN:
            _LOGGER.error(
                "Update_component_config was called for entity %s. Must be called with openems entity, not %s",
                entity_id,
                entry.platform,
            )
            return
        config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
        backend: OpenEMSBackend = config_entry.runtime_data.backend
        channel = find_channel_in_backend(backend, entry.unique_id)
        try:
            await channel.update_value(value)
        except AttributeError:
            _LOGGER.error(
                "Entity %s is not a property and cannot be updated",
                entity_id,
            )

    hass.services.async_register(
        DOMAIN,
        "update_component_config",
        service_update_component_config,
        schema=vol.Schema(
            {vol.Required(ATTR_ENTITY_ID): cv.entity_id, vol.Required("value"): object}
        ),
    )
    return True


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
    backend = OpenEMSBackend(
        config_entry.data["user_input"][CONF_HOST],
        config_entry.data["user_input"][CONF_USERNAME],
        config_entry.data["user_input"][CONF_PASSWORD],
    )
    edge_id = config_entry.data["user_input"][CONF_EDGE]
    # 2. Trigger the API connection (and authentication)
    try:
        await asyncio.wait_for(backend.connect_to_server(), timeout=2)
    except (TransportError, TimeoutError) as ex:
        raise ConfigEntryNotReady(f"Timeout while connecting to {backend.host}") from ex
    # login
    try:
        await backend.login_to_server()
    except ProtocolError as ex:
        raise ConfigEntryAuthFailed(f"Wrong user / password for {backend.host}") from ex

    # 3. Reload component list in case explicit user request to reload (hass.is_running)
    if hass.is_running or not config_entry.data["components"]:
        components = await backend.read_edge_components(edge_id)
        entry_data = {
            "user_input": config_entry.data["user_input"],
            "components": components,
        }
        hass.config_entries.async_update_entry(entry=config_entry, data=entry_data)
    else:
        backend.set_component_config(edge_id, config_entry.data["components"])

    await backend.prepare_entities()

    # 4. Read and set config options
    for component_name, is_enabled in config_entry.options.items():
        if component := backend.the_edge.components.get(component_name):
            component.create_entities = is_enabled

    config_entry.runtime_data = RuntimeData(backend=backend)
    config_entry.async_on_unload(config_entry.add_update_listener(update_config))

    await hass.config_entries.async_forward_entry_setups(config_entry, _PLATFORMS)
    backend.start()
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
    if config_entry.version == 1:
        store_conf: Store = Store(hass, 1, "openems_config")
        if config_data := await store_conf.async_load():
            # migrate config into entry
            edge_id = next(iter(config_data))
            new_data = {
                "user_input": config_entry.data.copy(),
                "components": config_data[edge_id]["components"],
            }
            new_data["user_input"][CONF_EDGE] = edge_id
            # delete config store data
            await store_conf.async_remove()

            host = config_entry.data[CONF_HOST]
            options_key = "openems_options_" + host
            store_options: Store = Store(hass, 1, options_key)
            # delete options store data
            await store_options.async_remove()

            hass.config_entries.async_update_entry(
                config_entry, data=new_data, version=2, minor_version=1
            )

            # update entities unique ids
            @callback
            def update_unique_id(entity_entry):
                """Update unique ID of entity entry."""
                unique_id_parts = entity_entry.unique_id.split("/")
                if len(unique_id_parts) != 4:
                    return None
                unique_id_parts[1] = unique_id_parts[1].removeprefix("edge")
                return {"new_unique_id": "/".join(unique_id_parts)}

            await async_migrate_entries(hass, config_entry.entry_id, update_unique_id)
            _LOGGER.debug(
                "Migration to configuration version %s.%s successful",
                config_entry.version,
                config_entry.minor_version,
            )
            return True
        return False

    return False


async def update_config(hass: HomeAssistant, entry: OpenEMSConfigEntry) -> None:
    """Handle options update."""

    if not entry.options:
        return

    backend: OpenEMSBackend = entry.runtime_data.backend
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    # for all edges
    for comp_name, component in backend.the_edge.components.items():
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
