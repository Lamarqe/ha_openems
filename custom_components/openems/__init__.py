"""The HA OpenEMS integration."""

from __future__ import annotations

import asyncio
import copy
import logging

from jsonrpc_base.jsonrpc import ProtocolError, TransportError

from homeassistant.const import CONF_HOST, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_registry import async_migrate_entries
from homeassistant.helpers.storage import Store

from .const import CONF_EDGE, CONN_TYPE_DIRECT_EDGE
from .entry_data import OpenEMSConfigReader, OpenEMSWebSocketConnection
from .helpers import (
    OpenEMSConfigEntry,
    OpenEMSEntityFeature,  # noqa: F401
    RuntimeData,
    component_device,
)
from .openems import CONFIG, OpenEMSBackend

_LOGGER = logging.getLogger(__name__)

_PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.TIME,
    Platform.UPDATE,
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
) -> bool:
    """Set up HA OpenEMS from a config entry."""
    # 0. Copy config data because we will hand over ownership to backend
    data_copy = copy.deepcopy(
        config_entry.data.copy()  # copy() before deepcopy because its a mappingproxy
    )

    try:
        # 1. Create connection instance
        connection = OpenEMSWebSocketConnection(data_copy["user_input"])

        edge_id = data_copy["user_input"][CONF_EDGE]
        # 2. Trigger the API connection (and authentication)
        await asyncio.wait_for(connection.connect_to_server(), timeout=2)

    except (TransportError, TimeoutError) as ex:
        await connection.stop()
        raise ConfigEntryNotReady(
            f"Error while connecting to {connection.conn_url.host}"
        ) from ex
    # login
    try:
        login_response: dict = await connection.login_to_server()
    except ProtocolError as ex:
        await connection.stop()
        raise ConfigEntryAuthFailed(
            f"Wrong user / password for {connection.conn_url.host}"
        ) from ex

    # 3. Reload component list in case explicit user request to reload (hass.is_running)
    if hass.is_running or not data_copy["components"]:
        config_reader = OpenEMSConfigReader(connection, edge_id)

        components = await config_reader.read_edge_components()
        entry_data = {
            "user_input": data_copy["user_input"],
            "components": components,
        }
        hass.config_entries.async_update_entry(
            entry=config_entry,
            data=copy.deepcopy(entry_data),  # copy data because backend has ownership
        )
    else:
        components = data_copy["components"]

    # 4. Prepare HA entity strucutures from config entry data
    multi_edge = OpenEMSConfigReader.parse_login_response(login_response)
    backend = OpenEMSBackend(connection, edge_id, multi_edge, components)

    # 5. Read and set config options
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
            components = config_data[edge_id]["components"]
            new_data = {
                "user_input": config_entry.data.copy(),
                "components": components,
            }
            new_data["user_input"][CONF_EDGE] = edge_id
            new_data["user_input"][CONF_TYPE] = CONN_TYPE_DIRECT_EDGE
            # delete config store data
            await store_conf.async_remove()

            host = config_entry.data[CONF_HOST]
            options_key = "openems_options_" + host
            store_options: Store = Store[dict[str, bool]](hass, 1, options_key)
            options: dict[str, bool] | None = await store_options.async_load()
            if options:
                # delete options store data
                await store_options.async_remove()
            else:
                # initialize options with default settings
                options = {}
                for component in components:
                    options[component] = CONFIG.is_component_enabled(component)

            hass.config_entries.async_update_entry(
                config_entry, data=new_data, options=options, version=2, minor_version=1
            )

            # update entities unique ids
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

    if config_entry.version == 2:
        # update device identifiers, as they got corrected with v1.1.0.
        device_registry = dr.async_get(hass)
        for device in device_registry.devices.copy().values():
            if (
                config_entry.entry_id in device.config_entries
                and device.identifiers
                and len(identifiers := device.identifiers.pop()) == 3
            ):
                # 3-tuples (DOMAIN, edge_hostname, component_name) are illegal
                # and changed to 2-tuples (DOMAIN, edge_hostname + " " + component_name)
                device_registry.async_update_device(
                    device_id=device.id,
                    new_identifiers={
                        (identifiers[0], identifiers[1] + " " + identifiers[2])
                    },
                )
        hass.config_entries.async_update_entry(config_entry, version=3, minor_version=1)
        _LOGGER.debug(
            "Migration to configuration version %s.%s successful",
            config_entry.version,
            config_entry.minor_version,
        )

        return True
    return False


async def update_config(hass: HomeAssistant, entry: OpenEMSConfigEntry) -> None:
    """Handle options update."""

    if not entry.options:
        return

    backend: OpenEMSBackend = entry.runtime_data.backend
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    for comp_name, component in backend.the_edge.components.items():
        if not entry.options.get(comp_name) and component.create_entities:
            # remove entities
            comp_device: DeviceInfo = component_device(component)
            device = device_registry.async_get_device(comp_device.get("identifiers"))
            if not device:
                continue

            entities = er.async_entries_for_device(entity_registry, device.id, True)
            for entity in entities:
                entity_registry.async_remove(entity.entity_id)
            # remove device
            device_registry.async_remove_device(device.id)

        # process newly enabled components
        if entry.options.get(comp_name) and not component.create_entities:
            for callback in entry.runtime_data.add_component_callbacks.values():
                callback(component)

        component.create_entities = bool(entry.options.get(comp_name))
