"""OpenEMS API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import json
import logging
import math
import os
import re
from typing import Any
import uuid

import aiohttp
from jinja2 import Template
import jsonrpc_base
import jsonrpc_websocket
import yarl

_LOGGER = logging.getLogger(__name__)


class OpenEMSConfig:
    """Load additional config options from json files."""

    def __init__(self) -> None:
        """Initialize and read json files."""
        path = os.path.dirname(__file__)
        with open(
            path + "/config/default_channels.json", encoding="utf-8"
        ) as channel_file:
            self.default_channels = json.load(channel_file)
        with open(path + "/config/enum_options.json", encoding="utf-8") as enum_file:
            self.enum_options = json.load(enum_file)
        with open(path + "/config/time_options.json", encoding="utf-8") as time_file:
            self.time_options = json.load(time_file)
        with open(
            path + "/config/number_properties.json", encoding="utf-8"
        ) as number_file:
            self.number_properties = json.load(number_file)

    def _get_config_property(self, dict, property, component_name, channel_name):
        """Return dict property for a given component/channel."""
        for component_conf in dict:
            comp_regex = component_conf["component_regexp"]
            if re.fullmatch(comp_regex, component_name):
                for channel in component_conf["channels"]:
                    if channel["id"] == channel_name:
                        return channel.get(property)
        return None

    def get_enum_options(self, component_name, channel_name) -> list[str] | None:
        """Return option string list for a given component/channel."""
        return self._get_config_property(
            self.enum_options, "options", component_name, channel_name
        )

    def is_time_property(self, component_name, channel_name) -> list[str] | None:
        """Return True if given component/channel is marked as time."""
        return self._get_config_property(
            self.time_options, "is_time", component_name, channel_name
        )

    def get_number_limit(self, component_name, channel_name) -> dict | None:
        """Return limit definition for a given component/channel."""
        return self._get_config_property(
            self.number_properties, "limit", component_name, channel_name
        )

    def get_number_multiplier(self, component_name, channel_name) -> dict | None:
        """Return multiplier for a given component/channel."""
        return self._get_config_property(
            self.number_properties, "multiplier", component_name, channel_name
        )

    def is_component_enabled(self, comp_name: str) -> bool:
        """Return if there is at least one channel enabled by default."""
        for entry in self.default_channels:
            if re.fullmatch(entry["component_regexp"], comp_name):
                return True

        return False

    def is_channel_enabled(self, comp_name, chan_name) -> bool:
        """Return True if the channel is enabled by default."""
        for entry in self.default_channels:
            if re.fullmatch(entry["component_regexp"], comp_name):
                if chan_name in entry["channels"]:
                    return True

        return False


CONFIG: OpenEMSConfig = OpenEMSConfig()


class OpenEMSChannel:
    """Class representing a sensor of an OpenEMS component."""

    # Use with platform sensor
    def __init__(self, component, channel_json) -> None:
        """Initialize the channel."""
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
        self.callback: callable | None = None

    def handle_current_value(self, value):
        """Handle a value update from the backend."""
        if self.callback:
            self.callback(value)

    def unique_id(self) -> str:
        """Generate unique ID for the channel."""
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
        self.callback = callback
        self.component.edge.register_channel(self)

    def unregister_callback(self):
        """Remove callback."""
        self.callback = None
        self.component.edge.unregister_channel(self)


class OpenEMSProperty(OpenEMSChannel):
    """Class representing a property of an OpenEMS component."""

    async def update_value(self, value: Any) -> None:
        """Handle value change request from Home Assisant."""
        await self.component.update_config(self.name[9:], value)


class OpenEMSEnumProperty(OpenEMSProperty):
    """Class representing a enum property of an OpenEMS component."""

    # Use with platform select
    def __init__(self, component, channel_json) -> None:
        """Initialize the channel."""
        super().__init__(component, channel_json)
        self.options = channel_json["options"]


class OpenEMSTimeProperty(OpenEMSProperty):
    """Class representing a enum property of an OpenEMS component."""


class OpenEMSNumberProperty(OpenEMSProperty):
    """Class representing a number property of an OpenEMS component."""

    STEPS = 200  # Minimum number of steps

    # Use with platform Number
    def __init__(
        self,
        component,
        channel_json,
    ) -> None:
        """Initialize the number channel."""
        super().__init__(component, channel_json)
        self.multiplier: float = 1.0
        self.lower_limit: float = None
        self.upper_limit: float = None
        self.step: float = None

    def handle_current_value(self, value):
        """Handle a value update from the backend."""
        value = None if value is None else self.multiplier * value
        super().handle_current_value(value)

    async def update_value(self, value: float) -> None:
        """Handle value change request from Home Assisant."""
        await super().update_value(value / self.multiplier)

    async def init_multiplier(self, multiplier):
        """Initialize the multiplier of the number channel."""
        self.multiplier = await self._compute_expression(multiplier)

    async def init_limits(self, limit_def):
        """Initialize the limits of the number channel."""
        lower_limit = await self._compute_expression(limit_def["lower"])
        upper_limit = await self._compute_expression(limit_def["upper"])
        lower_scaled = lower_limit * self.multiplier
        upper_scaled = upper_limit * self.multiplier
        min_step_range = (upper_scaled - lower_scaled) / OpenEMSNumberProperty.STEPS
        self.step = 10 ** math.ceil(math.log10(min_step_range))
        self.lower_limit = math.ceil(float(lower_scaled) / self.step) * self.step
        self.upper_limit = math.ceil(float(upper_scaled) / self.step) * self.step

    async def _compute_expression(self, expr) -> float:
        """Resolve an expression like "{$evcs.id/MinHardwarePower} / {$evcs.id/Phases}" to a concrete number."""
        # step 1: retrieve the values of all linked channels
        for ref in re.findall("{(.*?)}", expr):
            if ref not in self.component.ref_values:
                self.component.ref_values[ref] = await self._get_ref_value(ref)

        # step 2: replace all linked channels with their values
        def lookup_ref_value(matchobj) -> str:
            return str(self.component.ref_values[matchobj.group()[1:-1]])

        value_expr = re.sub("{(.*?)}", lookup_ref_value, expr)

        # step 3: calculate the expression (using jinja2)
        return float(Template("{{" + value_expr + "}}").render())

    async def _get_ref_value(self, reference_def: str):
        """Resolve an expression like "$evcs.id/Phases" to its concrete value."""
        component_reference, channel_reference = reference_def.split("/")
        # if the component starts with $, treat it like a variable to be looked up in the component properties
        if component_reference.startswith("$"):
            component_property = component_reference[1:]
            component_reference = self.component.properties[component_property]
        # retrieve the value via FEMS REST API
        url = self.component.edge.backend.rest_base_url.joinpath(
            "channel", component_reference, channel_reference
        )
        auth = aiohttp.BasicAuth(
            self.component.edge.backend.username, self.component.edge.backend.password
        )
        async with (
            aiohttp.ClientSession(auth=auth) as session,
            session.get(url) as resp,
        ):
            data = await resp.json()
            return data["value"]


class OpenEMSBooleanProperty(OpenEMSProperty):
    """Class representing a boolean property of an OpenEMS component."""

    # Use with platform switch


class OpenEMSComponent:
    """Class representing a component of an OpenEMS Edge."""

    def __init__(self, edge, name, json_def) -> None:
        """Initialize the component."""
        self.edge: OpenEMSEdge = edge
        self.name: str = name
        self.alias = json_def.get("_PropertyAlias")
        self.ref_values: dict = {}
        self.properties: dict = json_def["properties"]
        self.sensors: list[OpenEMSChannel] = []
        self.enum_properties: list[OpenEMSEnumProperty] = []
        self.number_properties: list[OpenEMSNumberProperty] = []
        self.boolean_properties: list[OpenEMSBooleanProperty] = []
        self.time_properties: list[OpenEMSTimeProperty] = []
        self.create_entities: bool = False

    async def init_channels(self, channels):
        """Parse and initialize the components channels."""
        for channel_json in channels:
            if channel_json["id"].startswith("_Property"):
                # scan type and convert to property
                match channel_json["type"]:
                    case "BOOLEAN":
                        prop = OpenEMSBooleanProperty(
                            component=self, channel_json=channel_json
                        )
                        self.boolean_properties.append(prop)
                    case "STRING":
                        if options := CONFIG.get_enum_options(
                            self.name, channel_json["id"]
                        ):
                            channel_json["options"] = options
                        if "options" in channel_json:
                            prop = OpenEMSEnumProperty(
                                component=self, channel_json=channel_json
                            )
                            self.enum_properties.append(prop)
                        elif CONFIG.is_time_property(self.name, channel_json["id"]):
                            prop = OpenEMSTimeProperty(
                                component=self, channel_json=channel_json
                            )
                            self.time_properties.append(prop)
                    case "INTEGER":
                        if limit_def := CONFIG.get_number_limit(
                            self.name, channel_json["id"]
                        ):
                            multiplier = CONFIG.get_number_multiplier(
                                self.name, channel_json["id"]
                            )
                            prop = OpenEMSNumberProperty(
                                component=self, channel_json=channel_json
                            )
                            if multiplier is not None:
                                await prop.init_multiplier(multiplier)
                            await prop.init_limits(limit_def)
                            self.number_properties.append(prop)

            else:
                channel = OpenEMSChannel(component=self, channel_json=channel_json)
                self.sensors.append(channel)

    async def update_config(self, property_name, value: Any):
        """Send updateComponentConfig request to backend."""
        properties = [{"name": property_name, "value": value}]
        envelope = OpenEMSBackend.wrap_jsonrpc(
            "updateComponentConfig", componentId=self.name, properties=properties
        )
        await self.edge.backend.rpc_server.edgeRpc(
            edgeId=self.edge.id, payload=envelope
        )

    @property
    def channels(self) -> list[OpenEMSChannel]:
        """Return all channels of the component (all platforms)."""
        return [
            *self.sensors,
            *self.enum_properties,
            *self.number_properties,
            *self.boolean_properties,
            *self.time_properties,
        ]


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

        def clear(self):
            """Clear the list of active subscriptions."""
            self._active_subscriptions = []

        async def _update_subscriptions_forever(self):
            try:
                _LOGGER.debug("SubscriptionUpdater start")
                while True:
                    await asyncio.sleep(5)
                    subscribe_in_progress_channels = list(
                        self._edge.registered_channels.keys()
                    )
                    if (
                        self._edge.backend.rpc_server.connected
                        and subscribe_in_progress_channels != self._active_subscriptions
                    ):
                        _LOGGER.debug(
                            "SubscriptionUpdater update: %d entities",
                            len(subscribe_in_progress_channels),
                        )
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
            except asyncio.CancelledError:
                _LOGGER.debug("SubscriptionUpdater end")
                raise

    def __init__(self, backend, id) -> None:
        """Initialize the edge."""
        self.backend: OpenEMSBackend = backend
        self._id: int = id
        self._edge_config: dict[str, dict] | None = None
        self.components: dict[str, OpenEMSComponent] = {}
        self.current_channel_data: dict | None = None
        self._channel_subscription_updater = self.OpenEmsEdgeChannelSubscriptionUpdater(
            self
        )
        self.hostname: str | None = None
        self._registered_channels: dict = {}

    async def prepare_entities(self):
        """Parse json config and create class structures."""
        self.hostname = self._edge_config["_host"]["Hostname"]
        if self.backend.multi_edge:
            self.hostname += " " + self.id_str

        for name, contents in self._edge_config.items():
            component: OpenEMSComponent = OpenEMSComponent(self, name, contents)
            await component.init_channels(contents["channels"])
            # create the entities within a service which linked to the edge device
            self.components[name] = component

    def set_unavailable(self):
        """Set all active entities to unavailable and clear the subscription indicator."""
        for key in self.current_channel_data:
            channel: OpenEMSChannel = self._registered_channels.get(key)
            if channel:
                channel.handle_current_value(None)
        self._channel_subscription_updater.clear()

    def stop(self):
        """Stop the connection to edge and all its subscriptions."""
        self._channel_subscription_updater.stop()

    def set_config(self, params):
        """Store the configuration which the edge sent us."""
        self._edge_config = params

    def edgeConfig(self, params):
        """Jsonrpc callback to receive edge config updates."""
        self.set_config(params["components"])

    def currentData(self, params):
        """Jsonrpc callback to receive channel subscription updates."""
        self.current_channel_data = params
        for key in self.current_channel_data:
            if channel := self._registered_channels.get(key):
                channel.handle_current_value(self.current_channel_data[key])

    def register_channel(self, channel: OpenEMSChannel):
        """Register one channel for updates."""
        key = channel.component.name + "/" + channel.name
        self._registered_channels[key] = channel

    def unregister_channel(self, channel: OpenEMSChannel):
        """Remove one channel from updates."""
        key = channel.component.name + "/" + channel.name
        del self._registered_channels[key]

    @property
    def id(self):
        """Return the edge ID."""
        return self._id

    @property
    def id_str(self):
        "Return the edge ID string."
        return "edge" + self._id

    @property
    def config(self):
        "Return the edge config."
        return self._edge_config

    @property
    def registered_channels(self):
        "Return the list of all subscribed channels."
        return self._registered_channels


class OpenEMSBackend:
    """Class which represents a connection to an OpenEMS backend."""

    def __init__(self, host: str, username: str, password: str) -> None:
        """Create a new OpenEMSBackend object."""
        self.username: str = username
        self.password: str = password
        self.host: str = host
        websocket_url = "ws://" + host + ":8085/"
        self.rpc_server = jsonrpc_websocket.Server(
            websocket_url, session=None, heartbeat=5
        )
        self.rest_base_url: yarl.URL = yarl.URL("http://" + host + ":8084" + "/rest/")
        self.rpc_server.edgeRpc = self.edgeRpc
        self.edges: dict[int, OpenEMSEdge] = {}
        self.multi_edge = True
        self._reconnect_task = None
        self._login_successful_event: asyncio.Event | None = asyncio.Event()

    @staticmethod
    def wrap_jsonrpc(method, **params):
        """Wrap a method call with paramters into a jsonrpc call."""
        envelope = {}
        envelope["jsonrpc"] = "2.0"
        envelope["method"] = method
        envelope["params"] = params
        envelope["id"] = str(uuid.uuid4())
        return envelope

    def edgeRpc(self, **kwargs):
        """Handle an edge jsonrpc callback and call the respective method of the edge object."""
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
        """Start a tasks which checks for connection losses tries to reconnect afterwards."""
        self._reconnect_task = asyncio.create_task(self._reconnect_forever())

    async def wait_for_login(self):
        """Wait for response to a login request."""
        await self._login_successful_event.wait()

    async def stop(self):
        """Close the connection to the backend and all internal connection objects."""
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
        """Parse json config of all edges."""
        for edge in self.edges.values():
            await edge.prepare_entities()

    async def subscribe_for_config_changes(self, edge_id):
        """Subscribe for edgeConfig updates."""
        return await self.rpc_server.subscribeEdges(edges=json.dumps([edge_id]))

    async def read_config(self) -> dict:
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
        auth = aiohttp.BasicAuth(self.username, self.password)

        url = self.rest_base_url.joinpath("channel", ".+", "_PropertyAlias")
        async with (
            aiohttp.ClientSession(auth=auth) as session,
            session.get(url) as resp,
        ):
            data = await resp.json()
            for entry in data:
                channel_name_items = entry["address"].split("/")
                config[edge.id]["components"][channel_name_items[0]][
                    channel_name_items[1]
                ] = entry["value"]

        url = self.rest_base_url.joinpath("channel", "_host", "Hostname")
        async with (
            aiohttp.ClientSession(auth=auth) as session,
            session.get(url) as resp,
        ):
            data = await resp.json()
            config[edge.id]["components"]["_host"]["Hostname"] = data["value"]
