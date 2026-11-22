"""The HA OpenEMS integration."""

import logging
from typing import Any

from openems_client.client import OpenEMSClient
from openems_client.const import CONN_TYPE_DIRECT_EDGE
from openems_client.exceptions import (
    OpenEMSAuthError,
    OpenEMSConnectionError,
    OpenEMSError,
)
from openems_client.openems import CONFIG, OpenEMSBackend, OpenEMSComponent

from homeassistant.const import CONF_HOST, CONF_TYPE, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
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
    client = OpenEMSClient(
        map_user_input(dict(config_entry.data)), async_get_clientsession(hass)
    )
    edge_id = config_entry.data[c.CONF_EDGE]
    placeholders = {"host": str(client.host)}

    # 1. Connect and log in, the client closes the connection on failure
    try:
        await client.connect()
    except OpenEMSAuthError as ex:
        raise ConfigEntryAuthFailed(
            translation_domain=c.DOMAIN,
            translation_key="invalid_auth",
            translation_placeholders=placeholders,
        ) from ex
    except OpenEMSConnectionError as ex:
        raise ConfigEntryNotReady(
            translation_domain=c.DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders=placeholders,
        ) from ex
    except OpenEMSError as ex:
        raise ConfigEntryNotReady(
            translation_domain=c.DOMAIN,
            translation_key="login_failed",
            translation_placeholders=placeholders,
        ) from ex

    # 2. Load the cached edge configuration. Read it from the edge if there is none yet,
    # otherwise check it in the background after the setup.
    store = component_store(hass, config_entry.entry_id)
    components = await store.async_load()
    cached = components is not None
    if components is None:
        try:
            components = await client.read_edge_config(edge_id)
        except OpenEMSError as ex:
            await client.close()
            raise ConfigEntryNotReady(
                translation_domain=c.DOMAIN,
                translation_key="cannot_read_configuration",
                translation_placeholders=placeholders,
            ) from ex
        await store.async_save(components)

    # 3. Prepare HA entity structures from the edge configuration
    backend = client.create_backend(edge_id, components)

    # 4. Read and set config options
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

    # 5. Create central edge device in HA device registry, so that all component devices can be linked to it
    device_registry = dr.async_get(hass)
    edge_device_info = dr.DeviceInfo(
        identifiers={(c.DOMAIN, backend.the_edge.hostname)},
        name=backend.the_edge.hostname,
        manufacturer=backend.the_edge.manufacturer,
    )
    edge_device_entry: dr.DeviceEntry = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        **edge_device_info,
    )

    @callback
    def _update_sw_version() -> None:
        if (version := backend.the_edge.version) is not None:
            device_registry.async_update_device(
                edge_device_entry.id, sw_version=version
            )

    config_entry.async_on_unload(
        backend.the_edge.add_version_listener(_update_sw_version)
    )

    # 6. Create component devices. Components without default channels start disabled.
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

    # 7. Prepare runtime data for later access
    config_entry.runtime_data = RuntimeData(
        backend=backend,
        edge_device=DeviceRuntimeData(info=edge_device_info, entry=edge_device_entry),
        enabled_components=enabled_components,
    )

    # 8. Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(config_entry, _PLATFORMS)

    # 9. Reload to create or remove entities when a component device gets enabled or disabled
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

    # 10. Start backend
    backend.start()

    if cached:
        config_entry.async_create_background_task(
            hass,
            _async_check_edge_components(hass, config_entry, client, components),
            "openems check edge configuration",
        )


async def _async_check_edge_components(
    hass: HomeAssistant,
    config_entry: OpenEMSConfigEntry,
    client: OpenEMSClient,
    cached: dict[str, Any],
) -> None:
    """Read the edge configuration again, save it and reload if it changed."""
    backend: OpenEMSBackend = config_entry.runtime_data.backend
    try:
        components, change = await client.check_edge_config(
            config_entry.data[c.CONF_EDGE], cached
        )
    except OpenEMSError as err:
        _LOGGER.warning(
            "Cannot check the configuration of edge %s: %s",
            backend.the_edge.hostname,
            err,
        )
        return
    if components == cached:
        return
    await component_store(hass, config_entry.entry_id).async_save(components)
    if change:
        _LOGGER.info(
            "Configuration of edge %s changed (added: %s, removed: %s, changed: %s), "
            "reloading",
            backend.the_edge.hostname,
            sorted(change.added),
            sorted(change.removed),
            sorted(change.changed),
        )
        hass.config_entries.async_schedule_reload(config_entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(config_entry, _PLATFORMS)
    await config_entry.runtime_data.backend.stop()


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

    # minor versions of version 6, each step migrates to the next minor version
    steps = {
        2: _async_remove_refresh_configuration_button,
        3: _async_remove_write_only_sensors,
        4: _async_reset_device_entry_type,
    }
    if config_entry.version == 6 and config_entry.minor_version in steps:
        for minor_version in range(config_entry.minor_version, max(steps) + 1):
            await steps[minor_version](hass, config_entry)
            hass.config_entries.async_update_entry(
                config_entry, minor_version=minor_version + 1
            )
            _LOGGER.debug(
                "Migration to configuration version %s.%s successful",
                config_entry.version,
                config_entry.minor_version,
            )
        return True

    return False


async def _async_remove_refresh_configuration_button(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> None:
    """Remove the button replaced by the configuration check during the setup."""
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    ):
        if entity.domain == Platform.BUTTON and entity.unique_id.endswith(
            "/refresh_configuration"
        ):
            entity_registry.async_remove(entity.entity_id)


async def _async_remove_write_only_sensors(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> None:
    """Remove the sensors of write-only channels, written by the write_channel action."""
    components = await component_store(hass, config_entry.entry_id).async_load()
    write_only_suffixes = tuple(
        f"/{component_name}/{channel['id']}"
        for component_name, component in (components or {}).items()
        for channel in component.get("channels", [])
        if channel.get("accessMode") == "WO"
        and not channel["id"].startswith("_Property")
    )
    if not write_only_suffixes:
        return
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    ):
        if entity.domain in (
            Platform.SENSOR,
            Platform.BINARY_SENSOR,
        ) and entity.unique_id.endswith(write_only_suffixes):
            entity_registry.async_remove(entity.entity_id)


async def _async_reset_device_entry_type(
    hass: HomeAssistant, config_entry: OpenEMSConfigEntry
) -> None:
    """Reset the service entry type, the edge and its components are no web services."""
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    ):
        if device.entry_type is not None:
            device_registry.async_update_device(device.id, entry_type=None)


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
