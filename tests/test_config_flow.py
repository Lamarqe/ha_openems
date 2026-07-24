"""Test the HA OpenEMS config flow."""

from unittest.mock import AsyncMock, MagicMock, patch

import jsonrpc_base
import jsonrpc_base.jsonrpc
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.openems.const import DOMAIN
from custom_components.openems.entry_data import OpenEMSConfigReader

_USER_INPUT = {
    CONF_HOST: "1.1.1.1",
    CONF_USERNAME: "test-username",
    CONF_PASSWORD: "test-password",
    "more_options": {
        "type": "direct_edge",
    },
}

_LOGIN_RESPONSE_SINGLE = {"user": {"hasMultipleEdges": False}}
_EDGES_RESPONSE = {"edges": [{"id": "fems17289", "isOnline": True}]}
_COMPONENTS: dict = {}


def _make_mock_connection():
    """Return an AsyncMock connection that avoids creating a real aiohttp session."""
    mock_conn = MagicMock()
    mock_conn.connect_to_server = AsyncMock()
    mock_conn.login_to_server = AsyncMock(return_value=_LOGIN_RESPONSE_SINGLE)
    mock_conn.stop = AsyncMock()
    return mock_conn


def _success_patches():
    mock_conn = _make_mock_connection()
    return (
        patch(
            "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        patch.object(OpenEMSConfigReader, "read_edges",
                     return_value=_EDGES_RESPONSE),
        patch.object(OpenEMSConfigReader, "read_edge_components",
                     return_value=_COMPONENTS),
    )


async def test_form(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    """Test we get the form and can submit it successfully."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    conn_patch, edges_patch, components_patch = _success_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "1.1.1.1"
    assert result["data"]["user_input"][CONF_HOST] == "1.1.1.1"
    assert result["data"]["user_input"][CONF_USERNAME] == "test-username"
    assert result["data"]["user_input"][CONF_PASSWORD] == "test-password"
    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_invalid_auth(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Test we handle a login ProtocolError (wrong password)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    error_conn = _make_mock_connection()
    error_conn.login_to_server = AsyncMock(
        side_effect=jsonrpc_base.jsonrpc.ProtocolError(401, "Unauthorized")
    )
    with patch(
        "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
        return_value=error_conn,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert CONF_PASSWORD in result["errors"]

    # Recover: submit again with correct credentials
    conn_patch, edges_patch, components_patch = _success_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_cannot_connect(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Test we handle a TransportError (host unreachable)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    error_conn = _make_mock_connection()
    error_conn.connect_to_server = AsyncMock(
        side_effect=jsonrpc_base.TransportError(
            "Connection refused", None, "errno 111")
    )
    with patch(
        "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
        return_value=error_conn,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert CONF_HOST in result["errors"]

    # Recover: submit again when host is reachable
    conn_patch, edges_patch, components_patch = _success_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(mock_setup_entry.mock_calls) == 1
