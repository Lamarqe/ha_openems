"""Config flow for the HA OpenEMS integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import jsonrpc_base
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import BooleanSelector, BooleanSelectorConfig
from homeassistant.helpers.storage import Store

from .__init__ import OpenEMSConfigEntry
from .const import (
    DEFAULT_EDGE_CHANNELS,
    DOMAIN,
    STORAGE_KEY_BACKEND_CONFIG,
    STORAGE_KEY_HA_OPTIONS,
    STORAGE_VERSION,
)
from .openems import OpenEMSBackend

_LOGGER = logging.getLogger(__name__)


def step_user_data_schema(user_input=None):
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


STEP_USER_DATA_SCHEMA = step_user_data_schema()


class OpenEMSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA OpenEMS."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._host: str | None = None
        self._username: str = "x"
        self._password: str | None = None

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
            try:
                # login
                backend.start()
                await asyncio.wait_for(backend.wait_for_login(), timeout=2)
                # read config
                config_data = await asyncio.wait_for(backend.read_config(), timeout=5)
                # store config in HA
                store: Store = Store(
                    self.hass, STORAGE_VERSION, STORAGE_KEY_BACKEND_CONFIG
                )
                await store.async_save(config_data)
                # initialize options
                options: dict[str:bool] = {}
                for channel_name in DEFAULT_EDGE_CHANNELS:
                    comp_name = channel_name.split("/")[0]
                    options[comp_name] = True
                options_key = STORAGE_KEY_HA_OPTIONS + "_" + backend.host
                store_options: Store = Store(self.hass, STORAGE_VERSION, options_key)
                await store_options.async_save(options)

                # stop
                await backend.stop()
            except jsonrpc_base.TransportError:
                errors[CONF_HOST] = "Cannot connect to the specified host."
            except jsonrpc_base.jsonrpc.ProtocolError:
                errors[CONF_PASSWORD] = "Wrong username / password."
            else:
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )

        # show errors in form
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
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
        for comp_name, comp in next(iter(backend.edges.values())).components.items():
            bool_selector = BooleanSelector(BooleanSelectorConfig())
            schema_entry = vol.Required(comp_name, default=comp.create_entities)
            schema = schema.extend({schema_entry: bool_selector})

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
