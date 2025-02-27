"""Config flow for the HA OpenEMS integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import jsonrpc_base
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .__init__ import DOMAIN
from .const import STORAGE_KEY, STORAGE_VERSION
from .openems import OpenEMSBackend

_LOGGER = logging.getLogger(__name__)


def step_user_data_schema(user_input=None):
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


class OpenEMSConfigFlowHandler(ConfigFlow, domain=DOMAIN):
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
            backend = OpenEMSBackend(self.hass, user_input)
            try:
                # login
                backend.start()
                await asyncio.wait_for(backend.wait_for_login(), timeout=2)
                # read config
                config_data = await asyncio.wait_for(backend.read_config(), timeout=5)
                # store config in HA
                store: Store = Store(self.hass, STORAGE_VERSION, STORAGE_KEY)
                await store.async_save(config_data)
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


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
