"""Config flow for the HA OpenEMS integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import jsonrpc_base
import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_BASE, CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    BooleanSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .__init__ import OpenEMSConfigEntry
from .const import CONF_EDGE, CONF_EDGES, DOMAIN
from .openems import CONFIG, OpenEMSBackend

_LOGGER = logging.getLogger(__name__)


def step_user_data_schema(user_input=None) -> vol.Schema:
    """Define the config flow input options."""
    default_host = user_input[CONF_HOST] if user_input else ""
    default_user = user_input[CONF_USERNAME] if user_input else "x"
    default_pass = user_input[CONF_PASSWORD] if user_input else ""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=default_host): str,
            vol.Required(CONF_USERNAME, default=default_user): str,
            vol.Required(CONF_PASSWORD, default_pass): str,
        }
    )


def step_edges_data_schema(edge_response: dict, user_input=None) -> vol.Schema:
    """Define the edges step input options."""
    return vol.Schema(
        {
            vol.Required(
                CONF_EDGES,
                default=user_input.get(CONF_EDGES) if user_input else None,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        e["id"] for e in edge_response["edges"] if e.get("isOnline")
                    ],
                    multiple=False,
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


class OpenEMSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA OpenEMS."""

    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._config_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        data_schema = step_user_data_schema(user_input)
        if user_input is not None:
            backend = OpenEMSBackend(
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            self._config_data = user_input
            try:
                # connect
                await asyncio.wait_for(backend.connect_to_server(), timeout=2)
            except jsonrpc_base.TransportError as te:
                errors[CONF_HOST] = f"{te.args[0]}: {te.args[1]}"
            try:
                # login
                await asyncio.wait_for(backend.login_to_server(), timeout=2)
            except jsonrpc_base.jsonrpc.ProtocolError as pe:
                errors[CONF_PASSWORD] = f"{pe.args[0]}: {pe.args[1]}"
            try:
                edges = await asyncio.wait_for(backend.read_edges(), timeout=2)
                if backend.multi_edge:
                    edges_schema = step_edges_data_schema(edges)
                    return self.async_show_form(
                        step_id="edges",
                        data_schema=edges_schema,
                    )

                # single edge backend, all required data has been received.
                self._config_data[CONF_EDGE] = next(iter(edges["edges"]))["id"]
                return await self._create_or_update_entry(backend)

            except (KeyError, jsonrpc_base.jsonrpc.ProtocolError):
                _LOGGER.exception("Cannot read edge components")
                errors[CONF_BASE] = "Cannot read edge components"

            await backend.stop()
        # show errors in form
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def _create_or_update_entry(
        self, backend: OpenEMSBackend | None = None
    ) -> ConfigFlowResult:
        # backend will be closed in case of success.
        # in case of exceptions, caller must stop the backend.
        if not backend:
            local_backend: OpenEMSBackend = OpenEMSBackend(
                self._config_data[CONF_HOST],
                self._config_data[CONF_USERNAME],
                self._config_data[CONF_PASSWORD],
            )
            await local_backend.connect_to_server()
            await local_backend.login_to_server()
            backend = local_backend
        else:
            local_backend = None

        # read edge components to validate it is correctly running
        # and to initialize entry options
        try:
            components = await asyncio.wait_for(
                backend.read_edge_components(self._config_data[CONF_EDGE]),
                timeout=10,
            )
            await backend.stop()
        except (jsonrpc_base.TransportError, jsonrpc_base.jsonrpc.ProtocolError):
            if local_backend:
                await local_backend.stop()
            raise

        entry_data = {"user_input": self._config_data, "components": components}

        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                entry=self._get_reconfigure_entry(), data=entry_data
            )

        # no reconfigure. Create new entry instead
        # initialize options with default settings
        options: dict[str:bool] = {}
        for component in components:
            options[component] = CONFIG.is_component_enabled(component)

        return self.async_create_entry(
            title=self._config_data[CONF_HOST], data=entry_data, options=options
        )

    async def async_step_edges(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle edges selection by user."""
        errors: dict[str, str] = {}
        edges_schema = self.cur_step["data_schema"]
        if user_input is not None:
            self._config_data[CONF_EDGE] = user_input[CONF_EDGES]
            try:
                return await self._create_or_update_entry()
            except (jsonrpc_base.TransportError, jsonrpc_base.jsonrpc.ProtocolError):
                _LOGGER.exception("Error during processing the selected edge")
                errors[CONF_EDGES] = "Error during processing the selected edge"

        # show errors in form
        return self.async_show_form(
            step_id="edges", data_schema=edges_schema, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Perform a reconfiguration."""
        return self.async_show_form(
            step_id="user",
            data_schema=step_user_data_schema(
                self._get_reconfigure_entry().data["user_input"]
            ),
            errors=None,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: OpenEMSConfigEntry,
    ) -> OpenEMSOptionsFlow:
        """Options callback for Reolink."""
        return OpenEMSOptionsFlow()


class OpenEMSOptionsFlow(OptionsFlow):
    """Handle OpenEMS options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the OpenEMS options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        backend: OpenEMSBackend = self.config_entry.runtime_data.backend
        schema: vol.Schema = vol.Schema({})
        for comp_name, comp in backend.the_edge.components.items():
            bool_selector = BooleanSelector(BooleanSelectorConfig())
            schema_entry = vol.Required(comp_name, default=comp.create_entities)
            schema = schema.extend({schema_entry: bool_selector})

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
