"""OpenEMS API."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections.abc import Callable
from datetime import time
import logging
import math
from typing import Any, NamedTuple

import aiohttp
from jinja2 import Template
import jsonrpc_base
from yarl import URL

from .config import OpenEMSConfig
from .const import CONN_TYPE_REST, CONN_TYPE_WEB_FENECON, CONN_TYPES, SLASH_ESC
from .entry_data import OpenEMSWebSocketConnection
from .helpers import connection_url, wrap_jsonrpc
from .helpers_openems import expand_sensor_def, prepare_ref_value

_LOGGER = logging.getLogger(__name__)


class RestDetails(NamedTuple):
    """Details about the REST connection."""

    mode: str
    url: URL


CONFIG: OpenEMSConfig = OpenEMSConfig()


class OpenEMSDataHandler:
    """Interface for handling data updates from the backend."""

    def __init__(self, component: OpenEMSComponent, name: str) -> None:
        """Initialize the handler."""
        self.component: OpenEMSComponent = component
        self.name: str = name
        self.callback: Callable | None = None
        self._current_value: Any = None

    @abstractmethod
    def handle_data_update(self, channel_name, value: str | float | None) -> None:
        """Handle a data update from the backend."""

    @abstractmethod
    def register_callback(self, callback: Callable):
        """Register callback."""

    @abstractmethod
    def unregister_callback(self):
        """Remove callback."""

    @property
    def native_value(self) -> float | None:
        """Return the value of the channel."""

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


class OpenEMSChannel(OpenEMSDataHandler):
    """Class representing a sensor of an OpenEMS component."""

    # Use with platform sensor
    def __init__(
        self,
        component: OpenEMSComponent,
        channel_json: dict[str, Any],
        options: dict[str, int] | None = None,
    ) -> None:
        """Initialize the channel."""
        super().__init__(component, channel_json["id"])
        unit = channel_json["unit"]
        self.options: dict[int, str] | None = None
        if (
            "category" in channel_json
            and channel_json["category"] == "ENUM"
            and options is not None
        ):
            self.options = {v: k for k, v in options.items()}

        self.unit: str = unit
        self.orig_json: dict[str, Any] = channel_json
        self._rest_update_task: asyncio.Task | None = None

    def handle_current_value(self, value: Any) -> None:
        """Handle a new entity value and notify Home Assistant."""
        if value != self._current_value:
            self._current_value = value
            self.notify_ha()

    def handle_data_update(self, channel_name, value: str | float | None) -> None:
        """Handle a data update from the backend."""
        if value is not None and isinstance(value, int):
            if self.options is not None:
                self.handle_current_value(self.options.get(value))
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

    def register_callback(self, callback: Callable):
        """Register callback."""
        self.callback = callback
        channel_names = {self.component.name + "/" + self.name}
        self.component.edge.register_channel(channel_names, self)

    def unregister_callback(self):
        """Remove callback."""
        self.callback = None
        if self._rest_update_task is not None:
            self._rest_update_task.cancel()
            self._rest_update_task = None
        self.component.edge.unregister_channel(self)

    async def update_value(
        self, new_value: float | bool, update_cycle: int, timeout: int
    ):
        """Handle value change request from Home Assisant."""
        edge = self.component.edge
        backend = edge.backend
        # Note: This is not a component property.
        # This means the value change request must be sent via REST.
        if not edge.rest or edge.rest.mode != "ReadWrite":
            _LOGGER.error(
                "Cannot update channel %s/%s: No REST write app found",
                self.component.name,
                self.name,
            )
            return

        # clean up a previously running cyclic update task
        if self._rest_update_task is not None:
            self._rest_update_task.cancel()
            self._rest_update_task = None

        # prepare REST URL and data
        url = edge.rest.url.joinpath("channel", self.component.name, self.name)
        data = {"value": new_value}

        async def _rest_update_cyclic():
            """Send REST update command cyclically."""
            while True:
                await _rest_update_oneshot()
                await asyncio.sleep(update_cycle)

        async def _rest_update_oneshot():
            """Send REST update command."""
            auth = aiohttp.BasicAuth(
                backend.connection.username, backend.connection.password
            )
            try:
                async with (
                    aiohttp.ClientSession(raise_for_status=False, auth=auth) as session,
                    session.post(url, json=data) as resp,
                ):
                    if resp.status != 200:
                        _LOGGER.error(
                            "Error during REST call to update channel %s/%s: HTTP %d: %s",
                            self.component.name,
                            self.name,
                            resp.status,
                            resp.reason,
                        )
            except aiohttp.ClientError as exc:
                _LOGGER.error(
                    "Cannot send REST update command via URL %s for channel %s/%s: %s",
                    str(url),
                    self.component.name,
                    self.name,
                    str(exc),
                )

        if update_cycle <= 0:
            # one shot
            await _rest_update_oneshot()
        else:
            # schedule cyclically
            my_timeout = timeout if timeout > 0 else None
            self._rest_update_task = asyncio.create_task(_rest_update_cyclic())
            try:
                await asyncio.wait_for(self._rest_update_task, timeout=my_timeout)
            except TimeoutError:
                self._rest_update_task = None
            except asyncio.CancelledError:
                # another update task took over. Dont touch the task reference.
                pass

        _LOGGER.info(
            "Update value service task completed for channel %s/%s",
            self.component.name,
            self.name,
        )


class OpenEMSDerivedChannel(OpenEMSDataHandler):
    """Class representing a derived sensor of an OpenEMS component."""

    def __init__(
        self, component: OpenEMSComponent, combined_sensor_def: dict[str, Any]
    ) -> None:
        """Initialize the derived channel."""
        super().__init__(component, combined_sensor_def["id"])
        self.sensor_template: Template
        self.unit: str = combined_sensor_def["unit_of_measurement"]
        self.reference_channels: dict[str, str | float | None] = {}
        self.sensor_template, sensor_references = prepare_ref_value(
            combined_sensor_def["template"], component
        )
        self.reference_channels = dict.fromkeys(sensor_references)

    def register_callback(self, callback: Callable):
        """Register callback."""
        self.callback = callback
        channel_names = {x.replace(SLASH_ESC, "/") for x in self.reference_channels} | {
            self.component.name + "/" + self.name,
        }
        self.component.edge.register_channel(channel_names, self)

    def unregister_callback(self):
        """Remove callback."""
        self.callback = None
        self.component.edge.unregister_channel(self)

    def handle_data_update(self, channel_name, value: str | float | None) -> None:
        """Handle a data update from the backend."""
        channel_reference = channel_name.replace("/", SLASH_ESC)
        if channel_reference in self.reference_channels:
            if self.reference_channels[channel_reference] != value:
                self.reference_channels[channel_reference] = value

                try:
                    render_result = self.sensor_template.render(self.reference_channels)
                    channel_value = float(render_result)
                except ValueError, TypeError:
                    channel_value = None

                if channel_value != self._current_value:
                    self._current_value = channel_value
                    self.notify_ha()
            return

    @property
    def native_value(self) -> float | None:
        """Return the value of the derived channel."""
        if isinstance(self._current_value, (float, int)):
            return self._current_value

        return None


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
        self, component: OpenEMSComponent, channel_json: dict, options: list[str]
    ) -> None:
        """Initialize the channel."""
        super().__init__(component, channel_json)
        self.property_options: list[str] = options

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
            if value in self.property_options:
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
    """Class representing a time property of an OpenEMS component."""

    # Use with platform time
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
        """Return the value of the number property."""
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
            except ValueError, TypeError:
                multiplier = 1.0

        try:
            render_result = self.lower_limit_def.render(self.reference_channels)
            lower_noscale = float(render_result)
        except ValueError, TypeError:
            lower_noscale = None

        try:
            render_result = self.upper_limit_def.render(self.reference_channels)
            upper_noscale = float(render_result)
        except ValueError, TypeError:
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
        self.multiplier_def, multiplier_references = prepare_ref_value(
            multiplier_def, self.component
        )
        if not multiplier_references:
            # no external references. Calculate the result immediately
            self.multiplier = float(self.multiplier_def.render())
        else:
            for ref in multiplier_references:
                self.reference_channels[ref] = None

    def set_limit_def(self, limit_def):
        """Initialize the limits of the number channel."""
        self.lower_limit_def, lower_references = prepare_ref_value(
            limit_def["lower"], self.component
        )
        if not lower_references:
            # no external references. Calculate the result immediately
            self.lower_limit = float(self.lower_limit_def.render())
        else:
            for ref in lower_references:
                self.reference_channels[ref] = None

        self.upper_limit_def, upper_references = prepare_ref_value(
            limit_def["upper"], self.component
        )
        if not upper_references:
            # no external references. Calculate the result immediately
            self.upper_limit = float(self.upper_limit_def.render())
        else:
            for ref in upper_references:
                self.reference_channels[ref] = None

        if not (lower_references or upper_references):
            self._update_config()

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
        self.derived_sensors: list[OpenEMSDerivedChannel] = []
        self.create_entities: bool = False

    def init_channels(self, channels: list[dict[str, Any]]):
        """Parse and initialize the components channels."""
        for channel_json in channels:
            options_backend: dict[str, int] | list[str] | None = channel_json.pop(
                "options", None
            )
            if channel_json["id"].startswith("_Property"):
                # scan type and convert to property
                match channel_json["type"]:
                    case "BOOLEAN":
                        prop = OpenEMSBooleanProperty(
                            component=self, channel_json=channel_json
                        )
                        self.boolean_properties.append(prop)
                    case "STRING":
                        options = (
                            # options received from backend are preferred over configured options
                            options_backend
                            if isinstance(options_backend, list)
                            else CONFIG.get_enum_options(self.name, channel_json["id"])
                        )
                        if options is not None:
                            prop = OpenEMSEnumProperty(
                                component=self,
                                channel_json=channel_json,
                                options=options,
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
                options = options_backend if isinstance(options_backend, dict) else None
                channel = OpenEMSChannel(
                    component=self, channel_json=channel_json, options=options
                )
                match channel_json["type"]:
                    case "BOOLEAN":
                        self.boolean_sensors.append(channel)
                    case _:
                        self.sensors.append(channel)
        # prepare derived sensors.
        for sensor_def in CONFIG.get_combined_sensors(self.name):
            # 1st step: map variables in config ids to concrete names
            expanded_sensor_defs = expand_sensor_def(
                sensor_def, [c["id"] for c in channels]
            )
            # 2nd step: create all channels
            for expanded_sensor_def in expanded_sensor_defs:
                self.derived_sensors.append(
                    OpenEMSDerivedChannel(self, expanded_sensor_def)
                )

    async def update_config(self, channels: list[tuple[str, Any]]):
        """Send updateComponentConfig request to backend."""
        properties = [{"name": chan[0], "value": chan[1]} for chan in channels]
        if not self.edge.backend.connection.rpc_server.connected:
            _LOGGER.error(
                'No connection to backend. Cannot send "updateComponentConfig" message for component %s with properties: %s',
                self.name,
                str(properties),
            )
            return

        envelope = wrap_jsonrpc(
            "updateComponentConfig", componentId=self.name, properties=properties
        )
        _LOGGER.debug(
            "updateComponentConfig: component: %s, properties: %s",
            self.name,
            str(properties),
        )
        await self.edge.backend.connection.rpc_server.edgeRpc(
            edgeId=self.edge.id, payload=envelope
        )

    @property
    def channels(self) -> list[OpenEMSDataHandler]:
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
                        self._edge.backend.connection.rpc_server.connected
                        and subscribe_in_progress_channels != self._active_subscriptions
                    ):
                        try:
                            if not self._active_subscriptions:
                                # no active subscription, so subscribe for the edge
                                await self._edge.backend.connection.rpc_server.subscribeEdges(
                                    edges=[self._edge.id]
                                )
                                self._count = 0
                            else:
                                self._count += 1

                            subscribe_call = wrap_jsonrpc(
                                "subscribeChannels",
                                count=self._count,
                                channels=subscribe_in_progress_channels,
                            )
                            await self._edge.backend.connection.rpc_server.edgeRpc(
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

    def __init__(
        self, backend: OpenEMSBackend, id: str, component_config: dict[str, dict]
    ) -> None:
        """Initialize the edge."""
        self.backend: OpenEMSBackend = backend
        self._id: str = id
        self.current_channel_data: dict[str, Any] = {}
        self._channel_subscription_updater = self.OpenEmsEdgeChannelSubscriptionUpdater(
            self
        )
        self._registered_handlers: dict[str, set[OpenEMSDataHandler]] = {}
        self.hostname: str = component_config["_host"]["Hostname"]
        if self.backend.multi_edge:
            self.hostname += " " + self.id

        self.components: dict[str, OpenEMSComponent] = self._prepare_entities(
            component_config
        )
        self.rest: RestDetails | None = self._prepare_rest(component_config)

    def _prepare_entities(
        self, component_config: dict[str, dict]
    ) -> dict[str, OpenEMSComponent]:
        """Parse json config and create class structures."""
        components: dict[str, OpenEMSComponent] = {}
        for name, contents in component_config.items():
            if "channels" in contents:
                component: OpenEMSComponent = OpenEMSComponent(self, name, contents)
                component.init_channels(contents["channels"])
                # create the entities within a service which linked to the edge device
                components[name] = component
        return components

    def _prepare_rest(self, component_config: dict[str, dict]) -> RestDetails | None:
        # Fenecon Web portal does not support REST API access
        if (
            self.backend.connection.conn_url.host
            == CONN_TYPES[CONN_TYPE_WEB_FENECON]["host"]
        ):
            return None
        for comp_content in component_config.values():
            if comp_content.get("factoryId", "").startswith("Controller.Api.Rest"):
                rest_mode = comp_content["factoryId"][20:]
                rest_url = connection_url(
                    CONN_TYPE_REST, self.backend.connection.conn_url.host
                )
                rest_url = rest_url.with_port(
                    int(comp_content["properties"].get("port", rest_url.port))
                )
                return RestDetails(rest_mode, rest_url)
        # No REST App found
        return None

    def set_unavailable(self):
        """Set all active entities to unavailable and clear the subscription indicator."""
        # Note: This method is called by the connection logic on connection loss.
        for channel_name in self.current_channel_data:
            for handler in self._registered_handlers[channel_name]:
                handler.handle_data_update(channel_name, None)
        self._channel_subscription_updater.clear()

    def stop(self):
        """Stop the connection to edge and all its subscriptions."""
        self._channel_subscription_updater.stop()

    def edgeConfig(self, params):
        """Jsonrpc callback to receive edge config updates."""
        # TODO: renew component channels and component info values
        # self._prepare_entities(params["components"])

    def currentData(self, params: dict[str, str | float | None]):
        """Jsonrpc callback to receive channel subscription updates."""
        self.current_channel_data = params
        for channel_name, value in params.items():
            registered_handlers = self._registered_handlers.get(channel_name)
            if not registered_handlers:
                _LOGGER.debug(
                    "Received data update for unsubscribed channel: %s", channel_name
                )
                continue
            for handler in registered_handlers:
                handler.handle_data_update(channel_name, value)

    def register_channel(self, channel_names: set[str], handler: OpenEMSDataHandler):
        """Register a channel and its dependent channels for updates."""
        for channel_name in channel_names:
            if channel_name not in self._registered_handlers:
                self._registered_handlers[channel_name] = {handler}
            else:
                self._registered_handlers[channel_name].add(handler)

    def unregister_channel(self, handler: OpenEMSDataHandler):
        """Remove a channel from receiving updates."""
        for channel_name, handlers in list(self._registered_handlers.items()):
            if handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    del self._registered_handlers[channel_name]

    @property
    def id(self):
        """Return the edge ID."""
        return self._id

    @property
    def registered_channels(self):
        "Return the list of all subscribed channels."
        return self._registered_handlers

    async def get_system_update_state(self) -> dict:
        """Read getSystemUpdateState response."""
        system_update_state_call = wrap_jsonrpc("getSystemUpdateState")
        edge_call = wrap_jsonrpc(
            "componentJsonApi", componentId="_host", payload=system_update_state_call
        )
        result = await self.backend.connection.rpc_server.edgeRpc(
            edgeId=self._id, payload=edge_call
        )
        return result["payload"]["result"]

    async def execute_system_update(self) -> None:
        """Trigger executeSystemUpdate request."""
        system_update_state_call = wrap_jsonrpc("executeSystemUpdate", isDebug=False)
        edge_call = wrap_jsonrpc(
            "componentJsonApi", componentId="_host", payload=system_update_state_call
        )
        await self.backend.connection.rpc_server.edgeRpc(
            edgeId=self._id, payload=edge_call
        )


class OpenEMSBackend:
    """Class which represents a connection to an OpenEMS backend."""

    def __init__(
        self,
        connection: OpenEMSWebSocketConnection,
        edge_id: str,
        multi_edge: bool,
        components: dict[str, dict],
    ) -> None:
        """Create a new OpenEMSBackend object."""
        self.connection = connection
        self.multi_edge = multi_edge

        connection.rpc_server.edgeRpc = self.edgeRpc
        self.the_edge = OpenEMSEdge(self, edge_id, components)

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

    def start(self):
        """Start to subscribe for updates to the edge."""
        self.connection.enable_reconnect(self.the_edge.set_unavailable)

    async def stop(self):
        """Close the connection to the backend and all internal connection objects."""
        await self.connection.stop()
        if self.the_edge:
            self.the_edge.stop()
