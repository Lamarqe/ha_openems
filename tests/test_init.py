"""Tests for config entry setup, migration and options update in __init__.py."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from openems_client.exceptions import (
    OpenEMSAuthError,
    OpenEMSConnectionError,
    OpenEMSError,
)
import pytest

from custom_components.openems import (
    async_migrate_entry,
    async_remove_config_entry_device,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
    CONF_COMPONENTS,
)
from custom_components.openems.const import (
    CONF_ADVANCED_OPTIONS,
    CONF_FORWARD_INTERVAL,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
)
from custom_components.openems.const import DOMAIN
from openems_client.connection import OpenEMSConfigReader
from openems_client.edge_config import EdgeConfigChange
from openems_client.openems import CONFIG
from custom_components.openems.helpers_ha import RuntimeData
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.const import Platform
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from .helpers_flow import make_mock_connection

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_USER_INPUT = {
    "type": "direct_edge",
    "host": "192.0.2.1",
    "username": "user",
    "password": "password",
    "edge": "edge-1",
}

_ENTRY_DATA = {
    "user_input": _USER_INPUT,
    "components": {"comp1": {}},
}

_ADVANCED_OPTIONS = {
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
    CONF_FORWARD_INTERVAL: 0,
}

_DEFAULT_OPTIONS = {CONF_ADVANCED_OPTIONS: _ADVANCED_OPTIONS}

_FLOW_USER_INPUT = {
    "host": "192.0.2.1",
    "username": "user",
    "password": "password",
    "more_options": {"type": "direct_edge"},
}

_EDGES_RESPONSE = {"edges": [{"id": "edge-1", "isOnline": True}]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn_patches(login_response=None):
    """Return context manager patches for a successful connection + login."""
    mock_conn = make_mock_connection(login_response)
    return (
        patch(
            "openems_client.client.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        mock_conn,
    )


async def _create_real_entry(hass: HomeAssistant) -> config_entries.ConfigEntry:
    """Use the config flow to create a real config entry (setup is mocked)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    mock_conn = make_mock_connection()
    with (
        patch(
            "openems_client.client.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        patch.object(OpenEMSConfigReader, "read_edges", return_value=_EDGES_RESPONSE),
        patch.object(OpenEMSConfigReader, "read_edge_config", return_value={}),
        patch("custom_components.openems.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _FLOW_USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    hass.config_entries.async_update_entry(entry, options=_DEFAULT_OPTIONS)
    placeholder_backend = MagicMock()
    placeholder_backend.stop = AsyncMock()
    entry.runtime_data = RuntimeData(
        backend=placeholder_backend, edge_device=MagicMock(), enabled_components=[]
    )
    return entry


# ---------------------------------------------------------------------------
# async_setup_entry - happy path
# ---------------------------------------------------------------------------


async def test_setup_entry_success(hass: HomeAssistant) -> None:
    """A valid connection and login results in a fully set-up entry."""
    entry = await _create_real_entry(hass)

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.set_config_options = MagicMock()
    mock_backend.start = MagicMock()

    conn_patch, _ = _make_conn_patches()
    with (
        conn_patch,
        patch.object(OpenEMSConfigReader, "read_edge_components", return_value={}),
        patch("openems_client.client.OpenEMSBackend", return_value=mock_backend),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert entry.runtime_data.backend is mock_backend
    mock_backend.start.assert_called_once()


def _storage_key(entry: config_entries.ConfigEntry) -> str:
    return f"{DOMAIN}.{entry.entry_id}"


def _unchanged(cached: dict[str, Any]) -> tuple[dict[str, Any], EdgeConfigChange]:
    return cached, EdgeConfigChange()


async def _setup_with_mocked_backend(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    read_edge_components: AsyncMock,
    check_edge_components: AsyncMock | None = None,
) -> MagicMock:
    """Run async_setup_entry and its background tasks, return the OpenEMSBackend class."""
    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = {}

    conn_patch, _ = _make_conn_patches()
    with (
        conn_patch,
        patch.object(
            OpenEMSConfigReader, "read_edge_components", new=read_edge_components
        ),
        patch.object(
            OpenEMSConfigReader,
            "check_edge_components",
            new=check_edge_components or AsyncMock(side_effect=_unchanged),
        ),
        patch(
            "openems_client.client.OpenEMSBackend", return_value=mock_backend
        ) as backend_class,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        await async_setup_entry(hass, entry)
        await hass.async_block_till_done(wait_background_tasks=True)
    return backend_class


async def test_setup_entry_reads_and_caches_configuration(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Without a cached configuration, setup reads it from the edge and caches it."""
    entry = await _create_real_entry(hass)
    read_edge_components = AsyncMock(return_value={"comp1": {"channels": []}})

    backend_class = await _setup_with_mocked_backend(hass, entry, read_edge_components)

    read_edge_components.assert_awaited_once()
    assert hass_storage[_storage_key(entry)]["data"] == {"comp1": {"channels": []}}
    assert backend_class.call_args.args[3] == {"comp1": {"channels": []}}


async def test_setup_entry_uses_cached_configuration(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A cached configuration is used without reading it from the edge."""
    entry = await _create_real_entry(hass)
    hass_storage[_storage_key(entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": _storage_key(entry),
        "data": {"cached": {"channels": []}},
    }
    read_edge_components = AsyncMock()

    backend_class = await _setup_with_mocked_backend(hass, entry, read_edge_components)

    read_edge_components.assert_not_awaited()
    assert backend_class.call_args.args[3] == {"cached": {"channels": []}}


def _cache(
    hass_storage: dict[str, Any], entry: config_entries.ConfigEntry, data: dict
) -> None:
    hass_storage[_storage_key(entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": _storage_key(entry),
        "data": data,
    }


_CACHED = {"meter0": {"factoryId": "Meter.Socomec", "properties": {}, "channels": []}}
_PROPERTY_CHANGED = {
    "meter0": {"factoryId": "Meter.Socomec", "properties": {"a": 1}, "channels": []}
}
_COMPONENT_ADDED = {**_CACHED, "ess0": {"factoryId": "Ess", "channels": []}}


@pytest.mark.parametrize(
    ("result", "saved", "reloaded"),
    [
        pytest.param((_CACHED, EdgeConfigChange()), _CACHED, False, id="unchanged"),
        pytest.param(
            (_PROPERTY_CHANGED, EdgeConfigChange()),
            _PROPERTY_CHANGED,
            False,
            id="properties_changed",
        ),
        pytest.param(
            (_COMPONENT_ADDED, EdgeConfigChange(added=frozenset({"ess0"}))),
            _COMPONENT_ADDED,
            True,
            id="component_added",
        ),
    ],
)
async def test_setup_entry_checks_cached_configuration(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    result: tuple[dict[str, Any], EdgeConfigChange],
    saved: dict[str, Any],
    reloaded: bool,
) -> None:
    """The cached configuration is checked in the background, changes are saved.

    Structural changes reload the entry, to create the entities and devices.
    """
    entry = await _create_real_entry(hass)
    _cache(hass_storage, entry, _CACHED)
    check_edge_components = AsyncMock(return_value=result)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _setup_with_mocked_backend(
            hass, entry, AsyncMock(), check_edge_components
        )

    check_edge_components.assert_awaited_once_with(_CACHED)
    assert hass_storage[_storage_key(entry)]["data"] == saved
    assert reload.called is reloaded


async def test_setup_entry_check_error_keeps_configuration(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed check keeps the cached configuration and only logs a warning."""
    entry = await _create_real_entry(hass)
    _cache(hass_storage, entry, _CACHED)
    check_edge_components = AsyncMock(side_effect=OpenEMSError("timeout"))

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _setup_with_mocked_backend(
            hass, entry, AsyncMock(), check_edge_components
        )

    assert hass_storage[_storage_key(entry)]["data"] == _CACHED
    reload.assert_not_called()
    assert "Cannot check the configuration of edge host: timeout" in caplog.text


async def test_setup_entry_without_cache_skips_check(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A configuration read during the setup is not checked again."""
    entry = await _create_real_entry(hass)
    check_edge_components = AsyncMock()

    await _setup_with_mocked_backend(
        hass, entry, AsyncMock(return_value=_CACHED), check_edge_components
    )

    check_edge_components.assert_not_awaited()


async def test_setup_entry_read_configuration_error_raises_not_ready(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A failure while reading the configuration raises ConfigEntryNotReady."""
    entry = await _create_real_entry(hass)
    read_edge_components = AsyncMock(
        side_effect=OpenEMSConnectionError("connection lost")
    )

    with pytest.raises(ConfigEntryNotReady):
        await _setup_with_mocked_backend(hass, entry, read_edge_components)

    assert _storage_key(entry) not in hass_storage


async def test_remove_entry_removes_cached_configuration(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Removing the config entry removes its cached configuration."""
    entry = MockConfigEntry(domain=DOMAIN, data=_USER_INPUT)
    entry.add_to_hass(hass)
    hass_storage[_storage_key(entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": _storage_key(entry),
        "data": {"comp1": {}},
    }

    await async_remove_entry(hass, entry)

    assert _storage_key(entry) not in hass_storage


@pytest.mark.parametrize(
    ("connect_error", "login_error", "expected", "translation_key"),
    [
        pytest.param(
            OpenEMSConnectionError("connection refused"),
            None,
            ConfigEntryNotReady,
            "cannot_connect",
            id="connect_error",
        ),
        pytest.param(
            None,
            OpenEMSConnectionError("connection lost"),
            ConfigEntryNotReady,
            "cannot_connect",
            id="login_connection_error",
        ),
        pytest.param(
            None,
            OpenEMSAuthError("Authentication failed"),
            ConfigEntryAuthFailed,
            "invalid_auth",
            id="login_rejected",
        ),
        pytest.param(
            None,
            OpenEMSError("Internal server error"),
            ConfigEntryNotReady,
            "login_failed",
            id="login_error",
        ),
    ],
)
async def test_setup_entry_connection_errors(
    hass: HomeAssistant,
    connect_error: OpenEMSError | None,
    login_error: OpenEMSError | None,
    expected: type[HomeAssistantError],
    translation_key: str,
) -> None:
    """Connection and login errors raise translated setup errors and stop the connection."""
    entry = await _create_real_entry(hass)

    mock_conn = MagicMock()
    mock_conn.connect_to_server = AsyncMock(side_effect=connect_error)
    mock_conn.login_to_server = AsyncMock(side_effect=login_error)
    mock_conn.stop = AsyncMock()
    mock_conn.conn_url.host = "192.0.2.1"

    with (
        patch(
            "openems_client.client.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        pytest.raises(expected) as exc_info,
    ):
        await async_setup_entry(hass, entry)

    assert exc_info.value.translation_key == translation_key
    assert exc_info.value.translation_placeholders == {"host": "192.0.2.1"}
    mock_conn.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# async_unload_entry
# ---------------------------------------------------------------------------


async def _prepare_unload(
    hass: HomeAssistant, unload_ok: bool
) -> tuple[config_entries.ConfigEntry, MagicMock]:
    """Return a set up entry and a mock recording the platform and backend calls."""
    entry = await _create_real_entry(hass)
    calls = MagicMock()
    calls.unload_platforms = AsyncMock(return_value=unload_ok)
    calls.stop = AsyncMock()
    entry.runtime_data = RuntimeData(
        backend=MagicMock(stop=calls.stop),
        edge_device=MagicMock(),
        enabled_components=[],
    )
    return entry, calls


async def test_unload_entry_stops_backend_after_platforms(hass: HomeAssistant) -> None:
    """The backend is stopped after all platforms are unloaded."""
    entry, calls = await _prepare_unload(hass, unload_ok=True)

    with patch.object(
        hass.config_entries, "async_unload_platforms", new=calls.unload_platforms
    ):
        await async_unload_entry(hass, entry)

    assert [name for name, _, _ in calls.mock_calls] == ["unload_platforms", "stop"]


async def test_unload_entry_failure_keeps_backend_running(hass: HomeAssistant) -> None:
    """If a platform cannot be unloaded, the backend keeps running."""
    entry, calls = await _prepare_unload(hass, unload_ok=False)

    with (
        patch.object(
            hass.config_entries, "async_unload_platforms", new=calls.unload_platforms
        ),
        pytest.raises(ConfigEntryError),
    ):
        await async_unload_entry(hass, entry)

    calls.stop.assert_not_awaited()


# ---------------------------------------------------------------------------
# async_setup_entry - component devices
# ---------------------------------------------------------------------------


def _make_component(name: str) -> MagicMock:
    """Return a mock component of the edge with hostname "host"."""
    component = MagicMock()
    component.name = name
    component.alias = name
    component.edge.hostname = "host"
    component.unique_id = "host " + name
    component.factory_id = "Factory." + name
    component.has_primary_channels = CONFIG.has_primary_channels(name)
    return component


async def _setup_with_components(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    components: dict[str, MagicMock],
) -> MagicMock:
    """Run async_setup_entry with a mock backend containing the given components."""
    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = components

    conn_patch, _ = _make_conn_patches()
    with (
        conn_patch,
        patch.object(OpenEMSConfigReader, "read_edge_components", return_value={}),
        patch("openems_client.client.OpenEMSBackend", return_value=mock_backend),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        await async_setup_entry(hass, entry)
    return mock_backend


@pytest.mark.parametrize(
    ("connection_type", "manufacturer"),
    [
        pytest.param("direct_edge", None, id="openems"),
        pytest.param("local_fems", "FENECON", id="local_fems"),
        pytest.param("web_fenecon", "FENECON", id="fenecon_portal"),
    ],
)
async def test_setup_entry_device_info(
    hass: HomeAssistant, connection_type: str, manufacturer: str | None
) -> None:
    """FEMS edges are made by FENECON, components show their implementation."""
    entry = await _create_real_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "type": connection_type}
    )

    await _setup_with_components(hass, entry, {"ess0": _make_component("ess0")})

    device_registry = dr.async_get(hass)
    edge_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "host"), entry.entry_id
    )
    ess_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "host ess0"), entry.entry_id
    )
    assert edge_device.manufacturer == manufacturer
    assert edge_device.entry_type is None
    assert ess_device.manufacturer is None
    assert ess_device.entry_type is None
    assert ess_device.model == "ess0"
    assert ess_device.model_id == "Factory.ess0"


async def test_setup_entry_updates_software_version(hass: HomeAssistant) -> None:
    """The edge device shows the software version reported by the edge."""
    entry = await _create_real_entry(hass)
    mock_backend = await _setup_with_components(hass, entry, {})
    version_listener = mock_backend.the_edge.add_version_listener.call_args.args[0]
    device_registry = dr.async_get(hass)

    mock_backend.the_edge.version = "2026.8.1"
    version_listener()
    # an unknown version does not replace the known one
    mock_backend.the_edge.version = None
    version_listener()

    edge_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "host"), entry.entry_id
    )
    assert edge_device.sw_version == "2026.8.1"


async def test_setup_entry_creates_component_devices(hass: HomeAssistant) -> None:
    """Components without default channels get a device disabled by the integration."""
    entry = await _create_real_entry(hass)
    components = {
        "ess0": _make_component("ess0"),
        "ctrlDebugLog0": _make_component("ctrlDebugLog0"),
    }

    await _setup_with_components(hass, entry, components)

    device_registry = dr.async_get(hass)
    ess_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "host ess0"), entry.entry_id
    )
    debug_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "host ctrlDebugLog0"), entry.entry_id
    )
    assert ess_device.disabled_by is None
    assert debug_device.disabled_by is dr.DeviceEntryDisabler.INTEGRATION
    assert entry.runtime_data.enabled_components == [components["ess0"]]


async def test_setup_entry_keeps_component_device_enabled_by_user(
    hass: HomeAssistant,
) -> None:
    """An existing enabled device keeps its state, so its component gets entities."""
    entry = await _create_real_entry(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "host ctrlDebugLog0")}
    )
    components = {"ctrlDebugLog0": _make_component("ctrlDebugLog0")}

    await _setup_with_components(hass, entry, components)

    assert entry.runtime_data.enabled_components == [components["ctrlDebugLog0"]]


async def test_setup_entry_keeps_component_device_disabled_by_user(
    hass: HomeAssistant,
) -> None:
    """A device disabled by the user stays disabled, so its component gets no entities."""
    entry = await _create_real_entry(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "host ess0")}
    )
    device_registry.async_update_device(
        device.id, disabled_by=dr.DeviceEntryDisabler.USER
    )
    components = {"ess0": _make_component("ess0")}

    await _setup_with_components(hass, entry, components)

    assert entry.runtime_data.enabled_components == []
    assert (
        device_registry.async_get(device.id).disabled_by is dr.DeviceEntryDisabler.USER
    )


async def test_component_device_enable_schedules_reload(hass: HomeAssistant) -> None:
    """Enabling or disabling a component device reloads the entry, other changes don't."""
    entry = await _create_real_entry(hass)
    await _setup_with_components(
        hass, entry, {"ctrlDebugLog0": _make_component("ctrlDebugLog0")}
    )
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "host ctrlDebugLog0"), entry.entry_id
    )

    with patch.object(hass.config_entries, "async_schedule_reload") as mock_reload:
        device_registry.async_update_device(device.id, name_by_user="Debug log")
        await hass.async_block_till_done()
        mock_reload.assert_not_called()

        device_registry.async_update_device(device.id, disabled_by=None)
        await hass.async_block_till_done()
        mock_reload.assert_called_once_with(entry.entry_id)


# ---------------------------------------------------------------------------
# async_migrate_entry
# ---------------------------------------------------------------------------


async def test_migrate_v5_moves_components_to_store(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The edge configuration moves to the store, the connection data is flattened."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=5,
        minor_version=1,
        data=_ENTRY_DATA,
        options=_DEFAULT_OPTIONS,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert (entry.version, entry.minor_version) == (6, 5)
    assert entry.data == _USER_INPUT
    assert hass_storage[_storage_key(entry)]["data"] == {"comp1": {}}


@pytest.mark.parametrize(
    "edge_identifier",
    [
        pytest.param("fems1", id="single_edge"),
        pytest.param("fems1 edge0", id="multi_edge"),
    ],
)
async def test_migrate_v6_1_sets_unique_id_from_edge_device(
    hass: HomeAssistant, edge_identifier: str
) -> None:
    """The unique ID is taken from the identifier of the edge device."""
    entry = MockConfigEntry(domain=DOMAIN, version=6, minor_version=1, data=_USER_INPUT)
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    edge_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, edge_identifier)}
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{edge_identifier} ess0")},
        via_device_id=edge_device.id,
    )

    assert await async_migrate_entry(hass, entry)

    assert (entry.version, entry.minor_version) == (6, 5)
    assert entry.unique_id == edge_identifier


async def test_migrate_v6_2_removes_refresh_configuration_button(
    hass: HomeAssistant,
) -> None:
    """The entity of the removed refresh configuration button is removed."""
    entry = MockConfigEntry(domain=DOMAIN, version=6, minor_version=2, data=_USER_INPUT)
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    for key in ("refresh_configuration", "restart"):
        entity_registry.async_get_or_create(
            Platform.BUTTON, DOMAIN, f"fems1/0/{key}", config_entry=entry
        )

    assert await async_migrate_entry(hass, entry)

    assert (entry.version, entry.minor_version) == (6, 5)
    assert not entity_registry.async_get_entity_id(
        Platform.BUTTON, DOMAIN, "fems1/0/refresh_configuration"
    )
    assert entity_registry.async_get_entity_id(Platform.BUTTON, DOMAIN, "fems1/0/restart")


async def test_migrate_v6_4_resets_device_entry_type(hass: HomeAssistant) -> None:
    """The edge and its components are no web services."""
    entry = MockConfigEntry(domain=DOMAIN, version=6, minor_version=4, data=_USER_INPUT)
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "fems1")},
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    assert await async_migrate_entry(hass, entry)

    assert (entry.version, entry.minor_version) == (6, 5)
    assert device_registry.async_get(device.id).entry_type is None


async def test_migrate_v4_moves_component_selection_to_devices(
    hass: HomeAssistant,
) -> None:
    """Deselected components get devices disabled by the user, options are cleaned."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        minor_version=1,
        data=_ENTRY_DATA,
        options={
            CONF_COMPONENTS: {"ess0": True, "meter0": False, "ctrlDebugLog0": False},
            CONF_ADVANCED_OPTIONS: _ADVANCED_OPTIONS,
        },
    )
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    edge_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "host")}
    )
    for name in ("ess0", "meter0"):
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"host {name}")},
            via_device_id=edge_device.id,
        )

    assert await async_migrate_entry(hass, entry)

    assert (entry.version, entry.minor_version) == (6, 5)
    assert entry.unique_id == "host"
    assert entry.options == _DEFAULT_OPTIONS
    disabled_by = {
        name: device_registry.async_get_device_by_identifier(
            (DOMAIN, f"host {name}"), entry.entry_id
        ).disabled_by
        for name in ("ess0", "meter0", "ctrlDebugLog0")
    }
    assert disabled_by == {
        "ess0": None,
        "meter0": dr.DeviceEntryDisabler.USER,
        "ctrlDebugLog0": dr.DeviceEntryDisabler.USER,
    }


async def test_migrate_v4_without_edge_device(hass: HomeAssistant) -> None:
    """Without an edge device no component devices are created, options are cleaned."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        minor_version=1,
        data=_ENTRY_DATA,
        options={
            CONF_COMPONENTS: {"ctrlDebugLog0": False},
            CONF_ADVANCED_OPTIONS: _ADVANCED_OPTIONS,
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 6
    assert entry.options == _DEFAULT_OPTIONS
    device_registry = dr.async_get(hass)
    assert not dr.async_entries_for_config_entry(device_registry, entry.entry_id)


async def test_migrate_v3_continues_to_v6(hass: HomeAssistant) -> None:
    """A version 3 entry is migrated in one go to version 6."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        minor_version=1,
        data=_ENTRY_DATA,
        options={"ctrlDebugLog0": False},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 6
    assert entry.options == _DEFAULT_OPTIONS


# ---------------------------------------------------------------------------
# async_remove_config_entry_device
# ---------------------------------------------------------------------------


def _make_device_entry(identifiers: set) -> MagicMock:
    device_entry = MagicMock()
    device_entry.identifiers = identifiers
    return device_entry


def _make_remove_device_entry(hostname: str, components: dict) -> MagicMock:
    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = hostname
    mock_backend.the_edge.components = components
    for name, component in components.items():
        component.unique_id = hostname + " " + name
    entry = MagicMock()
    entry.runtime_data.backend = mock_backend
    return entry


async def test_remove_device_foreign_domain_allows_removal(
    hass: HomeAssistant,
) -> None:
    """A device belonging to another integration must always be removable."""
    entry = _make_remove_device_entry("host", {})
    device_entry = _make_device_entry({("other_domain", "host")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True


async def test_remove_device_unexpected_identifier_shape_allows_removal(
    hass: HomeAssistant,
) -> None:
    """An identifier tuple that isn't (domain, value) must always be removable."""
    entry = _make_remove_device_entry("host", {})
    device_entry = _make_device_entry({(DOMAIN,)})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True


async def test_remove_device_edge_device_wrong_hostname_allows_removal(
    hass: HomeAssistant,
) -> None:
    """The main edge device is removable once its hostname no longer matches."""
    entry = _make_remove_device_entry("host", {})
    device_entry = _make_device_entry({(DOMAIN, "old-host")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True


async def test_remove_device_edge_device_matching_hostname_blocks_removal(
    hass: HomeAssistant,
) -> None:
    """The main edge device must not be removable while still active."""
    entry = _make_remove_device_entry("host", {})
    device_entry = _make_device_entry({(DOMAIN, "host")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is False


async def test_remove_device_component_wrong_hostname_allows_removal(
    hass: HomeAssistant,
) -> None:
    """A component device is removable if the hostname no longer matches."""
    entry = _make_remove_device_entry("host", {"comp1": MagicMock()})
    device_entry = _make_device_entry({(DOMAIN, "old-host comp1")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True


async def test_remove_device_component_not_present_allows_removal(
    hass: HomeAssistant,
) -> None:
    """A component device is removable once the component is gone from the edge."""
    entry = _make_remove_device_entry("host", {"comp1": MagicMock()})
    device_entry = _make_device_entry({(DOMAIN, "host comp2")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True


async def test_remove_device_component_still_present_blocks_removal(
    hass: HomeAssistant,
) -> None:
    """A component device must not be removable while still part of the edge."""
    entry = _make_remove_device_entry("host", {"comp1": MagicMock()})
    device_entry = _make_device_entry({(DOMAIN, "host comp1")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is False


async def test_remove_device_multi_edge_edge_device_blocks_removal(
    hass: HomeAssistant,
) -> None:
    """The main device of an edge in a multi edge setup must not be removable."""
    entry = _make_remove_device_entry("host edge0", {"comp1": MagicMock()})
    device_entry = _make_device_entry({(DOMAIN, "host edge0")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is False


async def test_remove_device_multi_edge_component_still_present_blocks_removal(
    hass: HomeAssistant,
) -> None:
    """A component device of a multi edge setup must not be removable while present."""
    entry = _make_remove_device_entry("host edge0", {"comp1": MagicMock()})
    device_entry = _make_device_entry({(DOMAIN, "host edge0 comp1")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is False


async def test_remove_device_multi_edge_component_not_present_allows_removal(
    hass: HomeAssistant,
) -> None:
    """A component device of a multi edge setup is removable once the component is gone."""
    entry = _make_remove_device_entry("host edge0", {"comp1": MagicMock()})
    device_entry = _make_device_entry({(DOMAIN, "host edge0 comp2")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True


async def test_remove_device_multi_edge_other_edge_allows_removal(
    hass: HomeAssistant,
) -> None:
    """Devices of another edge on the same host are removable from this entry."""
    entry = _make_remove_device_entry("host edge0", {"comp1": MagicMock()})

    for identifier in ("host edge1", "host edge1 comp1"):
        device_entry = _make_device_entry({(DOMAIN, identifier)})
        assert await async_remove_config_entry_device(hass, entry, device_entry) is True
