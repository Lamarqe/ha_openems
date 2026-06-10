"""Config flow for the HA OpenEMS integration."""

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
from homeassistant.data_entry_flow import SectionConfig, section
from homeassistant.helpers.selector import (
    BooleanSelector,
    BooleanSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import const as c
from .entry_data import OpenEMSConfigReader, OpenEMSWebSocketConnection
from .helpers_ha import OpenEMSConfigEntry, map_user_input
from .openems import CONFIG, OpenEMSBackend

_LOGGER = logging.getLogger(__name__)


class OpenEMSConfigFlow(ConfigFlow, domain=c.DOMAIN):
    """Handle a config flow for HA OpenEMS."""

    VERSION = 4
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._config_data: dict[str, Any] = {}

    def _step_user_data_schema(self, user_input=None) -> vol.Schema:
        """Define the config flow input options."""
        if not user_input:
            user_input = {}
        collapased = user_input.get(CONF_TYPE) == c.CONN_TYPE_DIRECT_EDGE
        schema: vol.Schema = vol.Schema(
            {
                vol.Optional(CONF_HOST, default=user_input.get(CONF_HOST, "")): str,
                vol.Required(
                    CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")
                ): str,
                vol.Required(
                    CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")
                ): str,
                vol.Required(c.CONF_MORE_OPTIONS): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_TYPE,
                                default=user_input.get(
                                    CONF_TYPE, c.CONN_TYPE_DIRECT_EDGE
                                ),
                            ): SelectSelector(
                                SelectSelectorConfig(
                                    options=[
                                        c.CONN_TYPE_DIRECT_EDGE,
                                        c.CONN_TYPE_LOCAL_FEMS,
                                        c.CONN_TYPE_LOCAL_OPENEMS,
                                        c.CONN_TYPE_WEB_FENECON,
                                        c.CONN_TYPE_CUSTOM_URL,
                                    ],
                                    translation_key="connection_type",
                                    multiple=False,
                                    mode=SelectSelectorMode.LIST,
                                )
                            ),
                            vol.Optional(
                                CONF_URL, default=user_input.get(CONF_URL, "")
                            ): str,
                        }
                    ),
                    SectionConfig(collapsed=collapased),
                ),
            }
        )
        return schema

    def _step_edges_data_schema(self, edge_response: dict, default_edge) -> vol.Schema:
        """Define the edges step input options."""
        return vol.Schema(
            {
                vol.Required(
                    c.CONF_EDGES,
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
        return user_input.get(
            CONF_TYPE
        ) == c.CONN_TYPE_CUSTOM_URL and not user_input.get(CONF_URL)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is None:
            return self._show_form(user_input=None, errors=errors)
        more_options = user_input.pop(c.CONF_MORE_OPTIONS, {})
        user_input.update(more_options)

        if (
            user_input[CONF_TYPE]
            in [
                c.CONN_TYPE_DIRECT_EDGE,
                c.CONN_TYPE_LOCAL_FEMS,
                c.CONN_TYPE_LOCAL_OPENEMS,
            ]
            and not user_input.get(CONF_HOST, "").strip()
        ):
            errors[CONF_HOST] = "host_missing"
            return self._show_form(user_input, errors)

        if user_input[CONF_TYPE] == c.CONN_TYPE_CUSTOM_URL:
            if not user_input.get(CONF_URL):
                errors[CONF_URL] = "custom_url_missing"
                return self._show_form(user_input, errors)

            try:
                conn_url = URL(user_input[CONF_URL])
            except ValueError as e:
                errors[CONF_URL] = "invalid_url"
                errors[CONF_BASE] = str(e)
                return self._show_form(user_input, errors)

            if not conn_url.absolute:
                errors[CONF_URL] = "url_not_absolute"
                return self._show_form(user_input, errors)

        try:
            connection = OpenEMSWebSocketConnection(map_user_input(user_input))
        except ValueError as e:
            errors[CONF_HOST] = str(e)
            return self._show_form(user_input, errors)

        try:
            self._config_data = user_input
            try:
                # connect
                await asyncio.wait_for(connection.connect_to_server(), timeout=2)
            except jsonrpc_base.TransportError as te:
                errors[CONF_HOST] = f"{te.args[0]}: {te.args[1]}"
                return self._show_form(user_input, errors)

            try:
                # login
                login_response = await asyncio.wait_for(
                    connection.login_to_server(), timeout=2
                )
            except jsonrpc_base.jsonrpc.ProtocolError as pe:
                errors[CONF_PASSWORD] = f"{pe.args[0]}: {pe.args[1]}"
                return self._show_form(user_input, errors)

            try:
                config_reader = OpenEMSConfigReader(connection)
                edges = await asyncio.wait_for(config_reader.read_edges(), timeout=2)
                multi_edge = OpenEMSConfigReader.parse_login_response(login_response)
                if multi_edge:
                    if self.source == SOURCE_RECONFIGURE:
                        default_edge = self._get_reconfigure_entry().data["user_input"][
                            c.CONF_EDGE
                        ]
                    else:
                        default_edge = None

                    edges_schema = self._step_edges_data_schema(edges, default_edge)
                    return self.async_show_form(
                        step_id="edges",
                        data_schema=edges_schema,
                    )

                # single edge backend, all required data has been received.
                edge_id = next(iter(edges["edges"]))["id"]
                self._config_data[c.CONF_EDGE] = edge_id
                config_reader.set_edge_id(edge_id)
                return await self._create_or_update_entry(config_reader, False)

            except TimeoutError, KeyError, jsonrpc_base.jsonrpc.ProtocolError:
                _LOGGER.exception("Cannot read edge components")
                errors[CONF_BASE] = "cannot_read_components"
                return self._show_form(user_input, errors)

        finally:
            await connection.stop()

    def _show_form(self, user_input, errors):
        # show errors in form
        data_schema = self._step_user_data_schema(user_input)
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def _create_or_update_entry(
        self, config_reader: OpenEMSConfigReader, multi_edge: bool
    ) -> ConfigFlowResult:
        # read edge components to validate it is correctly running
        # and to initialize entry options
        components = await asyncio.wait_for(
            config_reader.read_edge_components(), timeout=20
        )
        entry_data = {"user_input": self._config_data, "components": components}

        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                entry=self._get_reconfigure_entry(), data=entry_data
            )

        # no reconfigure. Create new entry instead
        # initialize options with default settings
        components_options: dict[str, bool] = {}
        for component in components:
            components_options[component] = CONFIG.is_component_enabled(component)
        options: c.ConfigOptions = {
            c.CONF_COMPONENTS: components_options,
            c.CONF_ADVANCED_OPTIONS: {
                c.CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
                c.CONF_FORWARD_INTERVAL: 0,
            },
        }

        # Create meaningful entry title, alter strategy based on selected options
        title: str = ""
        if self._config_data[CONF_TYPE] in [
            c.CONN_TYPE_DIRECT_EDGE,
            c.CONN_TYPE_LOCAL_FEMS,
            c.CONN_TYPE_LOCAL_OPENEMS,
        ]:
            title = self._config_data[CONF_HOST]
        elif self._config_data[CONF_TYPE] == c.CONN_TYPE_WEB_FENECON:
            title = "FEMS Web: " + self._config_data[CONF_USERNAME]
        elif config_reader.connection.conn_url.host is not None:
            title = config_reader.connection.conn_url.host
        if multi_edge:
            title += " " + self._config_data[c.CONF_EDGE]

        return self.async_create_entry(title=title, data=entry_data, options=options)

    async def async_step_edges(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle edges selection by user."""
        errors: dict[str, str] = {}
        if not self.cur_step:
            return self.async_abort(reason="No current step found in edges step")

        edges_schema = self.cur_step.get("data_schema")
        if user_input is not None:
            self._config_data[c.CONF_EDGE] = user_input[c.CONF_EDGES]
            connection: OpenEMSWebSocketConnection = OpenEMSWebSocketConnection(
                map_user_input(self._config_data)
            )
            try:
                await connection.connect_to_server()
                await connection.login_to_server()
                config_reader: OpenEMSConfigReader = OpenEMSConfigReader(
                    connection, user_input[c.CONF_EDGES]
                )
                return await self._create_or_update_entry(config_reader, True)
            except (
                jsonrpc_base.TransportError,
                jsonrpc_base.jsonrpc.ProtocolError,
                TimeoutError,
            ):
                _LOGGER.exception("Error during processing the selected edge")
                errors[c.CONF_EDGES] = "error_processing_edge"
            finally:
                await connection.stop()

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
        """Options callback for OpenEMS."""
        return OpenEMSOptionsFlow()


class OpenEMSOptionsFlow(OptionsFlow):
    """Handle OpenEMS options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the OpenEMS options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["select_components", "advanced_options"],
        )

    async def async_step_select_components(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage component selection."""
        if user_input is not None:
            options = {**self.config_entry.options, c.CONF_COMPONENTS: user_input}
            return self.async_create_entry(data=options)

        backend: OpenEMSBackend = self.config_entry.runtime_data.backend
        schema: vol.Schema = vol.Schema({})
        for comp_name, comp in backend.the_edge.components.items():
            bool_selector = BooleanSelector(BooleanSelectorConfig())
            schema_entry = vol.Required(comp_name, default=comp.create_entities)
            schema = schema.extend({schema_entry: bool_selector})

        return self.async_show_form(
            step_id="select_components",
            data_schema=schema,
        )

    async def async_step_advanced_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage advanced options."""
        if user_input is not None:
            options = {**self.config_entry.options, c.CONF_ADVANCED_OPTIONS: user_input}
            return self.async_create_entry(data=options)

        advanced = self.config_entry.options[c.CONF_ADVANCED_OPTIONS]
        schema = vol.Schema(
            {
                vol.Required(
                    c.CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
                    default=advanced[c.CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING],
                ): BooleanSelector(BooleanSelectorConfig()),
                vol.Required(
                    c.CONF_FORWARD_INTERVAL,
                    default=advanced[c.CONF_FORWARD_INTERVAL],
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=3600, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="advanced_options",
            data_schema=schema,
        )
