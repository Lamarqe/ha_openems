"""OpenEMS API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import contextlib
import json
from typing import Any
import uuid

import jsonrpc_base
import jsonrpc_websocket

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import exceptions

# import homeassistant.components.openems.sensor.OpenEMSSensorEntity as OpenEMSSensorEntity


class OpenEMSEdge:
    """Class representing an OpenEMS Edge device."""

    DEFAULT_CHANNELS = [
        "_sum/State",
        "_sum/EssSoc",
        "_sum/EssActivePower",
        "_sum/EssMinDischargePower",
        "_sum/EssMaxDischargePower",
        "_sum/GridActivePower",
        "_sum/GridMinActivePower",
        "_sum/GridMaxActivePower",
        "_sum/GridMode",
        "_sum/ProductionActivePower",
        "_sum/ProductionDcActualPower",
        "_sum/ProductionAcActivePower",
        "_sum/ProductionMaxActivePower",
        "_sum/ConsumptionActivePower",
        "_sum/ConsumptionMaxActivePower",
        "_sum/EssActivePowerL1",
        "_sum/EssActivePowerL2",
        "_sum/EssActivePowerL3",
        "ctrlPrepareBatteryExtension0/CtrlIsBlockingEss",
        "ctrlPrepareBatteryExtension0/CtrlIsChargingEss",
        "ctrlPrepareBatteryExtension0/CtrlIsDischargingEss",
        "ctrlPrepareBatteryExtension0/_PropertyIsRunning",
        "ctrlPrepareBatteryExtension0/_PropertyTargetTimeSpecified",
        "ctrlPrepareBatteryExtension0/_PropertyTargetTime",
        "ctrlEmergencyCapacityReserve0/_PropertyReserveSoc",
        "ctrlEmergencyCapacityReserve0/_PropertyIsReserveSocEnabled",
        "charger0/ActualPower",
        "charger1/ActualPower",
        "ess0/Soc",
        "ess0/Capacity",
        "_sum/GridActivePowerL1",
        "_sum/GridActivePowerL2",
        "_sum/GridActivePowerL3",
        "ctrlEssLimiter14a0/RestrictionMode",
        "_sum/ConsumptionActivePowerL1",
        "_sum/ConsumptionActivePowerL2",
        "_sum/ConsumptionActivePowerL3",
        "evcs0/ActivePower",
        "evcs0/ActivePowerL1",
        "evcs0/ActivePowerL2",
        "evcs0/ActivePowerL3",
        "meter2/ActivePower",
        "meter2/ActivePowerL1",
        "meter2/ActivePowerL2",
        "meter2/ActivePowerL3",
        "evcs0/ChargePower",
        "evcs0/Phases",
        "evcs0/Plug",
        "evcs0/Status",
        "evcs0/State",
        "evcs0/EnergySession",
        "evcs0/MinimumHardwarePower",
        "evcs0/MaximumHardwarePower",
        "evcs0/SetChargePowerLimit",
        "ctrlEvcs0/_PropertyEnabledCharging",
        "ctrlGridOptimizedCharge0/DelayChargeState",
        "ctrlGridOptimizedCharge0/SellToGridLimitState",
        "ctrlGridOptimizedCharge0/DelayChargeMaximumChargeLimit",
        "ctrlGridOptimizedCharge0/SellToGridLimitMinimumChargeLimit",
        "ctrlGridOptimizedCharge0/_PropertyMode",
        "ess0/DcDischargePower",
        "pvInverter0/ActivePower",
    ]

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
                    self._edge._backend.rpc_server.connected
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
                        await self._edge._backend.rpc_server.edgeRpc(
                            edgeId=self._edge.id_str, payload=subscribe_call
                        )
                        self._active_subscriptions = subscribe_in_progress_channels

    def __init__(self, backend, id) -> None:
        """Initialize the edge."""
        self._backend: OpenEMSBackend = backend
        self._id: int = id
        self._edge_config: dict[str, dict] | None = None
        self._data_event: asyncio.Event | None = asyncio.Event()
        self.current_channel_data: dict | None = None
        self._channel_subscription_updater = self.OpenEmsEdgeChannelSubscriptionUpdater(
            self
        )
        self._callbacks = {}

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
            print("Unhandled callback method: ", method)
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

    async def subscribe_for_config_changes(self, edge_id):
        """Subscribe for edgeConfig updates."""
        return await self.rpc_server.subscribeEdges(edges=json.dumps([edge_id]))

    async def update_component_config(self, edge_id, component_id, properties):
        try:
            await self.start()
            envelope = OpenEMSBackend.wrap_jsonrpc(
                "updateComponentConfig", componentId=component_id, properties=properties
            )
            r_edge_rpc = await self.rpc_server.edgeRpc(edgeId=edge_id, payload=envelope)
        except jsonrpc_base.jsonrpc.ProtocolError as e:
            if type(e.args) is tuple:
                raise exceptions.APIError(
                    message=f"{e.args[0]}: {e.args[1]}", code=e.args[0]
                )
            raise e
        r = r_edge_rpc["payload"]["result"]
        return r

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
