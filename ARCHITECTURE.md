# Architecture

This document describes how the OpenEMS integration (`custom_components/openems`) is structured and the implementation style that new code must follow.

## Module overview

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Config entry setup/unload/migration, options update listener (`update_config`), device removal. Holds the `_PLATFORMS` list. |
| `entry_data.py` | Websocket connection (`OpenEMSWebSocketConnection`, reconnect logic) and reading the edge configuration (`OpenEMSConfigReader`). No HA imports. |
| `openems.py` | Backend object model: `OpenEMSBackend` → `OpenEMSEdge` → `OpenEMSComponent` → channels/properties (`OpenEMSDataHandler` subclasses). No HA entity code. |
| `helpers_openems.py`, `helpers.py` | Pure helpers without HA dependencies. |
| `helpers_ha.py` | Helpers that use HA classes: `RuntimeData`, `OpenEMSConfigEntry`, `component_device`, `unit_description`, `translation_key`, ... |
| `config.py`, `config/*.json` | Static channel configuration (`CONFIG` singleton): default enabled channels, enum options, number limits, combined sensors, ... |
| `<platform>.py` | One module per HA platform (`sensor`, `binary_sensor`, `number`, `select`, `switch`, `time`, `update`, `button`). |
| `config_flow.py`, `services.py`, `device_action.py`, `diagnostics.py` | Remaining HA integration surfaces. |

### Object model and data flow

- `async_setup_entry` creates one `OpenEMSBackend` per config entry; it owns exactly one edge: `backend.the_edge`.
- The edge parses the stored component JSON into `OpenEMSComponent`s. Each component sorts its channels into typed lists: `sensors`, `boolean_sensors`, `derived_sensors`, `number_properties`, `enum_properties`, `boolean_properties`, `time_properties`.
- Runtime data lives in `entry.runtime_data` (`RuntimeData`): `backend`, `edge_device` (`info`: `DeviceInfo`, `entry`: `DeviceEntry`) and `add_component_callbacks`.
- Value updates are push-based: the websocket calls `OpenEMSBackend.edgeRpc` → `OpenEMSEdge.currentData` → registered `OpenEMSDataHandler`s → `notify_ha()` → the entity's registered callback (`async_schedule_update_ha_state`). Channel entities therefore never poll.

### Devices

- One **edge device**, identifier `(DOMAIN, edge.hostname)`, created in `__init__.async_setup_entry`. Its `DeviceInfo` is available as `entry.runtime_data.edge_device.info`.
- One **component device** per enabled component, built with `component_device(entry.runtime_data.edge_device.entry, component)` and linked to the edge device via `via_device_id`.
- In multi-edge setups the hostname is `"<hostname> <edge id>"`, so identifiers must never be split at the first space.

### Unique IDs

- Channel based entities: `channel.unique_id()` → `<hostname>/<edge id>/<component>/<channel>`.
- Edge level entities: `<hostname>/<edge id>/<key>` (e.g. `.../update`, `.../check_update`).
- Unique IDs are persisted in the entity registry. Never change an existing one without a migration in `async_migrate_entry`.

## Platform implementation style

All platform modules follow the same layout, in this order:

1. Module docstring, imports, `_LOGGER = logging.getLogger(__name__)`.
2. `async_setup_entry(hass, entry: OpenEMSConfigEntry, async_add_entities)`.
3. A frozen, kw-only description dataclass `OpenEMS<Platform>Description(<Platform>EntityDescription)`.
4. The entity class `OpenEMS<Platform>Entity(<Platform>Entity)`.

Further rules:

- Access the backend as `backend: OpenEMSBackend = entry.runtime_data.backend` (explicit type annotation).
- Entity descriptions are **built inside `async_setup_entry`**, with `key` set to the entity's unique id. There are no module-level description tuples and no callables (`press_fn`, `value_fn`, ...) inside descriptions.
- The description dataclass only holds static class-level defaults (at least `has_entity_name = True`). Per-entity values (`key`, `name`, `translation_key`, `entity_registry_enabled_default`, `entity_category`, units, ...) are passed when it is instantiated.
- Entity behaviour is implemented as **methods of the entity class**, not as module-level functions.
- Entity constructor signature: `(<backend object>, entity_description, device_info)`. It stores the backend object in a typed private attribute (`self._channel` / `self._edge`), and sets `self.entity_description`, `self._attr_unique_id` and `self._attr_device_info`.
- Use `translation_key` for names. Add new keys to `strings.json`, `translations/en.json` and `translations/de.json` (see `script/update_german_translations.py`).

### Variant A: channel based platforms

`sensor`, `binary_sensor`, `number`, `select`, `switch`, `time`:

```python
async def async_setup_entry(hass, entry: OpenEMSConfigEntry, async_add_entities) -> None:
    """Set up OpenEMS <platform> entities."""

    def _create_<platform>_entities(component: OpenEMSComponent) -> None:
        device = component_device(entry.runtime_data.edge_device.entry, component)
        # create empty device explicitly, in case there are no entities
        dr.async_get(hass).async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMS<Platform>Entity] = []
        for channel in component.<typed channel list>:
            entity_description = OpenEMS<Platform>Description(
                key=channel.unique_id(),
                entity_registry_enabled_default=CONFIG.is_channel_enabled(component.name, channel.name),
                name=channel.name[9:],  # properties: strip "_Property" prefix
                translation_key=translation_key(channel),
                ...
            )
            entities.append(OpenEMS<Platform>Entity(channel, entity_description, device))
        async_add_entities(entities)

    ############ END MARKER _create_<platform>_entities ##############

    backend: OpenEMSBackend = entry.runtime_data.backend
    for component in backend.the_edge.components.values():
        if component.create_entities:
            _create_<platform>_entities(component)

    # prepare callback for creating new entities during options config flow
    entry.runtime_data.add_component_callbacks[Platform.<PLATFORM>.value] = _create_<platform>_entities
```

- Properties (writable `_Property*` channels) use `entity_category=EntityCategory.CONFIG`.
- The entity sets `self._attr_should_poll = False` and `self._attr_extra_state_attributes = channel.orig_json`.
- State is read from the channel in properties (`native_value`, `is_on`, ...). Writes go through the channel (`await self._channel.update_value(...)`) followed by `self.async_write_ha_state()`.
- `async_added_to_hass` calls `self._channel.register_callback(self.async_schedule_update_ha_state)` and `async_will_remove_from_hass` calls `self._channel.unregister_callback()`, each followed by the `super()` call.
- The `add_component_callbacks` registration is required: `__init__.update_config` calls it when a component is enabled in the options flow.

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
- Edge requests (`edge.get_system_update_state()`, ...) catch `jsonrpc_base` `TransportError` / `ProtocolError` and set the entity unavailable instead of raising.

## Tests

- One test module per platform: `tests/test_<platform>.py`. Use `pytest-homeassistant-custom_component` (`asyncio_mode = auto`).
- Unit tests build the entity directly from a description and a `MagicMock` channel or edge, e.g. `OpenEMSSwitchEntity(channel, OpenEMSSwitchDescription(key=..., name=...), device_info)`.
- Setup tests call `<platform>.async_setup_entry(hass, mock_entry, added.extend)` with a `MagicMock` entry whose `runtime_data` is populated.
- `tests/helpers_component.py` (`setup_evcs_component`) provides a real edge/component with registered config entry and devices for integration-style tests.

## Tooling

- Formatting and linting follow HA core: `ruff format` and `ruff check` (configuration in `pyproject.toml`).
- Run tests with `python -m pytest`.
