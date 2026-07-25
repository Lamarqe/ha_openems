"""Config flow tests for multi-edge scenarios and edge selection."""

from unittest.mock import AsyncMock, MagicMock, patch

import jsonrpc_base
import jsonrpc_base.jsonrpc
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.openems.const import CONF_EDGE, CONF_EDGES, DOMAIN
from custom_components.openems.entry_data import OpenEMSConfigReader

_LOGIN_RESPONSE_MULTI: dict = {"user": {"hasMultipleEdges": True}}

_EDGES_RESPONSE: dict = {
    "edges": [
        {"id": "edge-online-1", "isOnline": True},
        {"id": "edge-online-2", "isOnline": True},
        {"id": "edge-offline-1", "isOnline": False},
    ]
}

_USER_INPUT = {
    CONF_HOST: "192.0.2.1",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "password",
    "more_options": {"type": "direct_edge"},
}
_COMPONENTS: dict = {}


def _make_mock_connection(login_response: dict):
    """Return an AsyncMock connection with the given login response."""
    mock_conn = MagicMock()
    mock_conn.connect_to_server = AsyncMock()
    mock_conn.login_to_server = AsyncMock(return_value=login_response)
    mock_conn.stop = AsyncMock()
    return mock_conn


def _multi_edge_patches(login_response: dict | None = None):
    """Return patches for a successful multi-edge connection."""
    if login_response is None:
        login_response = _LOGIN_RESPONSE_MULTI
    mock_conn = _make_mock_connection(login_response)
    return (
        patch(
            "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        patch.object(OpenEMSConfigReader, "read_edges",
                     return_value=_EDGES_RESPONSE),
        patch.object(
            OpenEMSConfigReader, "read_edge_components", return_value=_COMPONENTS
        ),
    )


async def test_multi_edge_flow_shows_edge_selection_form(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Multi-edge login must present an edge-selection form as the next step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _multi_edge_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edges"


async def test_multi_edge_form_only_lists_online_edges(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Submitting an offline edge id must be rejected by the edges form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _multi_edge_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edges"

    # voluptuous rejects offline ids because they are not in the SelectSelector options
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EDGES: "edge-offline-1"}
        )


async def test_multi_edge_selecting_edge_creates_entry(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Selecting an edge in the edges step must create a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _multi_edge_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        assert result["step_id"] == "edges"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EDGES: "edge-online-1"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["user_input"][CONF_EDGE] == "edge-online-1"
    # Title must include the edge id for multi-edge setups
    assert "edge-online-1" in result["title"]
    assert len(mock_setup_entry.mock_calls) == 1


async def test_multi_edge_entry_stores_selected_edge_id(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """The created config entry must persist the chosen edge id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _multi_edge_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EDGES: "edge-online-2"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["user_input"][CONF_EDGE] == "edge-online-2"


async def test_multi_edge_edges_step_error_on_connection_failure(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """A transport error during edge-step processing must show an error on the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _multi_edge_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        assert result["step_id"] == "edges"

    # Simulate a connection failure when the user confirms edge selection
    error_conn = _make_mock_connection(_LOGIN_RESPONSE_MULTI)
    error_conn.connect_to_server = AsyncMock(
        side_effect=jsonrpc_base.TransportError("connect failed")
    )
    with patch(
        "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
        return_value=error_conn,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EDGES: "edge-online-1"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edges"
    assert CONF_EDGES in result["errors"]
