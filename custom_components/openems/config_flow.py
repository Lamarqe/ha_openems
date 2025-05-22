"""Config flow for the HA OpenEMS integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import jsonrpc_base
import voluptuous as vol
from yarl import URL

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_BASE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_TYPE,
    CONF_URL,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    BooleanSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .__init__ import OpenEMSConfigEntry
from .const import (
    CONF_EDGE,
    CONF_EDGES,
    CONN_TYPE_CUSTOM_URL,
    CONN_TYPE_DIRECT_EDGE,
    CONN_TYPE_LOCAL_FEMS,
    CONN_TYPE_LOCAL_OPENEMS,
    CONN_TYPE_WEB_FENECON,
    DOMAIN,
    connection_url,
)
from .openems import CONFIG, OpenEMSBackend

_LOGGER = logging.getLogger(__name__)


class OpenEMSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA OpenEMS."""

    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._config_data: dict[str, Any] = {}

    def _step_user_data_schema(self, user_input=None) -> vol.Schema:
        """Define the config flow input options."""
        if not user_input:
            user_input = {}
        schema: vol.Schema = vol.Schema(
            {vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): str}
        )
        if self.show_advanced_options:
            schema = schema.extend(
                {vol.Optional(CONF_HOST, default=user_input.get(CONF_HOST, "")): str}
            )
        else:
            schema = schema.extend(
                {vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): str}
            )
        schema = schema.extend(
            {
                vol.Required(
                    CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")
                ): str,
                vol.Required(
                    CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")
                ): str,
            },
        )
        if self.show_advanced_options:
            schema = schema.extend(
                {
                    vol.Required(
                        CONF_TYPE,
                        default=user_input.get(CONF_TYPE, CONN_TYPE_DIRECT_EDGE),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                CONN_TYPE_LOCAL_FEMS,
                                CONN_TYPE_LOCAL_OPENEMS,
                                CONN_TYPE_DIRECT_EDGE,
                                CONN_TYPE_WEB_FENECON,
                                CONN_TYPE_CUSTOM_URL,
                            ],
                            multiple=False,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(CONF_URL, default=user_input.get(CONF_URL, "")): str,
                }
            )
        return schema

    def _step_edges_data_schema(self, edge_response: dict, default_edge) -> vol.Schema:
        """Define the edges step input options."""
        return vol.Schema(
            {
                vol.Required(
                    CONF_EDGES,
                    default=default_edge,
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

    def _validate_user_input_complete(self, user_input) -> bool:
        return user_input.get(CONF_TYPE) == CONN_TYPE_CUSTOM_URL and not user_input.get(
            CONF_URL
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        # preset connection type for non-advanced users
        if not self.show_advanced_options:
            user_input[CONF_TYPE] = CONN_TYPE_DIRECT_EDGE

        if not user_input:
            return self._show_form(user_input, errors)

        if (
            user_input[CONF_TYPE]
            in [
                CONN_TYPE_DIRECT_EDGE,
                CONN_TYPE_LOCAL_FEMS,
                CONN_TYPE_LOCAL_OPENEMS,
            ]
            and not user_input.get(CONF_HOST, "").strip()
        ):
            errors[CONF_HOST] = "Please provide host for local connection."
            return self._show_form(user_input, errors)

        if user_input[CONF_TYPE] == CONN_TYPE_CUSTOM_URL:
            if not user_input.get(CONF_URL):
                errors[CONF_URL] = "Custom URL must not be empty."
                return self._show_form(user_input, errors)

            try:
                conn_url = URL(user_input[CONF_URL])
            except ValueError as e:
                errors[CONF_URL] = f"Invalid URL provided: {e!s}."
                return self._show_form(user_input, errors)

            if not conn_url.absolute:
                errors[CONF_URL] = "Custom URL must be absolute."
                return self._show_form(user_input, errors)

        else:
            try:
                conn_url = connection_url(user_input[CONF_TYPE], user_input[CONF_HOST])
            except ValueError as e:
                errors[CONF_HOST] = str(e)
                return self._show_form(user_input, errors)

        backend = OpenEMSBackend(
            conn_url,
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
        )
        self._config_data = user_input
        try:
            # connect
            await asyncio.wait_for(backend.connect_to_server(), timeout=2)
        except jsonrpc_base.TransportError as te:
            errors[CONF_HOST] = f"{te.args[0]}: {te.args[1]}"
            return self._show_form(user_input, errors)

        try:
            # login
            await asyncio.wait_for(backend.login_to_server(), timeout=2)
        except jsonrpc_base.jsonrpc.ProtocolError as pe:
            errors[CONF_PASSWORD] = f"{pe.args[0]}: {pe.args[1]}"
            await backend.stop()
            return self._show_form(user_input, errors)

        try:
            edges = await asyncio.wait_for(backend.read_edges(), timeout=2)
            if backend.multi_edge:
                if self.source == SOURCE_RECONFIGURE:
                    default_edge = self._get_reconfigure_entry().data["user_input"][
                        CONF_EDGE
                    ]
                else:
                    default_edge = None

                edges_schema = self._step_edges_data_schema(edges, default_edge)
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
            return self._show_form(user_input, errors)

    def _show_form(self, user_input, errors):
        # show errors in form
        data_schema = self._step_user_data_schema(user_input)
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def _create_or_update_entry(
        self, backend: OpenEMSBackend | None = None
    ) -> ConfigFlowResult:
        # backend will be closed in case of success.
        # in case of exceptions, caller must stop the backend.
        if not backend:
            if self._config_data[CONF_TYPE] == CONN_TYPE_CUSTOM_URL:
                conn_url = URL(self._config_data[CONF_URL])
            else:
                conn_url = connection_url(
                    self._config_data[CONF_TYPE], self._config_data[CONF_HOST]
                )

            local_backend: OpenEMSBackend = OpenEMSBackend(
                conn_url,
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

        # Create meaningful entry title, alter strategy based on selected options
        if self._config_data[CONF_TYPE] in [
            CONN_TYPE_DIRECT_EDGE,
            CONN_TYPE_LOCAL_FEMS,
            CONN_TYPE_LOCAL_OPENEMS,
        ]:
            title = self._config_data[CONF_HOST]
        elif self._config_data[CONF_TYPE] == CONN_TYPE_WEB_FENECON:
            title = "FEMS Web: " + self._config_data[CONF_USERNAME]
        else:
            title = backend.ws_url.host
        if backend.multi_edge:
            title += " " + self._config_data[CONF_EDGE]

        return self.async_create_entry(title=title, data=entry_data, options=options)

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
            data_schema=self._step_user_data_schema(
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
