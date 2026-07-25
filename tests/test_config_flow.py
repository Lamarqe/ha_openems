"""Test the HA OpenEMS config flow."""

from unittest.mock import AsyncMock, patch

import jsonrpc_base
import jsonrpc_base.jsonrpc
from homeassistant import config_entries
from homeassistant.const import (CONF_HOST, CONF_PASSWORD, CONF_URL,
                                 CONF_USERNAME)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.openems.const import CONF_MORE_OPTIONS, DOMAIN
from custom_components.openems.entry_data import OpenEMSConfigReader

from .helpers_flow import make_mock_connection

_USER_INPUT = {
    CONF_HOST: "1.1.1.1",
    CONF_USERNAME: "test-username",
    CONF_PASSWORD: "test-password",
    "more_options": {
        "type": "direct_edge",
    },
}

_EDGES_RESPONSE = {"edges": [{"id": "edge-1", "isOnline": True}]}
_COMPONENTS: dict = {}


def _success_patches(conn_url: str | None = None):
    from yarl import URL

    mock_conn = make_mock_connection()
    if conn_url:
        mock_conn.conn_url = URL(conn_url)
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

    error_conn = make_mock_connection()
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

    error_conn = make_mock_connection()
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


_USER_INPUT_CUSTOM_URL = {
    CONF_USERNAME: "user",
    CONF_PASSWORD: "password",
    "more_options": {
        "type": "custom_url",
        CONF_URL: "ws://myserver.example.com:8085/openems-backend-ui",
    },
}


async def test_custom_url_empty_url_shows_error(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Submitting custom_url type with a blank URL must show a validation error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "user",
            CONF_PASSWORD: "password",
            "more_options": {"type": "custom_url", CONF_URL: "   "},
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert CONF_MORE_OPTIONS in result["errors"]
    assert result["errors"][CONF_MORE_OPTIONS] == "custom_url_missing"


async def test_custom_url_relative_url_shows_error(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Submitting a relative (non-absolute) custom URL must show a validation error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "user",
            CONF_PASSWORD: "password",
            "more_options": {"type": "custom_url", CONF_URL: "relative/path"},
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert CONF_MORE_OPTIONS in result["errors"]
    assert result["errors"][CONF_MORE_OPTIONS] == "url_not_absolute"


async def test_custom_url_creates_entry(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """A valid absolute custom URL must create an entry with URL stored and host absent."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _success_patches(
        conn_url="ws://myserver.example.com:8085/openems-backend-ui"
    )
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT_CUSTOM_URL
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    stored = result["data"]["user_input"]
    assert stored[CONF_URL] == "ws://myserver.example.com:8085/openems-backend-ui"
    # host is not provided for custom_url; it is absent or empty in stored data
    assert not stored.get(CONF_HOST)


async def test_custom_url_title_uses_url_host(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Entry title for a custom URL connection must be the URL hostname."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _success_patches(
        conn_url="ws://myserver.example.com:8085/openems-backend-ui"
    )
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT_CUSTOM_URL
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "myserver.example.com"


async def test_custom_url_invalid_url_shows_error(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """A malformed URL that raises ValueError must show an invalid_url error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "user",
            CONF_PASSWORD: "password",
            "more_options": {"type": "custom_url", CONF_URL: "http://[invalid"},
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"].get(CONF_MORE_OPTIONS) == "invalid_url"
    assert "base" in result["errors"]


# ---------------------------------------------------------------------------
# Fenecon Web connection type
# ---------------------------------------------------------------------------

_USER_INPUT_WEB_FENECON = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "password",
    "more_options": {"type": "web_fenecon"},
}


async def test_web_fenecon_creates_entry(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """web_fenecon type must create an entry without a host field stored."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _success_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT_WEB_FENECON
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    stored = result["data"]["user_input"]
    assert stored["type"] == "web_fenecon"
    assert not stored.get(CONF_HOST)


async def test_web_fenecon_title_uses_username(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Entry title for web_fenecon must be 'FEMS Web: <username>'."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _success_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT_WEB_FENECON
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "FEMS Web: user@example.com"


async def test_web_fenecon_multi_edge_shows_edge_selection(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """web_fenecon with multi-edge login must show the edge-selection form."""
    from .helpers_flow import make_mock_connection

    _multi_login = {"user": {"hasMultipleEdges": True}}
    _multi_edges = {
        "edges": [
            {"id": "edge-online-1", "isOnline": True},
            {"id": "edge-online-2", "isOnline": True},
        ]
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    mock_conn = make_mock_connection(_multi_login)
    with (
        patch(
            "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
            return_value=mock_conn,
        ),
        patch.object(OpenEMSConfigReader, "read_edges",
                     return_value=_multi_edges),
        patch.object(OpenEMSConfigReader,
                     "read_edge_components", return_value={}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT_WEB_FENECON
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edges"
