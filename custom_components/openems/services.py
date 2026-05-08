"""Services for OpenEMS devices."""

import voluptuous as vol

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import service

from .const import ATTR_TIMEOUT, ATTR_UPDATE_CYCLE, ATTR_VALUE, DOMAIN


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the OpenEMS services."""

    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "update_component_config",
        entity_domain=NUMBER_DOMAIN,
        schema={vol.Required(ATTR_VALUE): vol.Coerce(float)},
        func="update_component_config",
    )

    rest_schema = {
        vol.Required(ATTR_VALUE): vol.Coerce(float),
        vol.Optional(ATTR_UPDATE_CYCLE, default=30): vol.Coerce(int),
        vol.Optional(ATTR_TIMEOUT, default=0): vol.Coerce(int),
    }
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "rest_write_sensor",
        entity_domain=SENSOR_DOMAIN,
        schema=rest_schema,
        func="update_value",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "rest_write_binary_sensor",
        entity_domain=BINARY_SENSOR_DOMAIN,
        schema=rest_schema,
        func="update_value",
    )
