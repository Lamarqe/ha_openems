"""Tests for the OpenEMS config flow and options flow."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from openems_client.client import EdgeInfo
from openems_client.exceptions import (
    OpenEMSAuthError,
    OpenEMSConnectionError,
    OpenEMSError,
)
import pytest

from custom_components.openems.const import (
    CONF_ADVANCED_OPTIONS,
    CONF_EDGE,
    CONF_EDGES,
    CONF_FORWARD_INTERVAL,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
    CONF_MORE_OPTIONS,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
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
from pytest_homeassistant.common import MockConfigEntry

from .conftest import (
    EDGE_ID,
    ENTRY_DATA,
    ENTRY_OPTIONS,
    HOST,
    HOSTNAME,
    store_edge_config,
)

USER_INPUT = {
    CONF_HOST: HOST,
    CONF_USERNAME: "x",
    CONF_PASSWORD: "user",
    CONF_MORE_OPTIONS: {CONF_TYPE: "direct_edge"},
}
WEB_FENECON_INPUT = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "password",
    CONF_MORE_OPTIONS: {CONF_TYPE: "web_fenecon"},
}


async def _start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


@pytest.mark.usefixtures("mock_openems_client")
async def test_user_flow(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    """Test creating an entry for a directly connected edge."""
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["result"].unique_id == HOSTNAME
    assert result["data"] == ENTRY_DATA
    assert result["options"] == ENTRY_OPTIONS
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    ("method", "error", "errors"),
    [
        pytest.param(
            "connect",
            OpenEMSAuthError("rejected"),
            {CONF_BASE: "invalid_auth"},
            id="login_rejected",
        ),
        pytest.param(
            "connect",
            OpenEMSConnectionError("refused"),
            {CONF_BASE: "cannot_connect"},
            id="unreachable",
        ),
        pytest.param(
            "connect",
            OpenEMSError("internal"),
            {CONF_BASE: "cannot_connect"},
            id="login_error",
        ),
        pytest.param(
            "read_edges",
            OpenEMSError("internal"),
            {CONF_BASE: "cannot_read_components"},
            id="edges_error",
        ),
        pytest.param(
            "read_edges",
            RuntimeError("bug"),
            {CONF_BASE: "unknown"},
            id="edges_unexpected",
        ),
        pytest.param(
            "read_edge_hostname",
            OpenEMSError("internal"),
            {CONF_BASE: "cannot_read_components"},
            id="hostname_error",
        ),
        pytest.param(
            "read_edge_hostname",
            RuntimeError("bug"),
            {CONF_BASE: "unknown"},
            id="hostname_unexpected",
        ),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_openems_client: MagicMock,
    method: str,
    error: Exception,
    errors: dict[str, str],
) -> None:
    """Errors are shown in the form, the flow recovers after fixing them."""
    result = await _start_user_flow(hass)
    getattr(mock_openems_client, method).side_effect = error

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    getattr(mock_openems_client, method).side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("user_input", "errors"),
    [
        pytest.param(
            {**USER_INPUT, CONF_HOST: " "},
            {CONF_HOST: "host_missing"},
            id="host_missing",
        ),
        pytest.param(
            {
                **USER_INPUT,
                CONF_MORE_OPTIONS: {CONF_TYPE: "custom_url", CONF_URL: "   "},
            },
            {CONF_MORE_OPTIONS: "custom_url_missing"},
            id="url_missing",
        ),
        pytest.param(
            {
                **USER_INPUT,
                CONF_MORE_OPTIONS: {
                    CONF_TYPE: "custom_url",
                    CONF_URL: "http://[invalid",
                },
            },
            {CONF_MORE_OPTIONS: "invalid_url"},
            id="invalid_url",
        ),
        pytest.param(
            {
                **USER_INPUT,
                CONF_MORE_OPTIONS: {CONF_TYPE: "custom_url", CONF_URL: "relative/path"},
            },
            {CONF_MORE_OPTIONS: "url_not_absolute"},
            id="relative_url",
        ),
    ],
)
@pytest.mark.usefixtures("mock_openems_client", "mock_setup_entry")
async def test_user_flow_invalid_input(
    hass: HomeAssistant, user_input: dict[str, Any], errors: dict[str, str]
) -> None:
    """Invalid connection data is rejected, the flow recovers with valid data."""
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow_invalid_host(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """A host the client rejects is shown as invalid."""
    result = await _start_user_flow(hass)
    mock_openems_client.init_error = ValueError("invalid host")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["errors"] == {CONF_HOST: "invalid_host"}

    mock_openems_client.init_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow_without_edges(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """An account without edges cannot be added."""
    result = await _start_user_flow(hass)
    mock_openems_client.read_edges.return_value = []

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["errors"] == {CONF_BASE: "no_edges"}

    mock_openems_client.read_edges.return_value = [EdgeInfo(id=EDGE_ID, online=True)]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow_custom_url(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """A custom URL is stored without the host, the title is the host of the URL."""
    url = "ws://myserver.example.com:8085/openems-backend-ui"
    mock_openems_client.host = "myserver.example.com"
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**USER_INPUT, CONF_MORE_OPTIONS: {CONF_TYPE: "custom_url", CONF_URL: url}},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "myserver.example.com"
    assert result["data"] == {
        CONF_USERNAME: "x",
        CONF_PASSWORD: "user",
        CONF_TYPE: "custom_url",
        CONF_URL: url,
        CONF_EDGE: EDGE_ID,
    }


@pytest.mark.parametrize(
    ("hostname", "existing_host", "reason", "placeholders"),
    [
        pytest.param(
            HOSTNAME, "192.0.2.9", "already_configured", None, id="fems_number"
        ),
        pytest.param("openems", HOST, "already_configured", None, id="same_address"),
        pytest.param(
            "openems",
            "192.0.2.9",
            "hostname_in_use",
            {"hostname": "openems", "title": "192.0.2.9"},
            id="other_address",
        ),
    ],
)
async def test_user_flow_already_configured(
    hass: HomeAssistant,
    mock_openems_client: MagicMock,
    hostname: str,
    existing_host: str,
    reason: str,
    placeholders: dict[str, str] | None,
) -> None:
    """A system cannot be added twice, a hostname at another address gets a hint."""
    MockConfigEntry(
        domain=DOMAIN,
        title=existing_host,
        unique_id=hostname,
        data={**ENTRY_DATA, CONF_HOST: existing_host},
    ).add_to_hass(hass)
    mock_openems_client.read_edge_hostname.return_value = hostname
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert result["description_placeholders"] == placeholders


@pytest.mark.parametrize("multi_edge", [True])
@pytest.mark.usefixtures("mock_setup_entry")
async def test_multi_edge_flow(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """Accounts with several edges select one of their online edges."""
    mock_openems_client.read_edges.return_value = [
        EdgeInfo(id="0", online=True),
        EdgeInfo(id="1", online=False),
        EdgeInfo(id="2", online=True),
    ]
    mock_openems_client.read_edge_hostname.return_value = f"{HOSTNAME} 2"
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], WEB_FENECON_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "edges"
    edges = next(
        value
        for key, value in result["data_schema"].schema.items()
        if key == CONF_EDGES
    )
    assert edges.config["options"] == ["0", "2"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EDGES: "2"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "FEMS Web: user@example.com 2"
    assert result["result"].unique_id == f"{HOSTNAME} 2"
    assert result["data"] == {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "password",
        CONF_TYPE: "web_fenecon",
        CONF_EDGE: "2",
    }


@pytest.mark.parametrize("multi_edge", [True])
@pytest.mark.parametrize(
    ("error", "errors"),
    [
        pytest.param(
            OpenEMSError("internal"), {CONF_EDGES: "error_processing_edge"}, id="error"
        ),
        pytest.param(RuntimeError("bug"), {CONF_BASE: "unknown"}, id="unexpected"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_multi_edge_flow_errors(
    hass: HomeAssistant,
    mock_openems_client: MagicMock,
    error: Exception,
    errors: dict[str, str],
) -> None:
    """Errors reading the selected edge are shown, the flow recovers."""
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], WEB_FENECON_INPUT
    )
    mock_openems_client.read_edge_hostname.side_effect = error

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EDGES: EDGE_ID}
    )

    assert result["step_id"] == "edges"
    assert result["errors"] == errors

    mock_openems_client.read_edge_hostname.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EDGES: EDGE_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("multi_edge", [True])
@pytest.mark.usefixtures("mock_setup_entry")
async def test_multi_edge_flow_without_online_edges(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """An account without online edges cannot be added."""
    mock_openems_client.read_edges.return_value = [EdgeInfo(id="0", online=False)]
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], WEB_FENECON_INPUT
    )

    assert result["errors"] == {CONF_BASE: "no_edges"}

    mock_openems_client.read_edges.return_value = [EdgeInfo(id="0", online=True)]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], WEB_FENECON_INPUT
    )
    assert result["step_id"] == "edges"


@pytest.mark.usefixtures("mock_openems_client", "mock_setup_entry")
async def test_reconfigure(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_config_entry: MockConfigEntry,
    edge_config: dict[str, Any],
) -> None:
    """Reconfiguring stores the new connection data and drops the cached configuration."""
    mock_config_entry.add_to_hass(hass)
    store_edge_config(hass_storage, mock_config_entry, edge_config)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: "192.0.2.2"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == "192.0.2.2"
    assert f"{DOMAIN}.{mock_config_entry.entry_id}" not in hass_storage


async def test_reconfigure_other_system(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """Reconfiguring cannot switch the entry to another system."""
    mock_config_entry.add_to_hass(hass)
    mock_openems_client.read_edge_hostname.return_value = "fems54321"
    result = await mock_config_entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """Reauthentication stores the new credentials."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "owner", CONF_PASSWORD: "new"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == {
        **ENTRY_DATA,
        CONF_USERNAME: "owner",
        CONF_PASSWORD: "new",
    }
    assert mock_openems_client.conn_props["password"] == "new"


@pytest.mark.parametrize(
    ("error", "errors"),
    [
        pytest.param(
            OpenEMSAuthError("rejected"), {CONF_BASE: "invalid_auth"}, id="rejected"
        ),
        pytest.param(
            OpenEMSError("internal"), {CONF_BASE: "cannot_connect"}, id="error"
        ),
        pytest.param(RuntimeError("bug"), {CONF_BASE: "unknown"}, id="unexpected"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_reauth_errors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
    error: Exception,
    errors: dict[str, str],
) -> None:
    """Reauthentication errors are shown, the flow recovers."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    mock_openems_client.connect.side_effect = error

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "x", CONF_PASSWORD: "wrong"}
    )

    assert result["errors"] == errors

    mock_openems_client.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "x", CONF_PASSWORD: "new"}
    )
    assert result["reason"] == "reauth_successful"


async def test_reauth_other_system(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """Credentials of another system are rejected."""
    mock_config_entry.add_to_hass(hass)
    mock_openems_client.read_edge_hostname.return_value = "fems54321"
    result = await mock_config_entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "other", CONF_PASSWORD: "other"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_options_flow(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saved advanced options are stored and reload the entry."""
    mock_config_entry.add_to_hass(hass)
    user_input = {
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: True,
        CONF_FORWARD_INTERVAL: 10,
    }
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "advanced_options"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {CONF_ADVANCED_OPTIONS: user_input}
