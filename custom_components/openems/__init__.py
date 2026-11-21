"""The HA OpenEMS integration."""

import logging

from openems_client.connection import OpenEMSConfigReader, OpenEMSWebSocketConnection
from openems_client.const import CONN_TYPE_DIRECT_EDGE
from openems_client.exceptions import OpenEMSAuthError, OpenEMSError
from openems_client.openems import CONFIG, OpenEMSBackend, OpenEMSComponent

from homeassistant.const import CONF_HOST, CONF_TYPE, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_registry import async_migrate_entries
from homeassistant.helpers.event import async_track_device_registry_updated_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import const as c
from .helpers_ha import (
    DeviceRuntimeData,
    OpenEMSConfigEntry,
    RuntimeData,
    component_device,
    component_identifier,
    component_store,
    map_user_input,
)
from .services import async_setup_services

CONFIG_SCHEMA = cv.empty_config_schema(c.DOMAIN)

# options key of the component selection of configuration version 4
CONF_COMPONENTS = "components"

_LOGGER = logging.getLogger(__name__)

_PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
    Platform.UPDATE,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the OpenEMS integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
) -> None:
    """Set up HA OpenEMS from a config entry."""
    try:
        # 1. Create connection instance
        connection = OpenEMSWebSocketConnection(
            map_user_input(dict(config_entry.data)), async_get_clientsession(hass)
        )

        edge_id = config_entry.data[c.CONF_EDGE]
        # 2. Trigger the API connection (and authentication)
        await connection.connect_to_server()

    except OpenEMSError as ex:
        await connection.stop()
        raise ConfigEntryNotReady(
            translation_domain=c.DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": str(connection.conn_url.host)},
        ) from ex
    # login
    try:
        login_response: dict = await connection.login_to_server()
    except OpenEMSAuthError as ex:
        await connection.stop()
        raise ConfigEntryAuthFailed(
            translation_domain=c.DOMAIN,
            translation_key="invalid_auth",
            translation_placeholders={"host": str(connection.conn_url.host)},
        ) from ex
    except OpenEMSError as ex:
        await connection.stop()
        raise ConfigEntryNotReady(
            translation_domain=c.DOMAIN,
            translation_key="login_failed",
            translation_placeholders={"host": str(connection.conn_url.host)},
        ) from ex

    # 3. Load the cached edge configuration. Read it from the edge if there is none yet,
    # later refreshes are triggered by the refresh configuration button.
    store = component_store(hass, config_entry.entry_id)
    components = await store.async_load()
    if components is None:
        config_reader = OpenEMSConfigReader(connection, edge_id)
        try:
            components = await config_reader.read_edge_components()
        except OpenEMSError as ex:
            await connection.stop()
            raise ConfigEntryNotReady(
                translation_domain=c.DOMAIN,
                translation_key="cannot_read_configuration",
                translation_placeholders={"host": str(connection.conn_url.host)},
            ) from ex
        await store.async_save(components)

    # 4. Prepare HA entity structures from the edge configuration
    multi_edge = OpenEMSConfigReader.parse_login_response(login_response)
    backend = OpenEMSBackend(connection, edge_id, multi_edge, components)

    # 5. Read and set config options
    # ==================== TODO REMOVE_LATER_START ============================
    advanced = dict(config_entry.options.get(c.CONF_ADVANCED_OPTIONS, {}))
    if c.CONF_FORWARD_INTERVAL not in advanced:
        advanced[c.CONF_FORWARD_INTERVAL] = 0
        options = {**config_entry.options, c.CONF_ADVANCED_OPTIONS: advanced}
        hass.config_entries.async_update_entry(config_entry, options=options)
    # ==================== TODO REMOVE_LATER_END ============================
    backend.the_edge.set_forward_interval(
        config_entry.options[c.CONF_ADVANCED_OPTIONS][c.CONF_FORWARD_INTERVAL]
    )

    # 6. Create central edge device in HA device registry, so that all component devices can be linked to it
    edge_device_identifiers = {(c.DOMAIN, backend.the_edge.hostname)}
    device_registry = dr.async_get(hass)
    edge_device_info = dr.DeviceInfo(
        identifiers=edge_device_identifiers,
        name=backend.the_edge.hostname,
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    edge_device_entry: dr.DeviceEntry = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        **edge_device_info,
    )

    # 7. Create component devices. Components without default channels start disabled.
    # Entities are only created for components with an enabled device.
    component_device_ids: list[str] = []
    enabled_components: list[OpenEMSComponent] = []
    for component in backend.the_edge.components.values():
        is_new_device = (
            device_registry.async_get_device_by_identifier(
                component_identifier(component), config_entry.entry_id
            )
            is None
        )
        component_device_entry: dr.DeviceEntry = device_registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            **component_device(edge_device_entry, component),
        )
        # disabled_by must not be passed for existing devices, it would overwrite the user's choice
        if is_new_device and not component.has_primary_channels:
            component_device_entry = device_registry.async_update_device(
                component_device_entry.id,
                disabled_by=dr.DeviceEntryDisabler.INTEGRATION,
            )
        if not component_device_entry.disabled:
            enabled_components.append(component)
        component_device_ids.append(component_device_entry.id)

    # 8. Prepare runtime data for later access
    config_entry.runtime_data = RuntimeData(
        backend=backend,
        edge_device=DeviceRuntimeData(info=edge_device_info, entry=edge_device_entry),
        enabled_components=enabled_components,
    )

    # 9. Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(config_entry, _PLATFORMS)

    # 10. Reload to create or remove entities when a component device gets enabled or disabled
    @callback
    def _handle_component_device_update(
        event: Event[dr.EventDeviceRegistryUpdatedData],
    ) -> None:
        if event.data["action"] == "update" and "disabled_by" in event.data["changes"]:
            hass.config_entries.async_schedule_reload(config_entry.entry_id)

    config_entry.async_on_unload(
        async_track_device_registry_updated_event(
            hass, component_device_ids, _handle_component_device_update
        )
    )

    # 11. Start backend
    backend.start()


async def async_unload_entry(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> None:
    """Unload a config entry."""
    backend: OpenEMSBackend = config_entry.runtime_data.backend
    await backend.stop()
    await hass.config_entries.async_unload_platforms(config_entry, _PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> None:
    """Remove the cached edge configuration of a removed config entry."""
    await component_store(hass, config_entry.entry_id).async_remove()


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
            new_data["user_input"][c.CONF_EDGE] = edge_id
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
                    options[component] = CONFIG.has_primary_channels(component)

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
                device.config_entry_id == config_entry.entry_id
                and device.identifiers
                and len(identifiers := device.identifiers.pop()) == 3
            ):
                # 3-tuples (cn.DOMAIN, edge_hostname, component_name) are illegal
                # and changed to 2-tuples (cn.DOMAIN, edge_hostname + " " + component_name)
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

    if config_entry.version == 3:
        old_options = dict(config_entry.options)
        new_options = {
            CONF_COMPONENTS: old_options,
            c.CONF_ADVANCED_OPTIONS: {
                c.CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
                c.CONF_FORWARD_INTERVAL: 0,
            },
        }
        hass.config_entries.async_update_entry(
            config_entry, options=new_options, version=4, minor_version=1
        )
        _LOGGER.debug(
            "Migration to configuration version %s.%s successful",
            config_entry.version,
            config_entry.minor_version,
        )

    if config_entry.version == 4:
        # component selection moved from the options to the device registry
        options = dict(config_entry.options)
        components_options: dict[str, bool] = options.pop(CONF_COMPONENTS, {})
        device_registry = dr.async_get(hass)
        edge_device = next(
            (
                device
                for device in dr.async_entries_for_config_entry(
                    device_registry, config_entry.entry_id
                )
                if device.via_device_id is None
            ),
            None,
        )
        if edge_device is not None:
            hostname = next(iter(edge_device.identifiers))[1]
            for component_name, enabled in components_options.items():
                device = device_registry.async_get_or_create(
                    config_entry_id=config_entry.entry_id,
                    identifiers={(c.DOMAIN, hostname + " " + component_name)},
                    via_device_id=edge_device.id,
                )
                if not enabled and not device.disabled:
                    device_registry.async_update_device(
                        device.id, disabled_by=dr.DeviceEntryDisabler.USER
                    )
        hass.config_entries.async_update_entry(
            config_entry, options=options, version=5, minor_version=1
        )
        _LOGGER.debug(
            "Migration to configuration version %s.%s successful",
            config_entry.version,
            config_entry.minor_version,
        )

    if config_entry.version == 5:
        # edge configuration moved from the entry data to a store, connection data flattened
        await component_store(hass, config_entry.entry_id).async_save(
            config_entry.data["components"]
        )
        hass.config_entries.async_update_entry(
            config_entry,
            data=dict(config_entry.data["user_input"]),
            version=6,
            minor_version=1,
        )
        _LOGGER.debug(
            "Migration to configuration version %s.%s successful",
            config_entry.version,
            config_entry.minor_version,
        )

    if config_entry.version == 6 and config_entry.minor_version == 1:
        # the edge hostname became the unique ID, take it from the edge device identifier
        device_registry = dr.async_get(hass)
        edge_device = next(
            (
                device
                for device in dr.async_entries_for_config_entry(
                    device_registry, config_entry.entry_id
                )
                if device.via_device_id is None
            ),
            None,
        )
        hass.config_entries.async_update_entry(
            config_entry,
            unique_id=(next(iter(edge_device.identifiers))[1] if edge_device else None),
            minor_version=2,
        )
        _LOGGER.debug(
            "Migration to configuration version %s.%s successful",
            config_entry.version,
            config_entry.minor_version,
        )
        return True

    return False


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: OpenEMSConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Handle user request to remove a (stale) device."""
    edge = entry.runtime_data.backend.the_edge
    if (c.DOMAIN, edge.hostname) in device_entry.identifiers:
        # main device of the edge
        return False
    # allow removal of components, which are not part of the edge anymore
    return not any(
        component_identifier(component) in device_entry.identifiers
        for component in edge.components.values()
    )
