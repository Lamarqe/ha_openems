"""Config flow tests for multi-edge scenarios and edge selection."""

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.openems.const import CONF_EDGE, CONF_EDGES, DOMAIN
from openems_client.connection import OpenEMSConfigReader
from openems_client.exceptions import OpenEMSConnectionError
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from .conftest import EDGE_HOSTNAME
from .helpers_flow import make_mock_connection

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


def _multi_edge_patches(login_response: dict | None = None):
    """Return patches for a successful multi-edge connection."""
    if login_response is None:
        login_response = _LOGIN_RESPONSE_MULTI
    mock_conn = make_mock_connection(login_response)
    return (
        patch(
            "openems_client.client.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        patch.object(OpenEMSConfigReader, "read_edges", return_value=_EDGES_RESPONSE),
        patch.object(OpenEMSConfigReader, "read_edge_config", return_value=_COMPONENTS),
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

    # probatio rejects offline ids because they are not in the SelectSelector options
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
    assert result["data"][CONF_EDGE] == "edge-online-1"
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
    assert result["data"][CONF_EDGE] == "edge-online-2"
    # multi edge backends append the edge ID, like the edge device identifier
    assert result["result"].unique_id == f"{EDGE_HOSTNAME} edge-online-2"


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
    error_conn = make_mock_connection(_LOGIN_RESPONSE_MULTI)
    error_conn.connect_to_server = AsyncMock(
        side_effect=OpenEMSConnectionError("connect failed")
    )
    with patch(
        "openems_client.client.OpenEMSWebSocketConnection",
        return_value=error_conn,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EDGES: "edge-online-1"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edges"
    assert result["errors"] == {CONF_EDGES: "error_processing_edge"}
    # the form offers the online edges again
    edges_selector = next(
        value for key, value in result["data_schema"].schema.items() if key == CONF_EDGES
    )
    assert edges_selector.config["options"] == ["edge-online-1", "edge-online-2"]


async def test_multi_edge_without_online_edges(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """A backend without online edges shows an error instead of an empty selection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, _, components_patch = _multi_edge_patches()
    offline_edges = {"edges": [{"id": "edge-offline-1", "isOnline": False}]}
    with (
        conn_patch,
        patch.object(OpenEMSConfigReader, "read_edges", return_value=offline_edges),
        components_patch,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_edges"}
