"""Config flow for the HA OpenEMS integration."""

from collections.abc import Mapping
import logging
from typing import Any

from openems_client.client import EdgeInfo, OpenEMSClient
from openems_client.const import (
    CONN_TYPE_CUSTOM_URL,
    CONN_TYPE_DIRECT_EDGE,
    CONN_TYPE_LOCAL_FEMS,
    CONN_TYPE_LOCAL_OPENEMS,
    CONN_TYPE_WEB_FENECON,
)
from openems_client.exceptions import OpenEMSAuthError, OpenEMSError
import probatio
from yarl import URL

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    BooleanSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import const as c
from .helpers_ha import OpenEMSConfigEntry, component_store, map_user_input

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class OpenEMSConfigFlow(ConfigFlow, domain=c.DOMAIN):
    """Handle a config flow for HA OpenEMS."""

    VERSION = 6
    MINOR_VERSION = 5

    def __init__(self) -> None:
        """Initialize."""
        self._config_data: dict[str, Any] = {}
        # online edges of a multi edge backend, offered in the edges step
        self._edges: list[EdgeInfo] = []

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
                # never sent back to the frontend, also not the stored one on reconfigure
                probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
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

    def _step_edges_data_schema(self) -> probatio.Schema:
        """Define the edges step input options."""
        default_edge = (
            self._get_reconfigure_entry().data[c.CONF_EDGE]
            if self.source == SOURCE_RECONFIGURE
            else None
        )
        return probatio.Schema(
            {
                probatio.Required(
                    c.CONF_EDGES,
                    default=default_edge,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[edge.id for edge in self._edges],
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
            if not user_input.get(CONF_URL, "").strip():
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
            client = OpenEMSClient(
                map_user_input(user_input), async_get_clientsession(self.hass)
            )
        except ValueError:
            errors[CONF_HOST] = "invalid_host"
            return self._show_form(user_input, errors)

        self._config_data = user_input
        async with client:
            try:
                login = await client.connect()
            except OpenEMSAuthError:
                errors[CONF_BASE] = "invalid_auth"
                return self._show_form(user_input, errors)
            except OpenEMSError:
                errors[CONF_BASE] = "cannot_connect"
                return self._show_form(user_input, errors)

            try:
                edges = await client.read_edges()
            except OpenEMSError:
                errors[CONF_BASE] = "cannot_read_components"
                return self._show_form(user_input, errors)
            except Exception:
                _LOGGER.exception("Unexpected error while reading the edges")
                errors[CONF_BASE] = "unknown"
                return self._show_form(user_input, errors)

            if login.multi_edge:
                self._edges = [edge for edge in edges if edge.online]
                if not self._edges:
                    errors[CONF_BASE] = "no_edges"
                    return self._show_form(user_input, errors)
                return self.async_show_form(
                    step_id="edges", data_schema=self._step_edges_data_schema()
                )

            # single edge backend, all required data has been received
            if not edges:
                errors[CONF_BASE] = "no_edges"
                return self._show_form(user_input, errors)
            edge_id = edges[0].id
            self._config_data[c.CONF_EDGE] = edge_id
            try:
                hostname = await client.read_edge_hostname(edge_id)
            except OpenEMSError:
                errors[CONF_BASE] = "cannot_read_components"
                return self._show_form(user_input, errors)
            except Exception:
                _LOGGER.exception("Unexpected error while reading the edge")
                errors[CONF_BASE] = "unknown"
                return self._show_form(user_input, errors)
            return await self._create_or_update_entry(
                hostname, client.host, multi_edge=False
            )

    def _show_form(self, user_input, errors):
        # show errors in form
        data_schema = self._step_user_data_schema(user_input)
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def _create_or_update_entry(
        self, hostname: str, host: str | None, multi_edge: bool
    ) -> ConfigFlowResult:
        # the hostname identifies the edge
        await self.async_set_unique_id(hostname)

        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch()
            reconfigure_entry = self._get_reconfigure_entry()
            # the connection may point to another edge now, so read its configuration again
            await component_store(self.hass, reconfigure_entry.entry_id).async_remove()
            return self.async_update_reload_and_abort(
                entry=reconfigure_entry, data=self._config_data
            )

        # no reconfigure. Create new entry instead
        self._abort_if_unique_id_configured()
        # initialize options with default settings
        options = {
            c.CONF_ADVANCED_OPTIONS: {
                c.CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False,
                c.CONF_FORWARD_INTERVAL: 0,
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
        elif host is not None:
            title = host
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
        if user_input is not None:
            edge_id = user_input[c.CONF_EDGES]
            self._config_data[c.CONF_EDGE] = edge_id
            async with OpenEMSClient(
                map_user_input(self._config_data), async_get_clientsession(self.hass)
            ) as client:
                try:
                    await client.connect()
                    hostname = await client.read_edge_hostname(edge_id)
                except OpenEMSError:
                    errors[c.CONF_EDGES] = "error_processing_edge"
                except Exception:
                    _LOGGER.exception("Unexpected error while reading the edge")
                    errors[CONF_BASE] = "unknown"
                else:
                    return await self._create_or_update_entry(
                        hostname, client.host, multi_edge=True
                    )

        return self.async_show_form(
            step_id="edges", data_schema=self._step_edges_data_schema(), errors=errors
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

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a login rejected during setup."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials and validate them with a login."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            async with OpenEMSClient(
                map_user_input({**reauth_entry.data, **user_input}),
                async_get_clientsession(self.hass),
            ) as client:
                try:
                    await client.connect()
                    hostname = await client.read_edge_hostname(
                        reauth_entry.data[c.CONF_EDGE]
                    )
                except OpenEMSAuthError:
                    errors[CONF_BASE] = "invalid_auth"
                except OpenEMSError:
                    errors[CONF_BASE] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during reauthentication")
                    errors[CONF_BASE] = "unknown"
                else:
                    # the credentials must belong to the system of the entry
                    await self.async_set_unique_id(hostname)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        reauth_entry, data_updates=user_input
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_USERNAME, default=reauth_entry.data[CONF_USERNAME]
                    ): str,
                    probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            description_placeholders={"name": reauth_entry.title},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: OpenEMSConfigEntry,
    ) -> OpenEMSOptionsFlow:
        """Options callback for OpenEMS."""
        return OpenEMSOptionsFlow()


class OpenEMSOptionsFlow(OptionsFlowWithReload):
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
            options = {**self.config_entry.options, c.CONF_ADVANCED_OPTIONS: user_input}
            return self.async_create_entry(data=options)

        advanced = self.config_entry.options[c.CONF_ADVANCED_OPTIONS]
        schema = probatio.Schema(
            {
                probatio.Required(
                    c.CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
                    default=advanced[c.CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING],
                ): BooleanSelector(BooleanSelectorConfig()),
                probatio.Required(
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
