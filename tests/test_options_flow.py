"""Tests for the OpenEMS options flow (component selection and advanced options)."""

from unittest.mock import MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.openems.const import (
    CONF_ADVANCED_OPTIONS, CONF_COMPONENTS, CONF_FORWARD_INTERVAL,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING, DOMAIN)
from custom_components.openems.entry_data import OpenEMSConfigReader

from .helpers_flow import make_mock_connection

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_DEFAULT_OPTIONS = {
    CONF_COMPONENTS: {"comp-a": True, "comp-b": False},
    CONF_ADVANCED_OPTIONS: {
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
        CONF_FORWARD_INTERVAL: 0,
    },
}

_EDGES_RESPONSE = {"edges": [{"id": "edge-1", "isOnline": True}]}

_FLOW_USER_INPUT = {
    "host": "192.0.2.1",
    "username": "user",
    "password": "password",
    "more_options": {"type": "direct_edge"},
}


def _setup_patches():
    mock_conn = make_mock_connection()
    return (
        patch(
            "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        patch.object(OpenEMSConfigReader, "read_edges",
                     return_value=_EDGES_RESPONSE),
        patch.object(OpenEMSConfigReader,
                     "read_edge_components", return_value={}),
    )


def _make_mock_backend(components: dict | None = None):
    """Return a minimal mock backend with configurable components."""
    if components is None:
        components = {
            "comp-a": MagicMock(create_entities=True),
            "comp-b": MagicMock(create_entities=False),
        }
    backend = MagicMock()
    backend.the_edge.components = components
    return backend


async def _create_entry(hass: HomeAssistant) -> config_entries.ConfigEntry:
    """Create a config entry via the user flow and return it with default options set."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    conn_patch, edges_patch, comp_patch = _setup_patches()
    with conn_patch, edges_patch, comp_patch:
        with patch("custom_components.openems.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], _FLOW_USER_INPUT
            )
            await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    # Inject the default options we want to test against
    hass.config_entries.async_update_entry(entry, options=_DEFAULT_OPTIONS)
    return entry


# ---------------------------------------------------------------------------
# Options flow init: menu
# ---------------------------------------------------------------------------


async def test_options_flow_shows_menu(hass: HomeAssistant) -> None:
    """Options flow init step must present the two-option menu."""
    entry = await _create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert "select_components" in result["menu_options"]
    assert "advanced_options" in result["menu_options"]


# ---------------------------------------------------------------------------
# select_components step
# ---------------------------------------------------------------------------


async def test_select_components_shows_form(hass: HomeAssistant) -> None:
    """Navigating to select_components must show a form with one boolean per component."""
    entry = await _create_entry(hass)
    backend = _make_mock_backend()
    entry.runtime_data = MagicMock(backend=backend)

    flow_id = (
        await hass.config_entries.options.async_init(entry.entry_id)
    )["flow_id"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "select_components"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_components"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert "comp-a" in schema_keys
    assert "comp-b" in schema_keys


async def test_select_components_enables_component(hass: HomeAssistant) -> None:
    """Enabling a previously disabled component must be persisted in options."""
    entry = await _create_entry(hass)
    backend = _make_mock_backend()
    entry.runtime_data = MagicMock(backend=backend)

    flow_id = (
        await hass.config_entries.options.async_init(entry.entry_id)
    )["flow_id"]
    await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "select_components"}
    )
    result = await hass.config_entries.options.async_configure(
        flow_id, {"comp-a": True, "comp-b": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_COMPONENTS] == {"comp-a": True, "comp-b": True}


async def test_select_components_disables_component(hass: HomeAssistant) -> None:
    """Disabling a previously enabled component must be persisted in options."""
    entry = await _create_entry(hass)
    backend = _make_mock_backend()
    entry.runtime_data = MagicMock(backend=backend)

    flow_id = (
        await hass.config_entries.options.async_init(entry.entry_id)
    )["flow_id"]
    await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "select_components"}
    )
    result = await hass.config_entries.options.async_configure(
        flow_id, {"comp-a": False, "comp-b": False}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_COMPONENTS] == {
        "comp-a": False, "comp-b": False}


async def test_select_components_preserves_advanced_options(hass: HomeAssistant) -> None:
    """Submitting component selection must not overwrite existing advanced options."""
    entry = await _create_entry(hass)
    backend = _make_mock_backend()
    entry.runtime_data = MagicMock(backend=backend)

    flow_id = (
        await hass.config_entries.options.async_init(entry.entry_id)
    )["flow_id"]
    await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "select_components"}
    )
    result = await hass.config_entries.options.async_configure(
        flow_id, {"comp-a": True, "comp-b": False}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADVANCED_OPTIONS] == _DEFAULT_OPTIONS[CONF_ADVANCED_OPTIONS]


# ---------------------------------------------------------------------------
# advanced_options step
# ---------------------------------------------------------------------------


async def test_advanced_options_shows_form(hass: HomeAssistant) -> None:
    """Navigating to advanced_options must show a form with the two known fields."""
    entry = await _create_entry(hass)

    flow_id = (
        await hass.config_entries.options.async_init(entry.entry_id)
    )["flow_id"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "advanced_options"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "advanced_options"
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING in schema_keys
    assert CONF_FORWARD_INTERVAL in schema_keys


async def test_advanced_options_saves_forward_interval(hass: HomeAssistant) -> None:
    """Changing the forward_interval must be stored in options."""
    entry = await _create_entry(hass)

    flow_id = (
        await hass.config_entries.options.async_init(entry.entry_id)
    )["flow_id"]
    await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "advanced_options"}
    )
    result = await hass.config_entries.options.async_configure(
        flow_id,
        {
            CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
            CONF_FORWARD_INTERVAL: 30,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADVANCED_OPTIONS][CONF_FORWARD_INTERVAL] == 30


async def test_advanced_options_saves_ignore_decreasing(hass: HomeAssistant) -> None:
    """Enabling ignore_decreasing_if_total_increasing must be stored in options."""
    entry = await _create_entry(hass)

    flow_id = (
        await hass.config_entries.options.async_init(entry.entry_id)
    )["flow_id"]
    await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "advanced_options"}
    )
    result = await hass.config_entries.options.async_configure(
        flow_id,
        {
            CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: True,
            CONF_FORWARD_INTERVAL: 0,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADVANCED_OPTIONS][CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING] is True


async def test_advanced_options_preserves_component_selection(hass: HomeAssistant) -> None:
    """Submitting advanced options must not overwrite existing component selections."""
    entry = await _create_entry(hass)

    flow_id = (
        await hass.config_entries.options.async_init(entry.entry_id)
    )["flow_id"]
    await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "advanced_options"}
    )
    result = await hass.config_entries.options.async_configure(
        flow_id,
        {
            CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
            CONF_FORWARD_INTERVAL: 0,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_COMPONENTS] == _DEFAULT_OPTIONS[CONF_COMPONENTS]
