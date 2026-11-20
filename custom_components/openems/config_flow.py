"""Config flow for the HA OpenEMS integration."""

import logging
from typing import Any

from openems_client.const import (
    CONF_ADVANCED_OPTIONS,
    CONF_FORWARD_INTERVAL,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
    CONN_TYPE_CUSTOM_URL,
    CONN_TYPE_DIRECT_EDGE,
    CONN_TYPE_LOCAL_FEMS,
    CONN_TYPE_LOCAL_OPENEMS,
    CONN_TYPE_WEB_FENECON,
)
from openems_client.entry_data import OpenEMSConfigReader, OpenEMSWebSocketConnection
from openems_client.exceptions import OpenEMSAuthError, OpenEMSError
import probatio
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
from .helpers_ha import OpenEMSConfigEntry, component_store, map_user_input

_LOGGER = logging.getLogger(__name__)


class OpenEMSConfigFlow(ConfigFlow, domain=c.DOMAIN):
    """Handle a config flow for HA OpenEMS."""

    VERSION = 6
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._config_data: dict[str, Any] = {}

    def _step_user_data_schema(self, user_input=None) -> probatio.Schema:
        """Define the config flow input options."""
        if not user_input:
            user_input = {}
        collapased = user_input.get(CONF_TYPE) == CONN_TYPE_DIRECT_EDGE
        schema: probatio.Schema = probatio.Schema(
            {
                probatio.Optional(
                    CONF_HOST, default=user_input.get(CONF_HOST, "")
                ): str,
                probatio.Required(
                    CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")
                ): str,
                probatio.Required(
                    CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")
                ): str,
                probatio.Required(c.CONF_MORE_OPTIONS): section(
                    probatio.Schema(
                        {
                            probatio.Required(
                                CONF_TYPE,
                                default=user_input.get(
                                    CONF_TYPE, CONN_TYPE_DIRECT_EDGE
                                ),
                            ): SelectSelector(
                                SelectSelectorConfig(
                                    options=[
                                        CONN_TYPE_DIRECT_EDGE,
                                        CONN_TYPE_LOCAL_FEMS,
                                        CONN_TYPE_LOCAL_OPENEMS,
                                        CONN_TYPE_WEB_FENECON,
                                        CONN_TYPE_CUSTOM_URL,
                                    ],
                                    translation_key="connection_type",
                                    multiple=False,
                                    mode=SelectSelectorMode.LIST,
                                )
                            ),
                            probatio.Optional(
                                CONF_URL, default=user_input.get(CONF_URL, "")
                            ): str,
                        }
                    ),
                    SectionConfig(collapsed=collapased),
                ),
            }
        )
        return schema

    def _step_edges_data_schema(
        self, edge_response: dict, default_edge
    ) -> probatio.Schema:
        """Define the edges step input options."""
        return probatio.Schema(
            {
                probatio.Required(
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
            in (
                CONN_TYPE_DIRECT_EDGE,
                CONN_TYPE_LOCAL_FEMS,
                CONN_TYPE_LOCAL_OPENEMS,
            )
            and not user_input.get(CONF_HOST, "").strip()
        ):
            errors[CONF_HOST] = "host_missing"
            return self._show_form(user_input, errors)

        if user_input[CONF_TYPE] == CONN_TYPE_CUSTOM_URL:
            if not user_input.get(CONF_URL).strip():
                errors[c.CONF_MORE_OPTIONS] = "custom_url_missing"
                return self._show_form(user_input, errors)

            try:
                conn_url = URL(user_input[CONF_URL])
            except ValueError:
                errors[c.CONF_MORE_OPTIONS] = "invalid_url"
                return self._show_form(user_input, errors)

            if not conn_url.absolute:
                errors[c.CONF_MORE_OPTIONS] = "url_not_absolute"
                return self._show_form(user_input, errors)

        # avoid storing inactive config options in the config entry
        if user_input[CONF_TYPE] != CONN_TYPE_CUSTOM_URL and user_input.get(CONF_URL):
            del user_input[CONF_URL]
        if user_input.get(CONF_TYPE) in (
            CONN_TYPE_WEB_FENECON,
            CONN_TYPE_CUSTOM_URL,
        ) and user_input.get(CONF_HOST):
            del user_input[CONF_HOST]

        try:
            connection = OpenEMSWebSocketConnection(map_user_input(user_input))
        except ValueError:
            errors[CONF_HOST] = "invalid_host"
            return self._show_form(user_input, errors)

        try:
            self._config_data = user_input
            try:
                # connect
                await connection.connect_to_server()
            except OpenEMSError:
                errors[CONF_BASE] = "cannot_connect"
                return self._show_form(user_input, errors)

            try:
                # login
                login_response = await connection.login_to_server()
            except OpenEMSAuthError:
                errors[CONF_BASE] = "invalid_auth"
                return self._show_form(user_input, errors)
            except OpenEMSError:
                errors[CONF_BASE] = "cannot_connect"
                return self._show_form(user_input, errors)

            try:
                config_reader = OpenEMSConfigReader(connection)
                edges = await config_reader.read_edges()
                multi_edge = OpenEMSConfigReader.parse_login_response(login_response)
                if multi_edge:
                    if self.source == SOURCE_RECONFIGURE:
                        default_edge = self._get_reconfigure_entry().data[c.CONF_EDGE]
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

            except OpenEMSError, KeyError:
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
        # read the edge config to validate the edge is correctly running
        await config_reader.read_edge_config()

        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch()
            reconfigure_entry = self._get_reconfigure_entry()
            # the connection may point to another edge now, so read its configuration again
            await component_store(self.hass, reconfigure_entry.entry_id).async_remove()
            return self.async_update_reload_and_abort(
                entry=reconfigure_entry, data=self._config_data
            )

        # no reconfigure. Create new entry instead
        # initialize options with default settings
        options = {
            CONF_ADVANCED_OPTIONS: {
                CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
                CONF_FORWARD_INTERVAL: 0,
            },
        }

        # Create meaningful entry title, alter strategy based on selected options
        title: str = ""
        if self._config_data[CONF_TYPE] in (
            CONN_TYPE_DIRECT_EDGE,
            CONN_TYPE_LOCAL_FEMS,
            CONN_TYPE_LOCAL_OPENEMS,
        ):
            title = self._config_data[CONF_HOST]
        elif self._config_data[CONF_TYPE] == CONN_TYPE_WEB_FENECON:
            title = "FEMS Web: " + self._config_data[CONF_USERNAME]
        elif config_reader.connection.conn_url.host is not None:
            title = config_reader.connection.conn_url.host
        if multi_edge:
            title += " " + self._config_data[c.CONF_EDGE]

        return self.async_create_entry(
            title=title, data=self._config_data, options=options
        )

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
            except OpenEMSError:
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
                dict(self._get_reconfigure_entry().data)
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
        return await self.async_step_advanced_options(user_input)

    async def async_step_advanced_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage advanced options."""
        if user_input is not None:
            options = {**self.config_entry.options, CONF_ADVANCED_OPTIONS: user_input}
            return self.async_create_entry(data=options)

        advanced = self.config_entry.options[CONF_ADVANCED_OPTIONS]
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
                    default=advanced[CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING],
                ): BooleanSelector(BooleanSelectorConfig()),
                probatio.Required(
                    CONF_FORWARD_INTERVAL,
                    default=advanced[CONF_FORWARD_INTERVAL],
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
