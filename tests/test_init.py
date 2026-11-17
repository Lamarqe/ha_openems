"""Tests for config entry setup, migration and options update in __init__.py."""

from openems_client.const import (
    CONF_ADVANCED_OPTIONS,
    CONF_COMPONENTS,
    CONF_FORWARD_INTERVAL,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
)
from unittest.mock import AsyncMock, MagicMock, patch

from jsonrpc_base.jsonrpc import ProtocolError, TransportError
import pytest

from custom_components.openems import (
    async_migrate_entry,
    async_remove_config_entry_device,
    async_setup_entry,
    async_unload_entry,
    update_config,
)
from custom_components.openems.const import DOMAIN
from openems_client.entry_data import OpenEMSConfigReader
from custom_components.openems.helpers_ha import RuntimeData
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
            "custom_components.openems.OpenEMSWebSocketConnection",
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
            "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        patch.object(OpenEMSConfigReader, "read_edges", return_value=_EDGES_RESPONSE),
        patch.object(OpenEMSConfigReader, "read_edge_components", return_value={}),
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
        backend=placeholder_backend, edge_device=MagicMock()
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
        patch("custom_components.openems.OpenEMSBackend", return_value=mock_backend),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert entry.runtime_data.backend is mock_backend
    mock_backend.start.assert_called_once()


async def test_setup_entry_transport_error_raises_not_ready(
    hass: HomeAssistant,
) -> None:
    """A TransportError on connect must surface as ConfigEntryNotReady."""
    entry = await _create_real_entry(hass)

    mock_conn = MagicMock()
    mock_conn.connect_to_server = AsyncMock(
        side_effect=TransportError("connection refused")
    )
    mock_conn.stop = AsyncMock()
    mock_conn.conn_url.host = "192.0.2.1"

    with (
        patch(
            "custom_components.openems.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)


async def test_setup_entry_transport_error_on_login_raises_not_ready(
    hass: HomeAssistant,
) -> None:
    """A TransportError during login (e.g. connection lost) raises ConfigEntryNotReady."""
    entry = await _create_real_entry(hass)

    mock_conn = MagicMock()
    mock_conn.connect_to_server = AsyncMock()
    mock_conn.login_to_server = AsyncMock(
        side_effect=TransportError("Connection closed before a response was received")
    )
    mock_conn.stop = AsyncMock()
    mock_conn.conn_url.host = "192.0.2.1"

    with (
        patch(
            "custom_components.openems.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)
    mock_conn.stop.assert_awaited_once()


async def test_setup_entry_auth_error_code_1003_raises_auth_failed(
    hass: HomeAssistant,
) -> None:
    """ProtocolError code 1003 (OpenEMS authentication failure) raises ConfigEntryAuthFailed."""
    entry = await _create_real_entry(hass)

    mock_conn = MagicMock()
    mock_conn.connect_to_server = AsyncMock()
    mock_conn.login_to_server = AsyncMock(
        side_effect=ProtocolError(1003, "Authentication failed", [])
    )
    mock_conn.stop = AsyncMock()
    mock_conn.conn_url.host = "192.0.2.1"

    with (
        patch(
            "custom_components.openems.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, entry)


async def test_setup_entry_other_protocol_error_raises_not_ready(
    hass: HomeAssistant,
) -> None:
    """A ProtocolError with any code other than 1003 is treated as a transient error."""
    entry = await _create_real_entry(hass)

    mock_conn = MagicMock()
    mock_conn.connect_to_server = AsyncMock()
    mock_conn.login_to_server = AsyncMock(
        side_effect=ProtocolError(500, "Internal server error", [])
    )
    mock_conn.stop = AsyncMock()
    mock_conn.conn_url.host = "192.0.2.1"

    with (
        patch(
            "custom_components.openems.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)


# ---------------------------------------------------------------------------
# async_unload_entry
# ---------------------------------------------------------------------------


async def test_unload_entry_stops_backend(hass: HomeAssistant) -> None:
    """async_unload_entry must stop the backend and unload all platforms."""
    entry = await _create_real_entry(hass)

    mock_backend = MagicMock()
    mock_backend.stop = AsyncMock()
    entry.runtime_data = RuntimeData(backend=mock_backend, edge_device=MagicMock())

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        result = await async_unload_entry(hass, entry)

    mock_backend.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# update_config
# ---------------------------------------------------------------------------


async def test_update_config_applies_advanced_options(hass: HomeAssistant) -> None:
    """update_config must propagate advanced options to the backend edge."""
    entry = await _create_real_entry(hass)

    mock_backend = MagicMock()
    mock_backend.the_edge.components = {}
    entry.runtime_data = RuntimeData(backend=mock_backend, edge_device=MagicMock())

    new_advanced = {
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: True,
        CONF_FORWARD_INTERVAL: 10,
    }
    hass.config_entries.async_update_entry(
        entry,
        options={CONF_ADVANCED_OPTIONS: new_advanced},
    )

    await update_config(hass, entry)

    mock_backend.the_edge.set_advanced_options.assert_called_once_with(new_advanced)


async def test_update_config_no_options_returns_early(hass: HomeAssistant) -> None:
    """update_config must be a no-op when the entry has no options."""
    entry = await _create_real_entry(hass)

    mock_backend = MagicMock()
    entry.runtime_data = RuntimeData(backend=mock_backend, edge_device=MagicMock())
    hass.config_entries.async_update_entry(entry, options={})

    await update_config(hass, entry)

    mock_backend.the_edge.set_advanced_options.assert_not_called()


# ---------------------------------------------------------------------------
# async_setup_entry - component devices
# ---------------------------------------------------------------------------


def _make_component(name: str) -> MagicMock:
    """Return a mock component of the edge with hostname "host"."""
    component = MagicMock()
    component.name = name
    component.alias = name
    component.edge.hostname = "host"
    component.create_entities = False
    return component


async def _setup_with_components(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    components: dict[str, MagicMock],
) -> None:
    """Run async_setup_entry with a mock backend containing the given components."""
    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = components

    conn_patch, _ = _make_conn_patches()
    with (
        conn_patch,
        patch.object(OpenEMSConfigReader, "read_edge_components", return_value={}),
        patch("custom_components.openems.OpenEMSBackend", return_value=mock_backend),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    ):
        await async_setup_entry(hass, entry)


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
    assert components["ess0"].create_entities is True
    assert components["ctrlDebugLog0"].create_entities is False


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

    assert components["ctrlDebugLog0"].create_entities is True


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

    assert components["ess0"].create_entities is False
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

    assert (entry.version, entry.minor_version) == (5, 1)
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

    assert entry.version == 5
    assert entry.options == _DEFAULT_OPTIONS
    device_registry = dr.async_get(hass)
    assert not dr.async_entries_for_config_entry(device_registry, entry.entry_id)


async def test_migrate_v3_continues_to_v5(hass: HomeAssistant) -> None:
    """A version 3 entry is migrated in one go to version 5."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        minor_version=1,
        data=_ENTRY_DATA,
        options={"ctrlDebugLog0": False},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 5
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
