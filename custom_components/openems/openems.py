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

# import homeassistant.components.openems.sensor.OpenEMSSensorEntity as OpenEMSSensorEntity
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import exceptions


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
                if subscribe_in_progress_channels != self._active_subscriptions:
                    subscribe_call = OpenEMSBackend.wrap_jsonrpc(
                        "subscribeChannels",
                        count=0,
                        channels=subscribe_in_progress_channels,
                    )
                    r = await self._edge._backend.server.edgeRpc(
                        edgeId=self._edge.id_str, payload=subscribe_call
                    )
                    # TODO: check response code
                    self._active_subscriptions = subscribe_in_progress_channels

    def __init__(self, backend, id) -> None:
        """Initialize the edge."""
        self._backend: OpenEMSBackend = backend
        self._id: int = id
        self._edge_config: dict[str, dict] | None = None
        self._available_channels: dict[str, dict] = {}
        self._data_event: asyncio.Event | None = asyncio.Event()
        self.current_channel_data: dict | None = None
        self.config_values: dict | None = None
        self._channel_subscription_updater = self.OpenEmsEdgeChannelSubscriptionUpdater(
            self
        )
        self._callbacks = {}

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

    def currentData(self, params):
        """Jsonrpc callback to receive channel subscription updates."""
        self.current_channel_data = params
        for key in self.current_channel_data:
            callback = self._callbacks.get(key)
            if callback:
                callback(key, self.current_channel_data[key])
        self._data_event.set()
        self._data_event.clear()

    async def read_components(self):
        # Load channels of each component
        for componentId in self._edge_config:
            edge_component_call = OpenEMSBackend.wrap_jsonrpc(
                "getChannelsOfComponent",
                componentId=componentId,
            )
            edge_call = OpenEMSBackend.wrap_jsonrpc(
                "componentJsonApi",
                componentId="_componentManager",
                payload=edge_component_call,
            )
            r = await self._backend.server.edgeRpc(
                edgeId=self.id_str,
                payload=edge_call,
            )
            self._available_channels[componentId] = r["payload"]["result"]["channels"]

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
    def available_channels(self):
        return self._available_channels

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
        self.server = jsonrpc_websocket.Server(websocket_url)
        self.server.edgeRpc = self.edgeRpc
        self.username: str = config[CONF_USERNAME]
        self.password: str = config[CONF_PASSWORD]
        self.edges: dict[int, OpenEMSEdge] = {}
        self.multi_edge = True

    async def stop(self):
        await self.server.close()
        for edge in self.edges.values():
            edge.stop()

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

    async def check_login(self):
        await self.do_login()
        await self.server.close()

    async def do_login(self):
        if not self.server.connected:
            await self.server.ws_connect()
            retval = await self.server.authenticateWithPassword(
                username=self.username, password=self.password
            )
            self.multi_edge = retval["user"]["hasMultipleEdges"]

    async def read_edge_config(self):
        """Request list of all edges and their config."""
        self.edges = {}
        r = await self.server.getEdges(page=0, limit=20, searchParams={})
        json_edges = r["edges"]
        for json_edge in json_edges:
            edge_id = json_edge["id"]
            if edge_id not in self.edges:
                self.edges[edge_id] = OpenEMSEdge(self, edge_id)
            edge = self.edges[edge_id]

            # Load edgeConfig
            edge_call = OpenEMSBackend.wrap_jsonrpc("getEdgeConfig")
            r = await self.server.edgeRpc(edgeId=edge.id_str, payload=edge_call)
            edge.set_config(r["payload"]["result"]["components"])
            # read config values from selected channels
            await self.read_component_info_channels(edge_id)

    async def subscribe_for_config_changes(self, edge_id):
        """Subscribe for edgeConfig updates."""
        return await self.server.subscribeEdges(edges=json.dumps([edge_id]))

    async def read_component_info_channels(self, edge_id):
        """Read hostname and all component names of an edge."""
        edge: OpenEMSEdge = self.edges[edge_id]
        if edge.config_values:
            return

        config_channels = ["_host/Hostname"]
        config_channels.extend(comp + "/_PropertyAlias" for comp in edge.config)

        # Subscribe channels
        edge_call = OpenEMSBackend.wrap_jsonrpc(
            "subscribeChannels", count=0, channels=config_channels
        )
        await self.server.edgeRpc(edgeId=edge.id_str, payload=edge_call)
        # wait for data
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(edge.wait_for_current_data(), timeout=1)
        edge.config_values = edge.current_channel_data
        # Unsubscribe channels
        edge_call = OpenEMSBackend.wrap_jsonrpc(
            "subscribeChannels", count=0, channels=[]
        )
        await self.server.edgeRpc(edgeId=edge.id_str, payload=edge_call)

    async def update_component_config(self, edge_id, component_id, properties):
        try:
            await self.do_login()
            envelope = OpenEMSBackend.wrap_jsonrpc(
                "updateComponentConfig", componentId=component_id, properties=properties
            )
            r_edge_rpc = await self.server.edgeRpc(edgeId=edge_id, payload=envelope)
        except jsonrpc_base.jsonrpc.ProtocolError as e:
            if type(e.args) is tuple:
                raise exceptions.APIError(
                    message=f"{e.args[0]}: {e.args[1]}", code=e.args[0]
                )
            raise e
        r = r_edge_rpc["payload"]["result"]
        return r
