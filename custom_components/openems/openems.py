"""OpenEMS API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import contextlib
import json
import logging
from typing import Any
import uuid

import jsonrpc_base
import jsonrpc_websocket

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import exceptions

_LOGGER = logging.getLogger(__name__)


class OpenEMSChannel:
    """Class representing a sensor of an OpenEMS component."""

    # Use with platform sensor
    def __init__(self, component, channel_json) -> None:
        name = channel_json["id"]
        unit = channel_json["unit"]
        if (
            "category" in channel_json
            and channel_json["category"] == "ENUM"
            and "options" in channel_json
        ):
            options = {v: k for k, v in channel_json["options"].items()}
        else:
            options = {}

        self.component: OpenEMSComponent = component
        self.name: str = name
        self.unit: str = unit
        self.options: list | None = options
        self.orig_json: list | None = channel_json

    def unique_id(self) -> str:
        return (
            self.component.edge.hostname
            + "/"
            + self.component.edge.id_str
            + "/"
            + self.component.name
            + "/"
            + self.name
        )

    def register_callback(self, callback: Callable):
        """Register callback."""
        self.component.edge.register_callback(
            self.component.name + "/" + self.name, callback
        )

    def unregister_callback(self):
        """Remove callback."""
        self.component.edge.unregister_callback(self.component.name + "/" + self.name)


class OpenEMSEnumProperty(OpenEMSChannel):
    """Class representing a enum property of an OpenEMS component."""

    # Use with platform select


class OpenEMSNumberProperty(OpenEMSChannel):
    """Class representing a number property of an OpenEMS component."""

    # Use with platform Number


class OpenEMSBooleanProperty(OpenEMSChannel):
    """Class representing a boolean property of an OpenEMS component."""

    # Use with platform switch

    def __init__(self, component, channel_json) -> None:
        super().__init__(component, channel_json)

    async def update_value(self, value) -> None:
        await self.component.update_config(self.name[9:], value)


class OpenEMSComponent:
    """Class representing a component of an OpenEMS Edge."""

    def __init__(self, edge, name, json_def) -> None:
        """Initialize the component."""
        self.edge: OpenEMSEdge = edge
        self.name: str = name
        self.alias = json_def.get("_PropertyAlias")
        self._properties: dict = json_def["properties"]
        self.sensors: list[OpenEMSChannel] = []
        self.enum_properties: list[OpenEMSEnumProperty] = []
        self.number_properties: list[OpenEMSNumberProperty] = []
        self.boolean_properties: list[OpenEMSBooleanProperty] = []
        for channel_json in json_def["channels"]:
            if channel_json["id"].startswith("_Property"):
                # scan type and convert to property
                match channel_json["type"]:
                    case "BOOLEAN":
                        prop = OpenEMSBooleanProperty(
                            component=self, channel_json=channel_json
                        )
                        self.boolean_properties.append(prop)
            elif not name.startswith("ctrl"):
                # dont create non-Property channels of ctrl-components
                channel = OpenEMSChannel(component=self, channel_json=channel_json)
                self.sensors.append(channel)

    async def update_config(self, property_name, value):
        """Send updateComponentConfig request to backend."""
        properties = [{"name": property_name, "value": value}]
        envelope = OpenEMSBackend.wrap_jsonrpc(
            "updateComponentConfig", componentId=self.name, properties=properties
        )
        await self.edge.backend.rpc_server.edgeRpc(
            edgeId=self.edge.id, payload=envelope
        )


class OpenEMSEdge:
    """Class representing an OpenEMS Edge device."""

    class OpenEmsEdgeChannelSubscriptionUpdater:
        """Allows to register callbacks methods and get notified on updates."""

        def __init__(self, edge) -> None:
            """Initialize the updater."""
            self._edge: OpenEMSEdge = edge
            loop = asyncio.get_event_loop()
            self._fetch_task = loop.create_task(self._update_subscriptions_forever())
            self._active_subscriptions = []

        def stop(self):
            """Stop the updater."""
            self._fetch_task.cancel()

        async def _update_subscriptions_forever(self):
            while True:
                await asyncio.sleep(5)
                subscribe_in_progress_channels = list(self._edge.callbacks.keys())
                if (
                    self._edge.backend.rpc_server.connected
                    and subscribe_in_progress_channels != self._active_subscriptions
                ):
                    with contextlib.suppress(
                        jsonrpc_base.jsonrpc.TransportError,
                        jsonrpc_base.jsonrpc.ProtocolError,
                    ):
                        subscribe_call = OpenEMSBackend.wrap_jsonrpc(
                            "subscribeChannels",
                            count=0,
                            channels=subscribe_in_progress_channels,
                        )
                        await self._edge.backend.rpc_server.edgeRpc(
                            edgeId=self._edge.id_str, payload=subscribe_call
                        )
                        self._active_subscriptions = subscribe_in_progress_channels

    def __init__(self, backend, id) -> None:
        """Initialize the edge."""
        self.backend: OpenEMSBackend = backend
        self._id: int = id
        self._edge_config: dict[str, dict] | None = None
        self.edge_component: OpenEMSComponent | None = None
        self.components: dict[str, OpenEMSComponent] = {}
        self._data_event: asyncio.Event | None = asyncio.Event()
        self.current_channel_data: dict | None = None
        self._channel_subscription_updater = self.OpenEmsEdgeChannelSubscriptionUpdater(
            self
        )
        self.hostname: str | None = None
        self._callbacks = {}

    async def prepare_entities(self):
        """Parse json config and create class structures."""
        self.hostname = self._edge_config["_host"]["Hostname"]
        if self.backend.multi_edge:
            self.hostname += " " + self.id_str

        for name, contents in self._edge_config.items():
            component: OpenEMSComponent = OpenEMSComponent(self, name, contents)
            if name == "_sum":
                # all entities of the _sum component are created within the edge device
                self.edge_component = component
            elif contents["_PropertyAlias"]:
                # If the component has a property alias,
                # create the entities within a service which linked to the edge device
                self.components[name] = component

    def set_unavailable(self):
        """Set all active entities to unavailable and clear the subscription indicator."""
        for key in self.current_channel_data:
            callback = self._callbacks.get(key)
            if callback:
                callback(key, None)
        self._channel_subscription_updater._active_subscriptions = []
        self._data_event.set()
        self._data_event.clear()

    def stop(self):
        """Stop the connection to edge and all its subscriptions."""
        self._channel_subscription_updater.stop()

    def set_config(self, params):
        """Store the configuration which the edge sent us."""
        self._edge_config = params

    def edgeConfig(self, params):
        """Jsonrpc callback to receive edge config updates."""
        self.set_config(params["components"])

    async def wait_for_current_data(self):
        """Wait for the next jsonrpc callback to currentData()."""
        await self._data_event.wait()
        return self.current_channel_data

    def currentData(self, params):
        """Jsonrpc callback to receive channel subscription updates."""
        self.current_channel_data = params
        for key in self.current_channel_data:
            callback = self._callbacks.get(key)
            if callback:
                callback(key, self.current_channel_data[key])
        self._data_event.set()
        self._data_event.clear()

    def register_callback(self, key: str, callback: Callable):
        """Register callback for one channel."""
        self._callbacks[key] = callback

    def unregister_callback(self, key: str):
        """Remove callback for one channel."""
        del self._callbacks[key]

    @property
    def id(self):
        return self._id

    @property
    def id_str(self):
        return "edge" + self._id

    @property
    def config(self):
        return self._edge_config

    @property
    def callbacks(self):
        return self._callbacks


class OpenEMSBackend:
    def __init__(
        self,
        hass: HomeAssistant,
        config: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
        config_entry_id: str | None = None,
    ) -> None:
        websocket_url = "ws://" + config[CONF_HOST] + ":80/websocket"
        self.rpc_server = jsonrpc_websocket.Server(
            websocket_url, session=None, heartbeat=5
        )
        self.rpc_server.edgeRpc = self.edgeRpc
        self.username: str = config[CONF_USERNAME]
        self.password: str = config[CONF_PASSWORD]
        self.edges: dict[int, OpenEMSEdge] = {}
        self.multi_edge = True
        self._reconnect_task = None
        self._login_successful_event: asyncio.Event | None = asyncio.Event()

    def wrap_jsonrpc(method, **params):
        envelope = {}
        envelope["jsonrpc"] = "2.0"
        envelope["method"] = method
        envelope["params"] = params
        envelope["id"] = str(uuid.uuid4())
        return envelope

    def edgeRpc(self, **kwargs):
        edge = self.edges[kwargs["edgeId"]]
        method_name = kwargs["payload"]["method"]
        try:
            method = getattr(edge, method_name)
        except AttributeError:
            _LOGGER.error("Unhandled callback method: %s", method_name)
            return

        params = kwargs["payload"]["params"]
        # call the edge method
        method(params)

    async def _reconnect_main(self):
        ws_task: asyncio.Task = await self.rpc_server.ws_connect()
        with contextlib.suppress(Exception):
            await ws_task

    async def _reconnect_forever(self):
        while True:
            try:
                ws_task = await self.rpc_server.ws_connect()
            except (
                jsonrpc_base.jsonrpc.TransportError,
                jsonrpc_base.jsonrpc.ProtocolError,
            ):
                # cannot connect
                await asyncio.sleep(10)
                continue

            # connected. Lets login
            retval = await self.rpc_server.authenticateWithPassword(
                username=self.username, password=self.password
            )
            self.multi_edge = retval["user"]["hasMultipleEdges"]
            self._login_successful_event.set()
            self._login_successful_event.clear()
            with contextlib.suppress(
                jsonrpc_base.jsonrpc.TransportError,
                jsonrpc_base.jsonrpc.ProtocolError,
            ):
                await ws_task
            # we lost connection
            for edge in self.edges.values():
                edge.set_unavailable()
            self._login_successful_event.clear()
            await asyncio.sleep(10)

    def start(self):
        self._reconnect_task = asyncio.create_task(self._reconnect_forever())

    async def wait_for_login(self):
        await self._login_successful_event.wait()

    async def stop(self):
        self._reconnect_task.cancel()
        await self.rpc_server.close()
        for edge in self.edges.values():
            edge.stop()

    def set_config(self, config: dict):
        """Parse list of all edges and their config."""
        self.edges = {}
        for edge_id, edge_config in config.items():
            edge = OpenEMSEdge(self, edge_id)
            # Load edgeConfig
            edge.set_config(edge_config["components"])

            self.edges[edge_id] = edge

    async def prepare_entities(self):
        for edge in self.edges.values():
            await edge.prepare_entities()

    async def subscribe_for_config_changes(self, edge_id):
        """Subscribe for edgeConfig updates."""
        return await self.rpc_server.subscribeEdges(edges=json.dumps([edge_id]))

    async def read_config(self):
        """Request list of all edges and their config."""
        config = {}
        r = await self.rpc_server.getEdges(page=0, limit=20, searchParams={})
        json_edges = r["edges"]
        for json_edge in json_edges:
            edge_id = json_edge["id"]
            self.edges[edge_id] = OpenEMSEdge(self, edge_id)

            # Load edgeConfig
            edge_call = OpenEMSBackend.wrap_jsonrpc("getEdgeConfig")
            r = await self.rpc_server.edgeRpc(edgeId=edge_id, payload=edge_call)
            config[edge_id] = {}
            config[edge_id]["components"] = r["payload"]["result"]["components"]
            # read info details from selected channels
            await self._read_component_info_channels(config, self.edges[edge_id])

            # read properties of all channels of each component
            await self._read_edge_channels(config, self.edges[edge_id])
            # update edge with the config
            self.edges[edge_id].set_config(config[edge_id]["components"])

        return config

    async def _read_edge_channels(self, config, edge):
        # Load channels of each component
        for componentId in config[edge.id]["components"]:
            edge_component_call = OpenEMSBackend.wrap_jsonrpc(
                "getChannelsOfComponent",
                componentId=componentId,
            )
            edge_call = OpenEMSBackend.wrap_jsonrpc(
                "componentJsonApi",
                componentId="_componentManager",
                payload=edge_component_call,
            )
            r = await self.rpc_server.edgeRpc(
                edgeId="edge" + edge.id,
                payload=edge_call,
            )
            config[edge.id]["components"][componentId]["channels"] = r["payload"][
                "result"
            ]["channels"]

    async def _read_component_info_channels(self, config: dict, edge: OpenEMSEdge):
        """Read hostname and all component names of an edge."""

        config_channels = ["_host/Hostname"]
        config_channels.extend(
            comp + "/_PropertyAlias" for comp in config[edge.id]["components"]
        )

        # Subscribe channels
        edge_call = OpenEMSBackend.wrap_jsonrpc(
            "subscribeChannels", count=0, channels=config_channels
        )
        await self.rpc_server.edgeRpc(edgeId="edge" + edge.id, payload=edge_call)
        # wait for data
        data = await asyncio.wait_for(edge.wait_for_current_data(), timeout=2)
        # Unsubscribe channels
        edge_call = OpenEMSBackend.wrap_jsonrpc(
            "subscribeChannels", count=0, channels=[]
        )
        await self.rpc_server.edgeRpc(edgeId="edge" + edge.id, payload=edge_call)

        for channel_name, channel_value in data.items():
            channel_name_items = channel_name.split("/")
            config[edge.id]["components"][channel_name_items[0]][
                channel_name_items[1]
            ] = channel_value
