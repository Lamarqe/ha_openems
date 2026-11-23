"""Common fixtures for the OpenEMS tests."""

from collections.abc import AsyncGenerator, Generator
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from openems_client.client import EdgeInfo, LoginInfo
from openems_client.edge_config import EdgeConfigChange
from openems_client.openems import OpenEMSBackend
import pytest
from yarl import URL

from custom_components.openems import _PLATFORMS
from custom_components.openems.const import (
    CONF_ADVANCED_OPTIONS,
    CONF_EDGE,
    CONF_FORWARD_INTERVAL,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
    DOMAIN,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_TYPE,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry, load_json_object_fixture

HOSTNAME = "fems12345"
EDGE_ID = "0"
HOST = "192.0.2.1"
INSTALLED_VERSION = "2026.11.1"

ENTRY_DATA = {
    CONF_HOST: HOST,
    CONF_USERNAME: "x",
    CONF_PASSWORD: "user",
    CONF_TYPE: "direct_edge",
    CONF_EDGE: EDGE_ID,
}
ENTRY_OPTIONS = {
    CONF_ADVANCED_OPTIONS: {
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
        CONF_FORWARD_INTERVAL: 0,
    }
}


class FakeRpcServer:
    """Stand-in for the jsonrpc websocket server of the connection.

    Like jsonrpc_base, assigning a method registers a handler for requests of the
    edge, while calling it sends a request to the edge.
    """

    def __init__(self) -> None:
        """Initialize the server, connected and answering with empty results."""
        object.__setattr__(self, "_handlers", {})
        self.connected = True
        self.subscribeEdges = AsyncMock()
        # results of the componentJsonApi requests, by method of the inner request
        self.results: dict[str, Any] = {
            "getSystemUpdateState": {"updated": {"version": INSTALLED_VERSION}},
        }
        self.edge_rpc = AsyncMock(side_effect=self._answer)

    def __setattr__(self, name: str, value: Any) -> None:
        """Register edgeRpc as handler, set other attributes."""
        if name == "edgeRpc":
            self._handlers[name] = value
        else:
            object.__setattr__(self, name, value)

    def __getattr__(self, name: str) -> Any:
        """Send edgeRpc requests to the mock."""
        if name == "edgeRpc":
            return self.edge_rpc
        raise AttributeError(name)

    async def _answer(self, edgeId: str, payload: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        inner = payload.get("params", {}).get("payload", {})
        result = self.results.get(inner.get("method"), {})
        if isinstance(result, Exception):
            raise result
        return {"payload": {"result": result}}

    def push_current_data(self, data: dict[str, Any]) -> None:
        """Send channel data like the edge does for subscribed channels."""
        self._handlers["edgeRpc"](
            edgeId=EDGE_ID, payload={"method": "currentData", "params": data}
        )

    def component_updates(self) -> list[tuple[str, dict[str, Any]]]:
        """Return component and property values of the updateComponentConfig requests."""
        return [
            (
                payload["params"]["componentId"],
                {
                    prop["name"]: prop["value"]
                    for prop in payload["params"]["properties"]
                },
            )
            for call in self.edge_rpc.call_args_list
            if (payload := call.kwargs["payload"])["method"] == "updateComponentConfig"
        ]

    def request_methods(self) -> list[str]:
        """Return the methods of the requests sent, of componentJsonApi the inner one."""
        methods = []
        for call in self.edge_rpc.call_args_list:
            payload = call.kwargs["payload"]
            inner = payload.get("params", {}).get("payload")
            methods.append(inner["method"] if inner else payload["method"])
        return methods


def _make_connection(session: Any) -> MagicMock:
    connection = MagicMock()
    connection.conn_url = URL(f"ws://{HOST}:8085/websocket")
    connection.conn_type = "direct_edge"
    connection.username = ENTRY_DATA[CONF_USERNAME]
    connection.password = ENTRY_DATA[CONF_PASSWORD]
    connection.session = session
    connection.rpc_server = FakeRpcServer()
    connection.stop = AsyncMock()
    return connection


@pytest.fixture
def multi_edge() -> bool:
    """Return True for a backend managing several edges, eg the FENECON portal."""
    return False


@pytest.fixture
def mock_config_entry(
    request: pytest.FixtureRequest, multi_edge: bool
) -> MockConfigEntry:
    """Return the config entry of the edge, parametrize the advanced options."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        version=6,
        minor_version=5,
        unique_id=f"{HOSTNAME} {EDGE_ID}" if multi_edge else HOSTNAME,
        data=ENTRY_DATA,
        options={
            CONF_ADVANCED_OPTIONS: {
                **ENTRY_OPTIONS[CONF_ADVANCED_OPTIONS],
                **getattr(request, "param", {}),
            }
        },
    )


@pytest.fixture
def edge_config(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Return the component configuration of the edge, a reduced real one by default."""
    config = load_json_object_fixture("edge_config.json", DOMAIN)
    return deepcopy(getattr(request, "param", config))


@pytest.fixture
def channel_data(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Return the channel values sent by the edge."""
    return getattr(
        request, "param", load_json_object_fixture("channel_data.json", DOMAIN)
    )


@pytest.fixture
async def mock_openems_client(
    edge_config: dict[str, Any], multi_edge: bool
) -> AsyncGenerator[MagicMock]:
    """Mock the OpenEMSClient, the backends it creates use a fake connection.

    The backends are real, so entities are created from the library classification.
    """
    backends: list[OpenEMSBackend] = []
    client = MagicMock()
    client.host = HOST
    client.connect = AsyncMock(return_value=LoginInfo(multi_edge=multi_edge))
    client.read_edges = AsyncMock(return_value=[EdgeInfo(id=EDGE_ID, online=True)])
    client.read_edge_hostname = AsyncMock(
        return_value=f"{HOSTNAME} {EDGE_ID}" if multi_edge else HOSTNAME
    )
    client.read_edge_config = AsyncMock(
        side_effect=lambda edge_id: deepcopy(edge_config)
    )
    client.check_edge_config = AsyncMock(
        side_effect=lambda edge_id, cached: (deepcopy(cached), EdgeConfigChange())
    )
    client.close = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    # raised when the client is created, eg ValueError for an invalid host
    client.init_error = None

    def _client_class(conn_props: dict[str, Any], session: Any) -> MagicMock:
        if client.init_error is not None:
            raise client.init_error
        client.conn_props = conn_props
        client.session = session
        return client

    def _create_backend(edge_id: str, components: dict[str, Any]) -> OpenEMSBackend:
        backend = OpenEMSBackend(
            _make_connection(client.session),
            edge_id,
            client.connect.return_value.multi_edge,
            components,
        )
        backends.append(backend)
        client.backend = backend
        return backend

    client.create_backend = MagicMock(side_effect=_create_backend)

    with (
        patch("custom_components.openems.OpenEMSClient", side_effect=_client_class),
        patch(
            "custom_components.openems.config_flow.OpenEMSClient",
            side_effect=_client_class,
        ),
    ):
        yield client
    # backends of entries which are still loaded at the end of the test
    for backend in backends:
        await backend.stop()


@pytest.fixture
def platforms() -> list[Platform]:
    """Return the platforms to set up, all by default."""
    return list(_PLATFORMS)


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
    channel_data: dict[str, Any],
    platforms: list[Platform],
) -> MockConfigEntry:
    """Set up the integration and send the channel data of the edge."""
    await setup_integration(hass, mock_config_entry, platforms)
    mock_openems_client.backend.connection.rpc_server.push_current_data(channel_data)
    await hass.async_block_till_done()
    return mock_config_entry


async def setup_integration(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    platforms: list[Platform] | None = None,
) -> None:
    """Add and set up the config entry, optionally only with the given platforms."""
    entry.add_to_hass(hass)
    with patch("custom_components.openems._PLATFORMS", platforms or _PLATFORMS):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def store_edge_config(
    hass_storage: dict[str, Any], entry: MockConfigEntry, config: dict[str, Any]
) -> None:
    """Cache the edge configuration of the entry, as a previous setup did."""
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": config}


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "custom_components.openems.async_setup_entry", return_value=None
    ) as mock_setup_entry:
        yield mock_setup_entry
