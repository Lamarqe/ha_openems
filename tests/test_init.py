"""Tests for config entry setup and options update in __init__.py."""

from unittest.mock import AsyncMock, MagicMock, patch

from jsonrpc_base.jsonrpc import ProtocolError, TransportError
import pytest

from custom_components.openems import (
    async_remove_config_entry_device,
    async_setup_entry,
    async_unload_entry,
    update_config,
)
from custom_components.openems.const import (
    CONF_ADVANCED_OPTIONS,
    CONF_COMPONENTS,
    CONF_FORWARD_INTERVAL,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
    DOMAIN,
)
from custom_components.openems.entry_data import OpenEMSConfigReader
from custom_components.openems.helpers_ha import RuntimeData
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

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

_DEFAULT_OPTIONS = {
    CONF_COMPONENTS: {"comp1": True},
    CONF_ADVANCED_OPTIONS: {
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
        CONF_FORWARD_INTERVAL: 0,
    },
}

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
    return entry


# ---------------------------------------------------------------------------
# async_setup_entry - happy path
# ---------------------------------------------------------------------------


async def test_setup_entry_success(hass: HomeAssistant) -> None:
    """A valid connection and login results in a fully set-up entry."""
    entry = await _create_real_entry(hass)

    mock_backend = MagicMock()
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

    assert result is True
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
    entry.runtime_data = RuntimeData(backend=mock_backend)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        result = await async_unload_entry(hass, entry)

    assert result is True
    mock_backend.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# update_config
# ---------------------------------------------------------------------------


async def test_update_config_applies_advanced_options(hass: HomeAssistant) -> None:
    """update_config must propagate advanced options to the backend edge."""
    entry = await _create_real_entry(hass)

    mock_backend = MagicMock()
    mock_backend.the_edge.components = {}
    entry.runtime_data = RuntimeData(backend=mock_backend)

    new_advanced = {
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: True,
        CONF_FORWARD_INTERVAL: 10,
    }
    hass.config_entries.async_update_entry(
        entry,
        options={CONF_COMPONENTS: {}, CONF_ADVANCED_OPTIONS: new_advanced},
    )

    await update_config(hass, entry)

    mock_backend.the_edge.set_advanced_options.assert_called_once_with(new_advanced)


async def test_update_config_disables_component_removes_entities(
    hass: HomeAssistant,
) -> None:
    """When a component is disabled in options its device and entities are removed."""
    entry = await _create_real_entry(hass)

    mock_component = MagicMock()
    mock_component.name = "comp1"
    mock_component.create_entities = True
    mock_component.edge.hostname = "host"
    mock_component.alias = "Component 1"

    mock_backend = MagicMock()
    mock_backend.the_edge.components = {"comp1": mock_component}
    entry.runtime_data = RuntimeData(backend=mock_backend)

    # Build a mock device registry that finds a device and a mock entity registry with no entities
    mock_device = MagicMock()
    mock_device.id = "device-id-1"
    mock_device_registry = MagicMock()
    mock_device_registry.async_get_device.return_value = mock_device
    mock_entity_registry = MagicMock()
    mock_entity_registry.async_entries_for_device = MagicMock(return_value=[])

    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_COMPONENTS: {"comp1": False},
            CONF_ADVANCED_OPTIONS: {
                CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
                CONF_FORWARD_INTERVAL: 0,
            },
        },
    )

    with (
        patch(
            "custom_components.openems.dr.async_get", return_value=mock_device_registry
        ),
        patch(
            "custom_components.openems.er.async_get", return_value=mock_entity_registry
        ),
        patch(
            "custom_components.openems.er.async_entries_for_device",
            return_value=[],
        ),
    ):
        await update_config(hass, entry)

    mock_device_registry.async_remove_device.assert_called_once_with("device-id-1")
    assert mock_component.create_entities is False


async def test_update_config_enables_component_calls_callbacks(
    hass: HomeAssistant,
) -> None:
    """When a component is newly enabled, the add_component_callbacks are invoked."""
    entry = await _create_real_entry(hass)

    mock_component = MagicMock()
    mock_component.name = "comp1"
    mock_component.create_entities = False  # currently disabled

    callback = MagicMock()
    mock_backend = MagicMock()
    mock_backend.the_edge.components = {"comp1": mock_component}
    entry.runtime_data = RuntimeData(backend=mock_backend)
    entry.runtime_data.add_component_callbacks = {"sensor": callback}

    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_COMPONENTS: {"comp1": True},
            CONF_ADVANCED_OPTIONS: {
                CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
                CONF_FORWARD_INTERVAL: 0,
            },
        },
    )

    await update_config(hass, entry)

    callback.assert_called_once_with(mock_component)
    assert mock_component.create_entities is True


async def test_update_config_no_options_returns_early(hass: HomeAssistant) -> None:
    """update_config must be a no-op when the entry has no options."""
    entry = await _create_real_entry(hass)

    mock_backend = MagicMock()
    entry.runtime_data = RuntimeData(backend=mock_backend)
    hass.config_entries.async_update_entry(entry, options={})

    await update_config(hass, entry)

    mock_backend.the_edge.set_advanced_options.assert_not_called()


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
