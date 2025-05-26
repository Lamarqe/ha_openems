"""OpenEMS API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from datetime import time
import json
import logging
import math
from numbers import Number
import os
import re
from typing import Any
import uuid

import aiohttp
from jinja2 import Template
import jsonrpc_base
import jsonrpc_websocket
from yarl import URL

from .const import CONN_TYPE_REST, CONN_TYPE_WEB_FENECON, CONN_TYPES, connection_url

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
        with open(
            path + "/config/component_update_groups.json", encoding="utf-8"
        ) as groups_file:
            self.update_groups = json.load(groups_file)

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

    def update_group_members(self, comp_name, chan_name) -> tuple[list[str], Any]:
        """Return list of all update group members and the condition value."""
        for entry in self.update_groups:
            if re.fullmatch(entry["component_regexp"], comp_name):
                for rule in entry["rules"]:
                    if rule["channel"] == chan_name:
                        return rule["requires"], rule.get("when")

        return [], None


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
        self._current_value: Any = None

    def handle_current_value(self, value):
        """Handle a new value and notify Home Assistant."""
        if value != self._current_value:
            self._current_value = value
            self.notify_ha()

    def handle_raw_value(self, value) -> None:
        """Handle a value update from the backend."""
        if value in self.options:
            value = self.options[value]
        self.handle_current_value(value)

    @property
    def native_value(self) -> Any | None:
        """Return the value of the sensor."""
        return self._current_value

    @property
    def current_value(self):
        """Return the current value of the channel."""
        return self._current_value

    def notify_ha(self):
        """Notify HA of a value cahnge."""
        if self.callback:
            self.callback()

    def unique_id(self) -> str:
        """Generate unique ID for the channel."""
        return (
            self.component.edge.hostname
            + "/"
            + self.component.edge.id
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

    async def update_value(self, new_value: Any) -> None:
        """Handle value change request from Home Assisant."""
        channel_names, condition_value = CONFIG.update_group_members(
            self.component.name, self.name
        )
        properties = [(self.name[9].lower() + self.name[10:], new_value)]
        if condition_value is None or condition_value == new_value:
            for channel_name in channel_names:
                channel: OpenEMSProperty = next(
                    chan
                    for chan in self.component.channels
                    if chan.name == channel_name
                )
                properties.append(
                    (self.name[9].lower() + self.name[10:], channel.current_value)
                )

        await self.component.update_config(properties)


class OpenEMSEnumProperty(OpenEMSProperty):
    """Class representing a enum property of an OpenEMS component."""

    # Use with platform select
    def __init__(self, component, channel_json) -> None:
        """Initialize the channel."""
        super().__init__(component, channel_json)
        self.options = channel_json["options"]

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self._current_value

    def handle_raw_value(self, value):
        """Handle a value update from the backend."""
        if isinstance(value, str) and value in self.options:
            new_val = value
        else:
            new_val = None
        self.handle_current_value(new_val)


class OpenEMSTimeProperty(OpenEMSProperty):
    """Class representing a enum property of an OpenEMS component."""

    def handle_raw_value(self, value):
        """Handle a value update from the backend."""
        if value is None:
            new_val = None
        else:
            try:
                hour_str, minute_str = value.split(":")
                new_val = time(int(hour_str), int(minute_str))
            except ValueError:
                new_val = None
        self.handle_current_value(new_val)

    @property
    def native_value(self) -> time | None:
        """Return the value of the time entity."""
        return self._current_value

    async def async_set_value(self, value: time) -> None:
        """Update the selected time."""
        time_str = value.strftime("%H:%M")
        await super().update_value(time_str)


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

    @property
    def native_value(self) -> Number | None:
        """Return the value of the number entity."""
        return self._current_value

    def handle_raw_value(self, value):
        """Handle a value update from the backend."""
        if isinstance(value, Number):
            new_val = self.multiplier * value
        else:
            new_val = None
        self.handle_current_value(new_val)

    async def update_value(self, new_value: Number) -> None:
        """Handle value change request from Home Assisant."""
        await super().update_value(new_value / self.multiplier)

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
                # TODO: This could be optimized to load ref values in bulk
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
        channel = component_reference + "/" + channel_reference
        if self.component.edge.backend.rest_base_url:
            # retrieve the value via REST API
            data = await self.component.edge.get_channel_values_via_rest([channel])
        else:
            # retrieve the value via Websocket API
            data = await self.component.edge.get_channel_values_via_websocket([channel])
        return data[channel]


class OpenEMSBooleanProperty(OpenEMSProperty):
    """Class representing a boolean property of an OpenEMS component."""

    # Use with platform switch

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        return self._current_value

    def handle_raw_value(self, value):
        """Handle a value update from the backend."""
        if isinstance(value, int):
            new_val = bool(value)
        else:
            new_val = None
        self.handle_current_value(new_val)


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

    async def update_config(self, channels: list[tuple[str, Any]]):
        """Send updateComponentConfig request to backend."""
        properties = [{"name": chan[0], "value": chan[1]} for chan in channels]
        envelope = OpenEMSBackend.wrap_jsonrpc(
            "updateComponentConfig", componentId=self.name, properties=properties
        )
        _LOGGER.debug(
            "updateComponentConfig: component: %s, properties: %s",
            self.name,
            str(properties),
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
                        try:
                            if not self._active_subscriptions:
                                # no active subscription, so subscribe for the edge
                                await self._edge.backend.rpc_server.subscribeEdges(
                                    edges=[self._edge.id]
                                )

                            subscribe_call = OpenEMSBackend.wrap_jsonrpc(
                                "subscribeChannels",
                                count=0,
                                channels=subscribe_in_progress_channels,
                            )
                            await self._edge.backend.rpc_server.edgeRpc(
                                edgeId=self._edge.id, payload=subscribe_call
                            )
                            self._active_subscriptions = subscribe_in_progress_channels
                            _LOGGER.debug(
                                "SubscriptionUpdater update: %d entities",
                                len(subscribe_in_progress_channels),
                            )
                        except (
                            jsonrpc_base.jsonrpc.TransportError,
                            jsonrpc_base.jsonrpc.ProtocolError,
                        ):
                            _LOGGER.exception(
                                "SubscriptionUpdater error during subscribe"
                            )
            except asyncio.CancelledError:
                _LOGGER.debug("SubscriptionUpdater end")
                raise

    def __init__(self, backend, id) -> None:
        """Initialize the edge."""
        self.backend: OpenEMSBackend = backend
        self._id: str = id
        self._component_config: dict[str, dict] | None = None
        self.components: dict[str, OpenEMSComponent] = {}
        self.current_channel_data: dict | None = None
        self._channel_subscription_updater = self.OpenEmsEdgeChannelSubscriptionUpdater(
            self
        )
        self.hostname: str | None = None
        self._registered_channels: dict = {}

    async def read_components(self) -> dict:
        """Read components of the edge."""

        # read component list
        edge_call = OpenEMSBackend.wrap_jsonrpc("getEdgeConfig")
        r = await self.backend.rpc_server.edgeRpc(edgeId=self._id, payload=edge_call)
        components = r["payload"]["result"]["components"]

        # read properties of all channels of each component
        await self._read_edge_channels(components)

        # read info details from selected channels
        await self._read_component_info_channels(components)

        self._component_config = components
        return self._component_config

    async def _read_edge_channels(self, components):
        # Load channels of each component
        for componentId in components:
            edge_component_call = OpenEMSBackend.wrap_jsonrpc(
                "getChannelsOfComponent",
                componentId=componentId,
            )
            edge_call = OpenEMSBackend.wrap_jsonrpc(
                "componentJsonApi",
                componentId="_componentManager",
                payload=edge_component_call,
            )
            r = await self.backend.rpc_server.edgeRpc(
                edgeId=self._id,
                payload=edge_call,
            )
            components[componentId]["channels"] = r["payload"]["result"]["channels"]

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
        if self.backend.rest_base_url:
            # optimize for performance by creating a regex
            config_channels.append("|".join(alias_components) + "/_PropertyAlias")
            data = await self.get_channel_values_via_rest(config_channels)
        else:
            config_channels.extend(c + "/_PropertyAlias" for c in alias_components)
            data = await self.get_channel_values_via_websocket(config_channels)

        # store component aliases and hostname in the json config of the component
        for address, value in data.items():
            component, channel = address.split("/")
            if component in components:
                components[component][channel] = value

    async def get_channel_values_via_rest(self, channels: list[str]):
        """Read channel values via REST API."""
        auth = aiohttp.BasicAuth(self.backend.username, self.backend.password)
        values = []

        for channel in channels:
            url = self.backend.rest_base_url.joinpath("channel", channel)
            async with (
                aiohttp.ClientSession(raise_for_status=False, auth=auth) as session,
                session.get(url) as resp,
            ):
                if resp.status != 200:
                    continue
                data = await resp.json()
                if not isinstance(data, list):
                    data = [data]
                values.extend(data)

        # convert the data format so it matches the websocket data response structure
        retval = {}
        for record in values:
            retval[record["address"]] = record["value"]
        return retval

    async def prepare_entities(self):
        """Parse json config and create class structures."""
        self.hostname = self._component_config["_host"]["Hostname"]
        if self.backend.multi_edge:
            self.hostname += " " + self.id

        for name, contents in self._component_config.items():
            if "channels" in contents:
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

    def set_component_config(self, params):
        """Store the configuration which the edge sent us."""
        self._component_config = params

    def edgeConfig(self, params):
        """Jsonrpc callback to receive edge config updates."""
        self.set_component_config(params["components"])
        # TODO: renew component channels and component info values

    def currentData(self, params):
        """Jsonrpc callback to receive channel subscription updates."""
        self.current_channel_data = params
        for key in self.current_channel_data:
            if channel := self._registered_channels.get(key):
                channel.handle_raw_value(self.current_channel_data[key])

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
    def config(self):
        "Return the edge config."
        return self._component_config

    @property
    def registered_channels(self):
        "Return the list of all subscribed channels."
        return self._registered_channels

    async def get_channel_values_via_websocket(self, channels: list[str]):
        """Read channels via dedicated websocket connection."""

        # create new connection and login
        rpc_server = jsonrpc_websocket.Server(
            url=self.backend.ws_url,
            session=None,
            heartbeat=5,
        )

        await rpc_server.ws_connect()
        _LOGGER.debug("wsocket component info request: login")
        await rpc_server.authenticateWithPassword(
            username=self.backend.username, password=self.backend.password
        )

        # prepare callback event and subscribe for the required data
        data_received: asyncio.Event | None = asyncio.Event()
        data = None

        def _handle_callback(**kwargs):
            if kwargs["payload"]["method"] != "currentData":
                # ignore
                return

            nonlocal data
            data = kwargs["payload"]["params"]
            data_received.set()

        rpc_server.edgeRpc = _handle_callback
        await rpc_server.subscribeEdges(edges=[self._id])

        subscribe_call = OpenEMSBackend.wrap_jsonrpc(
            "subscribeChannels", count=0, channels=channels
        )
        await rpc_server.edgeRpc(edgeId=self._id, payload=subscribe_call)

        # wait for the data. When received, close connection and return data
        await asyncio.wait_for(data_received.wait(), timeout=5)
        await rpc_server.close()

        return data


class OpenEMSBackend:
    """Class which represents a connection to an OpenEMS backend."""

    def __init__(self, ws_url: URL, username: str, password: str) -> None:
        """Create a new OpenEMSBackend object."""
        self.ws_url: URL = ws_url
        self.username: str = username
        self.password: str = password
        if ws_url.host == CONN_TYPES[CONN_TYPE_WEB_FENECON]["host"]:
            # Fenecon Web portal does not support REST API access
            self.rest_base_url: URL | None = None
        else:
            self.rest_base_url: URL | None = connection_url(CONN_TYPE_REST, ws_url.host)

        self.rpc_server = jsonrpc_websocket.Server(
            self.ws_url, session=None, heartbeat=5
        )
        self.rpc_server_task: asyncio.Task | None = None
        self.rpc_server.edgeRpc = self.edgeRpc
        self.multi_edge = True
        self._reconnect_task = None
        self.the_edge: OpenEMSEdge | None = None

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
        if kwargs["edgeId"] != self.the_edge.id:
            _LOGGER.error("Received response for undefined edge: %s", kwargs["edgeId"])
            return

        method_name = kwargs["payload"]["method"]
        try:
            method = getattr(self.the_edge, method_name)
        except AttributeError:
            _LOGGER.error("Unhandled callback method: %s", method_name)
            return

        params = kwargs["payload"]["params"]
        # call the edge method
        method(params)

    async def _reconnect_forever(self):
        while True:
            # check for an existing connection
            if self.rpc_server_task:
                with contextlib.suppress(
                    jsonrpc_base.jsonrpc.TransportError,
                    jsonrpc_base.jsonrpc.ProtocolError,
                ):
                    await self.rpc_server_task
                # connection lost
                self.rpc_server_task = None
                self.the_edge.set_unavailable()
                await asyncio.sleep(10)

            try:
                await self.connect_to_server()
                # connected. Lets login
                await self.login_to_server()
            except (
                jsonrpc_base.jsonrpc.TransportError,
                jsonrpc_base.jsonrpc.ProtocolError,
            ):
                await asyncio.sleep(10)

    async def connect_to_server(self):
        "Establish websocket connection."
        self.rpc_server_task = await self.rpc_server.ws_connect()

    async def login_to_server(self):
        "Authenticate with username and password."
        if not self.rpc_server.connected:
            raise ConnectionError
        retval = await self.rpc_server.authenticateWithPassword(
            username=self.username, password=self.password
        )
        if user_dict := retval.get("user"):
            self.multi_edge = user_dict["hasMultipleEdges"]
        return retval

    def start(self):
        """Start a tasks which checks for connection losses tries to reconnect afterwards."""
        self._reconnect_task = asyncio.create_task(self._reconnect_forever())

    async def stop(self):
        """Close the connection to the backend and all internal connection objects."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self.rpc_server.close()
        if self.the_edge:
            self.the_edge.stop()

    def set_component_config(self, edge_id, components: dict) -> OpenEMSEdge:
        """Prepare edge and all its components."""
        self.the_edge = OpenEMSEdge(self, edge_id)
        self.the_edge.set_component_config(components)
        return self.the_edge

    async def prepare_entities(self):
        """Parse json config of all edges."""
        await self.the_edge.prepare_entities()

    async def read_edges(self) -> dict:
        """Request list of all edges."""
        return await self.rpc_server.getEdges(page=0, limit=20, searchParams={})

    async def read_edge_components(self, edge_id: str) -> dict:
        """Request config of an edge."""
        self.the_edge = OpenEMSEdge(self, edge_id)
        return await self.the_edge.read_components()
