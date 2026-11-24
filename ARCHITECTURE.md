# Architecture

This document describes how the OpenEMS integration (`custom_components/openems`) is structured and the implementation style that new code must follow.

## Module overview

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Config entry setup/unload/migration, device removal. Holds the `_PLATFORMS` list. Option changes reload the entry (`OptionsFlowWithReload`). |
| `helpers_ha.py` | Helpers that use HA classes: `RuntimeData`, `OpenEMSConfigEntry`, `component_device`, `component_from_device`, `property_selector`, ... |
| `const.py` | HA-only constants (`DOMAIN`, `ATTR_*`, config flow keys). |
| `<platform>.py` | One module per HA platform (`sensor`, `binary_sensor`, `number`, `select`, `switch`, `time`, `update`, `button`). |
| `entity.py` | `OpenEMSChannelEntity`, the base class of the channel based entities: unique ID, device, availability, channel listener. |
| `config_flow.py`, `services.py`, `device_action.py`, `diagnostics.py` | Remaining HA integration surfaces. |

The protocol implementation lives in the [`openems-client`](https://github.com/Lamarqe/openems-client) library (`openems_client`), which has no HA dependencies:

| Module | Responsibility |
| --- | --- |
| `openems_client.client` | `OpenEMSClient`, the entry point: connect and log in, list edges, read the hostname and configuration of an edge, create the backend. |
| `openems_client.connection` | Websocket connection (`OpenEMSWebSocketConnection`, reconnect logic) and reading the edge configuration (`OpenEMSConfigReader`), used by the client. |
| `openems_client.openems` | Backend object model: `OpenEMSBackend` → `OpenEMSEdge` → `OpenEMSComponent` → channels/properties (`OpenEMSDataHandler` subclasses). |
| `openems_client.helpers_openems`, `openems_client.helpers` | Pure helpers. |
| `openems_client.config`, `config/*.json` | Channel type and constraint knowledge missing in the OpenEMS metadata: enum options, time properties, number limits/multiplier/step, update groups. |
| `openems_client.const` | Connection types, timeouts and `SLASH_ESC`. The config entry keys (`CONF_ADVANCED_OPTIONS`, ...) are HA-only and live in the integration's `const.py`. |

### Object model and data flow

- `async_setup_entry` creates one `OpenEMSBackend` per config entry; it owns exactly one edge: `backend.the_edge`.
- All access to the system goes through `OpenEMSClient(map_user_input(...), async_get_clientsession(hass))`, with HA's shared session. The config flow uses it as `async with` context manager, which closes the connection. Setup calls `client.connect()`, reads or loads the configuration and creates the backend with `client.create_backend(...)`, which takes over the connection. Constructing library objects starts no background tasks: `backend.start()` at the end of `async_setup_entry` starts them, `await backend.stop()` in `async_unload_entry` closes the connection and waits until they have finished.
- The edge configuration (component JSON with channels) is cached in a `Store` per config entry (`helpers_ha.component_store`, key `openems.<entry_id>`). Setup reads it from the edge only when the cache is empty. Otherwise, after the setup, a background task (`__init__._async_check_edge_components`) reads it again with `client.check_edge_config`: changes are saved, structural changes (components added, removed, `factoryId` or channels changed) also reload the entry. So "Reload" of the config entry is the explicit check for changes. Reconfiguring the entry and removing it (`async_remove_entry`) delete the cache. The config entry data only holds the connection data (`host`, `username`, `password`, `type`, `url`, `edge`).
- The edge parses the component JSON into `OpenEMSComponent`s. The library classifies every channel: `kind` (`openems_client.model.ChannelKind`: `SENSOR`, `ENUM_SENSOR`, `TEXT_SENSOR`, `BINARY_SENSOR`, `NUMBER`, `SELECT`, `SWITCH`, `TIME`), `key`, normalised `unit`, `quantity`, `cumulative`, `primary`, `severity` and `option_keys`. Platforms get their channels with `component.channels_of(ChannelKind.<KIND>[, <channel class>])` and only map these attributes to HA concepts (device class, state class, ...). OpenEMS knowledge (units, default channels, enum options) belongs in the library, not in the integration.
- Primary channels, the channels enabled by default, are defined in the library (`config/primary_channels.json`). Derived channels, eg the battery cell voltage differences, are created by the library (`openems.DERIVED_CHANNELS`) and are `SENSOR` channels of their component.
- Runtime data lives in `entry.runtime_data` (`RuntimeData`): `backend`, `edge_device` (`info`: `DeviceInfo`, `entry`: `DeviceEntry`) and `enabled_components`.
- Value updates are push-based: the websocket calls `OpenEMSBackend.edgeRpc` → `OpenEMSEdge.currentData` → registered `OpenEMSDataHandler`s → their listeners (the entity's `async_schedule_update_ha_state`). Channel entities therefore never poll.

### Devices

- One **edge device**, identifier `(DOMAIN, edge.hostname)`, created in `__init__.async_setup_entry`. Its `DeviceInfo` is available as `entry.runtime_data.edge_device.info`. Its `manufacturer` comes from the library (`edge.manufacturer`: FENECON for FEMS, none otherwise). A version listener (`edge.add_version_listener`) keeps its `sw_version` up to date.
- One **component device** per component, built with `component_device(entry.runtime_data.edge_device.entry, component)` and linked to the edge device via `via_device_id`. `__init__.async_setup_entry` creates them for all components, the platforms do not create devices. Component devices show the alias as `model` and the implementation (`component.factory_id`, e.g. `Evcs.HardyBarth`) as `model_id`; devices have no entry type. New devices of components without primary channels (`component.has_primary_channels`) are created with `disabled_by=DeviceEntryDisabler.INTEGRATION`; existing devices keep their state.
- `entry.runtime_data.enabled_components` holds the components with an enabled device. Platforms create entities only for those, so disabled components have no entities at all.
- Enabling or disabling a component device reloads the config entry (`async_track_device_registry_updated_event` in `async_setup_entry`), which creates or removes the entities of the component.
- The component device identifier is `(DOMAIN, component.unique_id)` (`"<hostname> <component>"`, see `component_identifier`). In multi-edge setups the hostname is `"<hostname> <edge id>"`, so identifiers are compared as a whole and never split.

### Unique IDs

- Config entry: the edge hostname, read in the config flow (`client.read_edge_hostname`), with `" <edge id>"` in multi-edge setups, so it equals the edge device identifier. Adding the same system twice aborts, and reconfigure and reauth abort when they lead to another system.
- Channel based entities: `channel.unique_id()` → `<hostname>/<edge id>/<component>/<channel>`.
- Edge level entities: `<hostname>/<edge id>/<key>` (e.g. `.../update`, `.../restart`).
- Unique IDs are persisted in the entity registry. Never change an existing one without a migration in `async_migrate_entry`.

## Platform implementation style

All platform modules follow the same layout, in this order:

1. Module docstring, imports, `_LOGGER = logging.getLogger(__name__)`.
2. `async_setup_entry(hass, entry: OpenEMSConfigEntry, async_add_entities)`.
3. A frozen, kw-only description dataclass `OpenEMS<Platform>Description(<Platform>EntityDescription)`.
4. The entity class: `OpenEMS<Platform>Entity(OpenEMSChannelEntity[<channel class>], <Platform>Entity)` for channel based platforms, `OpenEMS<Name>Entity(<Platform>Entity)` for edge level platforms.

Further rules:

- Access the backend as `backend: OpenEMSBackend = entry.runtime_data.backend` (explicit type annotation).
- Entity descriptions are **built inside `async_setup_entry`**, with `key` set to the entity's unique id. There are no module-level description tuples and no callables (`press_fn`, `value_fn`, ...) inside descriptions.
- The description dataclass only holds static class-level defaults (at least `has_entity_name = True`). Per-entity values (`key`, `name`, `translation_key`, `entity_registry_enabled_default`, `entity_category`, units, ...) are passed when it is instantiated.
- Entity behaviour is implemented as **methods of the entity class**, not as module-level functions.
- Entity constructor signature: `(<backend object>, entity_description, device_info)`. It stores the backend object in a typed private attribute (`self._channel` / `self._edge`), and sets `self.entity_description`, `self._attr_unique_id` and `self._attr_device_info`. For channel based entities, the base class `OpenEMSChannelEntity` (`entity.py`) does this.
- Errors: the library raises only `openems_client.exceptions` (`OpenEMSError`, `OpenEMSConnectionError`, `OpenEMSAuthError`) and handles the timeouts itself; HA code never catches `jsonrpc_base` errors and adds no timeouts. Raised HA exceptions are translated (`translation_key` and placeholders, no stringified exception). Write methods of channel entities use the `@handle_write_errors` decorator from `helpers_ha.py`.
- Use `translation_key` for names. Add new keys to `strings.json`, `translations/en.json` and `translations/de.json` (see `script/update_german_translations.py`).
- Channel translation keys are `channel.key`: the snake_case channel name without the `_Property` prefix (e.g. `ChargeMode` → `charge_mode`). They are shared by all components, so state translations of one key cover the options of every component with that channel; the state keys are the `channel.option_keys`. Every primary channel that creates an entity has a `name` under its key (checked by `test_primary_channels_have_translated_names`); platforms pass `name=UNDEFINED` for them, so HA uses the translation. All other channels pass `channel.display_name` as `name`: a readable English name generated by the library from the channel ID (e.g. `MaxDcChargePower` → "Max DC charge power", acronyms in `config/acronyms.json` of the library).

### Variant A: channel based platforms

`sensor`, `binary_sensor`, `number`, `select`, `switch`, `time`:

```python
async def async_setup_entry(hass, entry: OpenEMSConfigEntry, async_add_entities) -> None:
    """Set up OpenEMS <platform> entities."""

    def _create_<platform>_entities(component: OpenEMSComponent) -> None:
        device = component_device(entry.runtime_data.edge_device.entry, component)

        entities: list[OpenEMS<Platform>Entity] = []
        for channel in component.channels_of(ChannelKind.<KIND>, <channel class>):
            entity_description = OpenEMS<Platform>Description(
                key=channel.unique_id,
                entity_registry_enabled_default=channel.primary,
                # primary channels have a translated name, others a generated one
                name=UNDEFINED if channel.primary else channel.display_name,
                translation_key=channel.key,
                ...
            )
            entities.append(OpenEMS<Platform>Entity(channel, entity_description, device))
        async_add_entities(entities)

    for component in entry.runtime_data.enabled_components:
        _create_<platform>_entities(component)
```

- Properties (writable `_Property*` channels) use `entity_category=EntityCategory.CONFIG`, text sensors (`ChannelKind.TEXT_SENSOR`, e.g. versions and serial numbers) `EntityCategory.DIAGNOSTIC`. Other channels have no entity category.
- `OpenEMSChannelEntity` sets `_attr_should_poll = False` and combines `available` with `channel.available`, so entities are unavailable while the edge is disconnected. Entities have no extra state attributes; the channel metadata is part of the diagnostics.
- State is read from the channel in properties (`channel.value`, `is_on`, ...). Writes go through the channel (`await self._channel.update_value(...)`) followed by `self.async_write_ha_state()`.
- Write-only channels (`ChannelKind.WRITE_ONLY`) have no entities. They are written with the `write_channel` action and the device action of the same name (`channel.write_via_rest`).
- `OpenEMSChannelEntity.async_added_to_hass` registers `_handle_channel_update` as listener of the channel, which is called on value and availability changes. The channel is subscribed at the edge while it has listeners. Platforms override `_handle_channel_update` only for extra logic (e.g. `sensor.py`), not `async_added_to_hass`.

### Variant B: edge level platforms

`update`, `button`: one entity per edge, attached to the edge device. Reference implementation: `update.py`.

```python
async def async_setup_entry(hass, entry: OpenEMSConfigEntry, async_add_entities) -> None:
    """Set up OpenEMS <platform> entity."""
    backend: OpenEMSBackend = entry.runtime_data.backend
    entity_description = OpenEMS<Name>Description(key=<edge level unique id>)

    entity = OpenEMS<Name>Entity(
        backend.the_edge, entity_description, entry.runtime_data.edge_device.info
    )
    async_add_entities([entity])


@dataclass(frozen=True, kw_only=True)
class OpenEMS<Name>Description(<Platform>EntityDescription):
    has_entity_name = True
    translation_key = ...      # static defaults only
    entity_category = ...


class OpenEMS<Name>Entity(<Platform>Entity):
    entity_description: OpenEMS<Name>Description

    def __init__(self, edge: OpenEMSEdge, entity_description, device_info: DeviceInfo) -> None:
        self._edge: OpenEMSEdge = edge
        self.entity_description = entity_description
        self._attr_unique_id = entity_description.key
        self._attr_device_info = device_info
```

- If another module needs an edge level unique id, expose it as a module-level function next to its platform (e.g. `update.update_unique_id(edge)`). Do not duplicate the string format.
- The update entity is a firmware update (`UpdateDeviceClass.FIRMWARE`). Release URL and release notes come from the library (`edge.release_url`, `edge.get_release_notes(language)`, only for FEMS); the integration does not know FEMS specifics.
- Polling edge requests (`edge.get_system_update_state()`) catch `OpenEMSError` and set the entity unavailable instead of raising. Requests triggered by the user (buttons) raise a translated `HomeAssistantError`.
- Long running edge operations are implemented in the library, not in the entity: `edge.install_system_update(on_progress)` installs the update and reports each state to the callback, which writes the entity state. The entity only maps the library exceptions (`OpenEMSTimeoutError`, `OpenEMSError`) to translated errors.

## Tests

Tests follow the style of HA core and are laid out like there: `tests/components/openems/`, one module per platform (`test_<platform>.py`), using `pytest-homeassistant`.

- Test through Home Assistant: set up the entry with the `init_integration` fixture or `setup_integration()`, call services and read states. Do not build entities or call `async_setup_entry` directly.
- `mock_openems_client` (`conftest.py`) mocks `OpenEMSClient`. Its backends are real `OpenEMSBackend`s with a fake connection, so entities come from the library classification. Edge data is sent with `rpc_server.push_current_data(...)`, requests are checked with `rpc_server.component_updates()` and `request_methods()`, edge answers are set in `rpc_server.results`.
- The edge configuration and channel data are JSON fixtures (`fixtures/edge_config.json`, `channel_data.json`): a reduced, anonymised extract of a real FEMS. Vary them with `pytest.mark.parametrize(..., indirect=True)` on `edge_config`, `channel_data`, `mock_config_entry` (advanced options) or `multi_edge`.
- Entities, devices, diagnostics and action capabilities are covered by snapshots (`snapshot_platform`, `--snapshot-update`); set the `platforms` fixture to one platform.
- Tests of library behaviour belong to the library.

## Tooling

- Formatting and linting follow HA core: `ruff format` and `ruff check` (configuration in `pyproject.toml`).
- Run tests with `python -m pytest`.
