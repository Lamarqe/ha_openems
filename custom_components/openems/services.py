"""Services for OpenEMS devices."""

from collections import defaultdict
from typing import Any

import voluptuous as vol

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, service

from .const import ATTR_PROPERTIES, ATTR_TIMEOUT, ATTR_UPDATE_CYCLE, ATTR_VALUE, DOMAIN
from .helpers_ha import component_from_device
from .openems import OpenEMSComponent, OpenEMSProperty

SERVICE_UPDATE_COMPONENT_CONFIG = "update_component_config"

UPDATE_COMPONENT_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_PROPERTIES): vol.Schema({cv.string: object}),
    }
)


def _to_backend_value(prop: OpenEMSProperty, value: Any) -> Any:
    """Convert the value for the given property, raise ServiceValidationError if invalid."""
    try:
        return prop.to_backend_value(value)
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_property_value",
            translation_placeholders={
                "component": prop.component.name,
                "property": prop.config_name,
                "value": str(value),
            },
        ) from err


async def async_update_component_config(call: ServiceCall) -> None:
    """Send property updates with a single updateComponentConfig request per component."""
    updates: dict[OpenEMSComponent, dict[OpenEMSProperty, Any]] = defaultdict(dict)

    # validate and convert everything first, so that nothing is sent on errors
    for device_id in call.data[ATTR_DEVICE_ID]:
        component = component_from_device(call.hass, device_id)
        for name, value in call.data[ATTR_PROPERTIES].items():
            if (prop := component.get_property(name)) is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_property",
                    translation_placeholders={
                        "component": component.name,
                        "property": name,
                        "valid_properties": ", ".join(
                            p.config_name for p in component.properties
                        ),
                    },
                )
            updates[component][prop] = _to_backend_value(prop, value)

    for component, values in updates.items():
        await component.update_properties(values)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the OpenEMS services."""

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_COMPONENT_CONFIG,
        async_update_component_config,
        schema=UPDATE_COMPONENT_CONFIG_SCHEMA,
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
