"""Test the HA OpenEMS config flow."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import (
    CONF_BASE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_TYPE,
    CONF_URL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openems.const import CONF_EDGE, CONF_MORE_OPTIONS, DOMAIN
from openems_client.entry_data import OpenEMSConfigReader
from openems_client.exceptions import (
    OpenEMSAuthError,
    OpenEMSConnectionError,
    OpenEMSError,
)

from .conftest import EDGE_HOSTNAME
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
        patch.object(OpenEMSConfigReader, "read_edges", return_value=_EDGES_RESPONSE),
        patch.object(OpenEMSConfigReader, "read_edge_config", return_value=_COMPONENTS),
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
    assert result["result"].unique_id == EDGE_HOSTNAME
    assert result["data"] == {
        CONF_HOST: "1.1.1.1",
        CONF_USERNAME: "test-username",
        CONF_PASSWORD: "test-password",
        CONF_TYPE: "direct_edge",
        CONF_URL: "",
        CONF_EDGE: "edge-1",
    }
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    ("failing_call", "error", "expected_errors"),
    [
        pytest.param(
            "connect_to_server",
            OpenEMSConnectionError("Connection refused"),
            {CONF_BASE: "cannot_connect"},
            id="connect_error",
        ),
        pytest.param(
            "login_to_server",
            OpenEMSAuthError("Authentication failed"),
            {CONF_BASE: "invalid_auth"},
            id="login_rejected",
        ),
        pytest.param(
            "login_to_server",
            OpenEMSConnectionError("Connection lost"),
            {CONF_BASE: "cannot_connect"},
            id="login_connection_error",
        ),
    ],
)
async def test_form_connection_errors(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    failing_call: str,
    error: Exception,
    expected_errors: dict[str, str],
) -> None:
    """Connection and login errors show translated errors, a retry creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    error_conn = make_mock_connection()
    setattr(error_conn, failing_call, AsyncMock(side_effect=error))
    with patch(
        "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
        return_value=error_conn,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == expected_errors

    conn_patch, edges_patch, components_patch = _success_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_cannot_read_edge(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """An error while reading the edge shows the cannot_read_components error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, _ = _success_patches()
    with (
        conn_patch,
        edges_patch,
        patch.object(
            OpenEMSConfigReader,
            "read_edge_config",
            side_effect=OpenEMSError("rejected"),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BASE: "cannot_read_components"}


async def test_form_invalid_host(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """A host the connection cannot be built for shows the invalid_host error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
        side_effect=ValueError("invalid host"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}


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
    stored = result["data"]
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
    assert result["errors"] == {CONF_MORE_OPTIONS: "invalid_url"}


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
    stored = result["data"]
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
        patch.object(OpenEMSConfigReader, "read_edges", return_value=_multi_edges),
        patch.object(OpenEMSConfigReader, "read_edge_config", return_value={}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT_WEB_FENECON
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edges"


async def test_reconfigure_updates_entry_and_clears_cached_configuration(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, hass_storage: dict[str, Any]
) -> None:
    """Reconfigure stores the new connection data and drops the cached configuration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=6,
        unique_id=EDGE_HOSTNAME,
        data={
            CONF_HOST: "2.2.2.2",
            CONF_USERNAME: "old-username",
            CONF_PASSWORD: "old-password",
            CONF_TYPE: "direct_edge",
            CONF_EDGE: "edge-1",
        },
    )
    entry.add_to_hass(hass)
    storage_key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[storage_key] = {
        "version": 1,
        "minor_version": 1,
        "key": storage_key,
        "data": {"comp1": {}},
    }

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM

    conn_patch, edges_patch, components_patch = _success_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "1.1.1.1"
    assert storage_key not in hass_storage


_REAUTH_ENTRY_DATA = {
    CONF_HOST: "1.1.1.1",
    CONF_USERNAME: "test-username",
    CONF_PASSWORD: "old-password",
    CONF_TYPE: "direct_edge",
    CONF_EDGE: "edge-1",
}


@pytest.mark.parametrize(
    ("login_error", "expected_error"),
    [
        pytest.param(
            OpenEMSAuthError("Authentication failed"), "invalid_auth", id="rejected"
        ),
        pytest.param(
            OpenEMSConnectionError("Connection lost"),
            "cannot_connect",
            id="no_connection",
        ),
    ],
)
async def test_reauth_shows_error_and_recovers(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    login_error: OpenEMSError,
    expected_error: str,
) -> None:
    """A failed login shows an error, valid credentials update the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=6, unique_id=EDGE_HOSTNAME, data=_REAUTH_ENTRY_DATA
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    error_conn = make_mock_connection()
    error_conn.login_to_server = AsyncMock(side_effect=login_error)
    with patch(
        "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
        return_value=error_conn,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "test-username", CONF_PASSWORD: "wrong-password"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BASE: expected_error}
    error_conn.stop.assert_awaited_once()

    with patch(
        "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
        return_value=make_mock_connection(),
    ) as connection_class:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "test-username", CONF_PASSWORD: "new-password"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {**_REAUTH_ENTRY_DATA, CONF_PASSWORD: "new-password"}
    # the login is validated with the stored connection data and the new credentials
    assert connection_class.call_args.args[0]["host"] == "1.1.1.1"
    assert connection_class.call_args.args[0]["password"] == "new-password"


async def test_form_aborts_for_configured_system(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """A system that is already configured, e.g. with another connection type, aborts."""
    MockConfigEntry(domain=DOMAIN, version=6, unique_id=EDGE_HOSTNAME).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    conn_patch, edges_patch, components_patch = _success_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not mock_setup_entry.mock_calls


async def test_reconfigure_aborts_for_other_system(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Reconfiguring an entry to a different system aborts and keeps the entry data."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=6, unique_id="fems2", data=_REAUTH_ENTRY_DATA
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    conn_patch, edges_patch, components_patch = _success_patches()
    with conn_patch, edges_patch, components_patch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data == _REAUTH_ENTRY_DATA


async def test_reauth_aborts_for_other_system(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Credentials of a different system abort the reauth and keep the entry data."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=6, unique_id="fems2", data=_REAUTH_ENTRY_DATA
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    with patch(
        "custom_components.openems.config_flow.OpenEMSWebSocketConnection",
        return_value=make_mock_connection(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "other-username", CONF_PASSWORD: "other-password"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data == _REAUTH_ENTRY_DATA
