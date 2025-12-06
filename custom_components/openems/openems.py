"""OpenEMS API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from datetime import time
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
from yarl import URL

from .const import (
    CONN_TYPE_REST,
    CONN_TYPE_WEB_FENECON,
    CONN_TYPES,
    QUERY_CONFIG_VIA_REST,
    SLASH_ESC,
    connection_url,
)

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
    def __init__(
        self, component: OpenEMSComponent, channel_json: dict[str, Any]
    ) -> None:
        """Initialize the channel."""
        name = channel_json["id"]
        unit = channel_json["unit"]
        if (
            "category" in channel_json
            and channel_json["category"] == "ENUM"
            and "options" in channel_json
        ):
            options = {
                v: k.lower().replace(" ", "_")
                for k, v in channel_json["options"].items()
            }
        else:
            options = {}

        self.component: OpenEMSComponent = component
        self.name: str = name
        self.unit: str = unit
        self.options: dict[int, str] = options
        self.orig_json: dict[str, Any] = channel_json
        self.callback: Callable | None = None
        self._current_value: Any = None

    def handle_current_value(self, value: Any) -> None:
        """Handle a new entity value and notify Home Assistant."""
        if value != self._current_value:
            self._current_value = value
            self.notify_ha()

    def handle_data_update(self, channel_name, value: str | float | None) -> None:
        """Handle a data update from the backend."""
        if value is not None and isinstance(value, int):
            if value in self.options:
                enum_value = self.options[value]
                self.handle_current_value(enum_value)
            else:
                self.handle_current_value(value)
        else:
            self.handle_current_value(None)

    @property
    def native_value(self) -> str | int | None:
        """Return the value of the sensor."""
        if isinstance(self._current_value, (str, int)):
            return self._current_value

        return None

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
        channel_names = {self.component.name + "/" + self.name}
        self.component.edge.register_channel(channel_names, self)

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
                    for chan in self.component.properties
                    if chan.name == channel_name
                )
                properties.append(
                    (channel.name[9].lower() + channel.name[10:], channel.current_value)
                )

        await self.component.update_config(properties)


class OpenEMSEnumProperty(OpenEMSProperty):
    """Class representing a enum property of an OpenEMS component."""

    # Use with platform select
    def __init__(
        self, component: OpenEMSComponent, options: list[str], channel_json: dict
    ) -> None:
        """Initialize the channel."""
        super().__init__(component, channel_json)
        # convert options to translatable strings and store originals in a lookup map
        self.options: dict[str, str] = {v.lower().replace(" ", "_"): v for v in options}

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        if isinstance(self._current_value, str):
            return self._current_value

        return None

    def handle_data_update(self, channel_name, value: str | float | None):
        """Handle a data update from the backend."""
        if value is None:
            self.handle_current_value(None)
        elif isinstance(value, str):
            value = value.lower().replace(" ", "_")
            if value in self.options:
                self.handle_current_value(value)
            else:
                _LOGGER.warning(
                    "Received unknown value option for %s/%s: %s. Setting to status to Unknown",
                    self.name,
                    self.component.name,
                    value,
                )
                self.handle_current_value(None)
        else:
            self.handle_current_value(value)


class OpenEMSTimeProperty(OpenEMSProperty):
    """Class representing a enum property of an OpenEMS component."""

    def handle_data_update(self, channel_name, value: str | float | None):
        """Handle a data update from the backend."""
        if not isinstance(value, str):
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
        if isinstance(self._current_value, time):
            return self._current_value

        return None

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
        self.lower_limit: float = 0
        self.upper_limit: float = 100000

        self.multiplier_def: Template = Template(str(self.multiplier))
        self.lower_limit_def: Template = Template(str(self.lower_limit))
        self.upper_limit_def: Template = Template(str(self.upper_limit))

        self.step: float = 1.0
        self.reference_channels: dict[str, str | float | None] = {}

    @property
    def native_value(self) -> float | None:
        """Return the value of the number entity."""
        if isinstance(self._current_value, (float, int)):
            return self._current_value

        return None

    def handle_data_update(self, channel_name, value: str | float | None):
        """Handle a data update from the backend."""
        channel_reference = channel_name.replace("/", SLASH_ESC)
        if channel_reference in self.reference_channels:
            if self.reference_channels[channel_reference] != value:
                self.reference_channels[channel_reference] = value
                if self._update_config():
                    # config vars changed. Update the entity in HA
                    self.notify_ha()
            return

        if isinstance(value, (float, int)):
            new_val = self.multiplier * value
        else:
            new_val = None
        self.handle_current_value(new_val)

    def _update_config(self) -> bool:
        """Calculate the new multiplier, limits and step after references changed.

        Return True if at least one of these parameters changed, False otherwise.
        """
        multiplier = self.multiplier
        lower_limit = self.lower_limit
        upper_limit = self.upper_limit
        step = self.step
        if self.multiplier_def:
            try:
                render_result = self.multiplier_def.render(self.reference_channels)
                multiplier = float(render_result)
            except (ValueError, TypeError):
                multiplier = 1.0

        try:
            render_result = self.lower_limit_def.render(self.reference_channels)
            lower_noscale = float(render_result)
        except (ValueError, TypeError):
            lower_noscale = None

        try:
            render_result = self.upper_limit_def.render(self.reference_channels)
            upper_noscale = float(render_result)
        except (ValueError, TypeError):
            upper_noscale = None

        if upper_noscale is None or lower_noscale is None:
            lower_limit = 0
            upper_limit = 100000
        else:
            # assure upper limit is larger than lower limit
            if upper_noscale < lower_noscale + 10:
                upper_noscale = lower_noscale + 10
                _LOGGER.warning(
                    "Upper limit, resulting from config too small. Adjusting to %d",
                    upper_noscale,
                )
            lower_scaled = lower_noscale * multiplier
            upper_scaled = upper_noscale * multiplier
            # split range into 200, but assure min step size of 1
            min_step_range = max(
                1, (upper_scaled - lower_scaled) / OpenEMSNumberProperty.STEPS
            )
            # align step size with a power of 10
            step = 10 ** math.ceil(math.log10(min_step_range))
            lower_limit = math.ceil(float(lower_scaled) / step) * step
            upper_limit = math.ceil(float(upper_scaled) / step) * step

        if (new_config := (multiplier, lower_limit, upper_limit, step)) != (
            self.multiplier,
            self.lower_limit,
            self.upper_limit,
            self.step,
        ):
            self.multiplier, self.lower_limit, self.upper_limit, self.step = new_config
            _LOGGER.debug(
                "Config of entity %s changed: Multiplier: %d, Lower Limit: %d, Upper Limit: %d, Step: %d",
                self.name,
                self.multiplier,
                self.lower_limit,
                self.upper_limit,
                self.step,
            )
            return True

        return False

    async def update_value(self, new_value: float) -> None:
        """Handle value change request from Home Assisant."""
        await super().update_value(new_value / self.multiplier)

    def set_multiplier_def(self, multiplier_def):
        """Initialize the multiplier of the number channel."""
        self.multiplier_def, has_references = self._prepare_ref_value(multiplier_def)
        if not has_references:
            # no external references. Calculate the result immediately
            self.multiplier = float(self.multiplier_def.render())

    def set_limit_def(self, limit_def):
        """Initialize the limits of the number channel."""
        self.lower_limit_def, lower_has_references = self._prepare_ref_value(
            limit_def["lower"]
        )
        if not lower_has_references:
            # no external references. Calculate the result immediately
            self.lower_limit = float(self.lower_limit_def.render())

        self.upper_limit_def, upper_has_references = self._prepare_ref_value(
            limit_def["upper"]
        )
        if not upper_has_references:
            # no external references. Calculate the result immediately
            self.upper_limit = float(self.upper_limit_def.render())

        if not (lower_has_references or upper_has_references):
            self._update_config()

    def _prepare_ref_value(self, expr) -> tuple[Template, bool]:
        """Parse a template string into a template and channels contained."""
        has_reference = False

        def calc_component_reference(matchobj) -> str:
            nonlocal has_reference
            has_reference = True
            comp_ref, channel = matchobj.group()[2:-2].split("/")
            if comp_ref[0] == "$":
                # if the reference starts with $, treat the component like a variable,
                # to be looked up in the component properties
                # replace all linked channels with their values
                comp_ref = self.component.json_properties[comp_ref[1:]]

            # prepare value containers of required channels
            linked_channel = comp_ref + SLASH_ESC + channel
            if linked_channel not in self.reference_channels:
                self.reference_channels[linked_channel] = None
            return linked_channel

        value_expr = "{{" + re.sub(r"{{(.*?)}}", calc_component_reference, expr) + "}}"
        return Template(value_expr), has_reference

    def register_callback(self, callback: Callable):
        """Register callback."""
        self.callback = callback
        channel_names = {x.replace(SLASH_ESC, "/") for x in self.reference_channels} | {
            self.component.name + "/" + self.name,
        }
        self.component.edge.register_channel(channel_names, self)


class OpenEMSBooleanProperty(OpenEMSProperty):
    """Class representing a boolean property of an OpenEMS component."""

    # Use with platform switch

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        return self._current_value

    def handle_data_update(self, channel_name, value: str | float | None):
        """Handle a data update from the backend."""
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
        self.json_properties: dict = json_def["properties"]
        self.sensors: list[OpenEMSChannel] = []
        self.boolean_sensors: list[OpenEMSChannel] = []
        self.enum_properties: list[OpenEMSEnumProperty] = []
        self.number_properties: list[OpenEMSNumberProperty] = []
        self.boolean_properties: list[OpenEMSBooleanProperty] = []
        self.time_properties: list[OpenEMSTimeProperty] = []
        self.create_entities: bool = False

    async def init_channels(self, channels: list[dict[str, Any]]):
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
                        if options_conf := (
                            # options received from backend are preferred over configured options
                            channel_json.pop("options", None)
                            or CONFIG.get_enum_options(self.name, channel_json["id"])
                        ):
                            prop = OpenEMSEnumProperty(
                                component=self,
                                options=options_conf,
                                channel_json=channel_json,
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
                            try:
                                multiplier = CONFIG.get_number_multiplier(
                                    self.name, channel_json["id"]
                                )
                                prop = OpenEMSNumberProperty(
                                    component=self, channel_json=channel_json
                                )
                                if multiplier is not None:
                                    prop.set_multiplier_def(multiplier)
                                prop.set_limit_def(limit_def)
                                self.number_properties.append(prop)
                            except (
                                TypeError,
                                jsonrpc_base.jsonrpc.TransportError,
                                jsonrpc_base.jsonrpc.ProtocolError,
                                ValueError,
                            ):
                                _LOGGER.warning(
                                    "Error during initialization of channel %s/%s",
                                    self.name,
                                    channel_json["id"],
                                    exc_info=True,
                                )

            else:
                channel = OpenEMSChannel(component=self, channel_json=channel_json)
                match channel_json["type"]:
                    case "BOOLEAN":
                        self.boolean_sensors.append(channel)
                    case _:
                        self.sensors.append(channel)

    async def update_config(self, channels: list[tuple[str, Any]]):
        """Send updateComponentConfig request to backend."""
        properties = [{"name": chan[0], "value": chan[1]} for chan in channels]
        if not self.edge.backend.rpc_server.connected:
            _LOGGER.error(
                'No connection to backend. Cannot send "updateComponentConfig" message for component %s with properties: %s',
                self.name,
                str(properties),
            )
            return

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
            *self.properties,
            *self.sensors,
            *self.boolean_sensors,
        ]

    @property
    def properties(self) -> list[OpenEMSProperty]:
        """Return all properties of the component (all platforms)."""
        return [
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
            self._count = 0

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
                                self._count = 0
                            else:
                                self._count += 1

                            subscribe_call = OpenEMSBackend.wrap_jsonrpc(
                                "subscribeChannels",
                                count=self._count,
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
        self._component_config: dict[str, dict] = {}
        self.components: dict[str, OpenEMSComponent] = {}
        self.current_channel_data: dict[str, Any] = {}
        self._channel_subscription_updater = self.OpenEmsEdgeChannelSubscriptionUpdater(
            self
        )
        self.hostname: str = ""
        self._registered_channels: dict[str, set[OpenEMSChannel]] = {}

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
        for componentId in list(components):
            edge_component_call = OpenEMSBackend.wrap_jsonrpc(
                "getChannelsOfComponent",
                componentId=componentId,
            )
            try:
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
            except (
                jsonrpc_base.jsonrpc.TransportError,
                jsonrpc_base.jsonrpc.ProtocolError,
            ):
                _LOGGER.warning(
                    "_read_edge_channels: could not read channels of component %s, skipping",
                    componentId,
                )
                del components[componentId]

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

    async def get_channel_values_via_rest(self, channels: list[str]) -> dict:
        """Read channel values via REST API."""
        if self.backend.rest_base_url is None:
            return {}

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
        for channel_name in self.current_channel_data:
            for channel in self._registered_channels[channel_name]:
                channel.handle_data_update(channel_name, None)
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

    def currentData(self, params: dict[str, str | float | None]):
        """Jsonrpc callback to receive channel subscription updates."""
        self.current_channel_data = params
        for channel_name, value in params.items():
            registered_channels = self._registered_channels.get(channel_name)
            if not registered_channels:
                _LOGGER.debug(
                    "Received data update for unsubscribed channel: %s", channel_name
                )
                continue
            for channel in registered_channels:
                channel.handle_data_update(channel_name, value)

    def register_channel(self, channel_names: set[str], handler: OpenEMSChannel):
        """Register a channel and its dependent channels for updates."""
        for channel_name in channel_names:
            if channel_name not in self._registered_channels:
                self._registered_channels[channel_name] = {handler}
            else:
                self._registered_channels[channel_name].add(handler)

    def unregister_channel(self, handler: OpenEMSChannel):
        """Remove a channel from receiving updates."""
        for channel_name, handlers in list(self._registered_channels.items()):
            if handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    del self._registered_channels[channel_name]

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

    async def get_channel_values_via_websocket(self, channels: list[str]) -> dict:
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
        await rpc_server.subscribeEdges(edges=[self._id])

        subscribe_call = OpenEMSBackend.wrap_jsonrpc(
            "subscribeChannels", count=0, channels=channels
        )
        await rpc_server.edgeRpc(edgeId=self._id, payload=subscribe_call)  # pyright: ignore[reportGeneralTypeIssues]

        # wait for the data. When received, close connection and return data
        await asyncio.wait_for(data_received.wait(), timeout=5)
        await rpc_server.close()

        return data

    async def get_system_update_state(self) -> dict:
        """Read getSystemUpdateState response."""
        system_update_state_call = OpenEMSBackend.wrap_jsonrpc("getSystemUpdateState")
        edge_call = OpenEMSBackend.wrap_jsonrpc(
            "componentJsonApi", componentId="_host", payload=system_update_state_call
        )
        result = await self.backend.rpc_server.edgeRpc(
            edgeId=self._id, payload=edge_call
        )
        return result["payload"]["result"]

    async def execute_system_update(self) -> None:
        """Trigger executeSystemUpdate request."""
        system_update_state_call = OpenEMSBackend.wrap_jsonrpc(
            "executeSystemUpdate", isDebug=False
        )
        edge_call = OpenEMSBackend.wrap_jsonrpc(
            "componentJsonApi", componentId="_host", payload=system_update_state_call
        )
        await self.backend.rpc_server.edgeRpc(edgeId=self._id, payload=edge_call)


class OpenEMSBackend:
    """Class which represents a connection to an OpenEMS backend."""

    def __init__(self, ws_url: URL, username: str, password: str) -> None:
        """Create a new OpenEMSBackend object."""
        self.ws_url: URL = ws_url
        self.username: str = username
        self.password: str = password
        use_rest: bool = (
            # Only if REST is explicitly enabled
            QUERY_CONFIG_VIA_REST
            # Fenecon Web portal does not support REST API access
            and ws_url.host != CONN_TYPES[CONN_TYPE_WEB_FENECON]["host"]
        )
        self.rest_base_url: URL | None = (
            connection_url(CONN_TYPE_REST, ws_url.host) if use_rest else None
        )

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
                _LOGGER.info("Connection to backend %s lost", self.the_edge.hostname)
                self.rpc_server_task = None
                self.the_edge.set_unavailable()
                await asyncio.sleep(10)

            try:
                await self.connect_to_server()
                _LOGGER.debug("Reconnected to backend %s", self.the_edge.hostname)
                # connected. Lets login
                await self.login_to_server()
                _LOGGER.info(
                    "Connection to backend %s reestablished", self.the_edge.hostname
                )
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
