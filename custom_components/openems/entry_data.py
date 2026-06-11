"""OpenEMS config entry data access and preparation."""

import asyncio
from collections.abc import Callable
import contextlib
import logging
import time
from typing import TypedDict

import jsonrpc_base
import jsonrpc_websocket
from yarl import URL

from .const import CONN_TYPE_CUSTOM_URL, CURRENT_DATA_TIMEOUT_SECONDS
from .helpers import connection_url, wrap_jsonrpc

_LOGGER = logging.getLogger(__name__)


class ConnectionProperties(TypedDict):
    "Type containing the websocket connection paramters."

    host: str | None
    password: str
    type: str
    url: str | None
    username: str


class OpenEMSWebSocketConnection:
    """Class to manage a websocket connection to an OpenEMS system."""

    def __init__(self, conn_props: ConnectionProperties) -> None:
        """Initialize OpenEMS websocket connection management."""
        if conn_props["type"] == CONN_TYPE_CUSTOM_URL:
            url_prop: str = conn_props["url"]  # type: ignore[assignment]
            self.conn_url = URL(url_prop)
        else:
            self.conn_url = connection_url(
                conn_props["type"],
                conn_props["host"],
            )
        self.username: str = conn_props["username"]
        self.password: str = conn_props["password"]

        self.rpc_server = jsonrpc_websocket.Server(
            self.conn_url, session=None, heartbeat=5
        )
        self.rpc_server_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._last_data_received: float = time.time()

    async def connect_to_server(self):
        "Establish websocket connection."
        self.rpc_server_task = await self.rpc_server.ws_connect()

    async def login_to_server(self) -> dict:
        "Authenticate with username and password."
        if not self.rpc_server.connected:
            raise ConnectionError
        return await self.rpc_server.authenticateWithPassword(
            username=self.username, password=self.password
        )

    def enable_reconnect(self, connection_lost_callback: Callable):
        """Start a tasks which checks for connection losses tries to reconnect afterwards."""
        self._reconnect_task = asyncio.create_task(
            self._reconnect_forever(connection_lost_callback)
        )

    def notify_data_received(self) -> None:
        """Store the timestamp of the last received data. Used for connection loss detection."""
        self._last_data_received = time.time()

    async def _reconnect_forever(self, connection_lost_callback: Callable):
        while True:
            # check for an existing connection
            if self.rpc_server_task:
                while True:
                    with contextlib.suppress(
                        jsonrpc_base.jsonrpc.TransportError,
                        jsonrpc_base.jsonrpc.ProtocolError,
                    ):
                        await asyncio.wait([self.rpc_server_task], timeout=10)
                        # check if data was received recently. If not, trigger reconnect.
                        if (
                            time.time() - self._last_data_received
                            > CURRENT_DATA_TIMEOUT_SECONDS
                        ):
                            # treat the connection as lost.
                            _LOGGER.info(
                                "No data updates received from host %s for %d seconds. Triggering reconnect",
                                self.conn_url.host,
                                CURRENT_DATA_TIMEOUT_SECONDS,
                            )
                            await self.rpc_server.close()

                        if self.rpc_server_task.done():
                            # connection lost (either by missing tcp keepalive or by missing data updates).
                            # Trigger reconnect.
                            break

            # connection lost
            _LOGGER.info("Connection to host %s lost", self.conn_url.host)
            self.rpc_server_task = None
            connection_lost_callback()
            await asyncio.sleep(10)

            try:
                await self.connect_to_server()
                _LOGGER.debug("Reconnected to host %s", self.conn_url.host)
                # connected. Lets login
                await self.login_to_server()
                _LOGGER.info("Connection to host %s reestablished", self.conn_url.host)
            except (
                jsonrpc_base.jsonrpc.TransportError,
                jsonrpc_base.jsonrpc.ProtocolError,
            ):
                await asyncio.sleep(10)
            self._last_data_received = time.time()

    async def stop(self):
        """Close the connection and stop the reconnect logic andg c."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self.rpc_server.close()


class EdgeNotDefinedError(Exception):
    """Raised when no edge is defined in config entry data."""


class OpenEMSConfigReader:
    """Data for managing OpenEMS config entry data."""

    def __init__(
        self, connection: OpenEMSWebSocketConnection, edge_id: str | None = None
    ) -> None:
        """Initialize OpenEMS entry data."""
        self.connection: OpenEMSWebSocketConnection = connection
        self.edge_id: str | None = edge_id

    def set_edge_id(self, edge_id: str):
        """Set the edge ID to be used for further data requests."""
        self.edge_id = edge_id

    async def read_edge_components(self) -> dict:
        """Read components of the edge."""
        if not self.edge_id:
            raise EdgeNotDefinedError("No edge ID defined for reading components.")

        # read component list
        edge_call = wrap_jsonrpc("getEdgeConfig")
        r = await self.connection.rpc_server.edgeRpc(
            edgeId=self.edge_id, payload=edge_call
        )
        components = r["payload"]["result"]["components"]

        # read properties of all channels of each component
        await self._read_edge_channels(components)

        # read info details from selected channels
        await self._read_component_info_channels(components)

        return components

    async def _read_edge_channels(self, components):
        """Load channels of each component."""
        for component_id in list(components):
            edge_component_call = wrap_jsonrpc(
                "getChannelsOfComponent",
                componentId=component_id,
            )
            try:
                edge_call = wrap_jsonrpc(
                    "componentJsonApi",
                    componentId="_componentManager",
                    payload=edge_component_call,
                )
                r = await self.connection.rpc_server.edgeRpc(
                    edgeId=self.edge_id,
                    payload=edge_call,
                )
                components[component_id]["channels"] = r["payload"]["result"][
                    "channels"
                ]
            except (
                jsonrpc_base.jsonrpc.TransportError,
                jsonrpc_base.jsonrpc.ProtocolError,
            ):
                _LOGGER.warning(
                    "_read_edge_channels: could not read channels of component %s, skipping",
                    component_id,
                )
                del components[component_id]

    async def _read_component_info_channels(self, components: dict):
        """Read hostname and all component names of an edge."""

        # Look up all components which have a channel "_PropertyAlias"
        alias_components = []
        for comp_name, comp in components.items():
            for chan in comp["channels"]:
                if chan["id"] == "_PropertyAlias":
                    alias_components.append(comp_name)
                    continue

        config_channels = ["_host/Hostname"]
        config_channels.extend(c + "/_PropertyAlias" for c in alias_components)
        data = await self.get_channel_values_via_websocket(config_channels)

        # store component aliases and hostname in the json config of the component
        for address, value in data.items():
            component, channel = address.split("/")
            if component in components:
                components[component][channel] = value

    async def get_channel_values_via_websocket(self, channels: list[str]) -> dict:
        """Read channels via dedicated websocket connection."""
        if not self.edge_id:
            raise EdgeNotDefinedError("No edge ID defined for reading components.")

        # create new connection and login
        rpc_server = jsonrpc_websocket.Server(
            url=self.connection.conn_url,
            session=None,
            heartbeat=5,
        )

        await rpc_server.ws_connect()
        _LOGGER.debug("wsocket component info request: login")
        await rpc_server.authenticateWithPassword(
            username=self.connection.username, password=self.connection.password
        )

        # prepare callback event and subscribe for the required data
        data_received: asyncio.Event = asyncio.Event()
        data = {}

        def _handle_callback(**kwargs):
            if kwargs["payload"]["method"] != "currentData":
                # ignore
                return

            nonlocal data
            data = kwargs["payload"]["params"]
            data_received.set()

        rpc_server.edgeRpc = _handle_callback
        await rpc_server.subscribeEdges(edges=[self.edge_id])

        subscribe_call = wrap_jsonrpc("subscribeChannels", count=0, channels=channels)
        await rpc_server.edgeRpc(edgeId=self.edge_id, payload=subscribe_call)  # pyright: ignore[reportGeneralTypeIssues]

        # wait for the data. When received, close connection and return data
        await asyncio.wait_for(data_received.wait(), timeout=5)
        await rpc_server.close()

        return data

    async def read_edges(self) -> dict:
        """Request list of all edges."""
        return await self.connection.rpc_server.getEdges(
            page=0, limit=20, searchParams={}
        )

    @staticmethod
    def parse_login_response(login_response: dict) -> bool:
        """Parse login response to determine if backend has multiple edges."""
        if user_dict := login_response.get("user"):
            return user_dict["hasMultipleEdges"]
        # Fallback to true. Should never happen.
        return True
