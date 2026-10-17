"""Device actions for OpenEMS components."""

from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_DEVICE_ID, CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import ATTR_PROPERTIES, DOMAIN
from .helpers_ha import component_from_device, property_selector
from .openems import OpenEMSProperty
from .services import SERVICE_UPDATE_COMPONENT_CONFIG

ACTION_TYPE_UPDATE_COMPONENT_CONFIG = "update_component_config"

# The property fields are stored flat in the action config, next to these keys.
RESERVED_KEYS = {
    *(str(key) for key in cv.DEVICE_ACTION_BASE_SCHEMA.schema),
    CONF_TYPE,
}

ACTION_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): ACTION_TYPE_UPDATE_COMPONENT_CONFIG},
    extra=vol.ALLOW_EXTRA,
)


def field_name(prop: OpenEMSProperty) -> str:
    """Return the field name of a property in the action config."""
    # Fall back to the channel name, eg for a property "enabled", which would
    # otherwise collide with the "enabled" key of every script action.
    if prop.config_name in RESERVED_KEYS:
        return prop.name
    return prop.config_name


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device actions for OpenEMS component devices."""
    try:
        component = component_from_device(hass, device_id)
    except ServiceValidationError:
        # eg the main device of an edge
        return []
    if not component.properties:
        return []
    return [
        {
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: ACTION_TYPE_UPDATE_COMPONENT_CONFIG,
        }
    ]


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """List one optional field per property of the component."""
    try:
        component = component_from_device(hass, config[CONF_DEVICE_ID])
    except ServiceValidationError:
        return {}
    return {
        "extra_fields": vol.Schema(
            {
                vol.Optional(field_name(prop)): selector.selector(
                    property_selector(prop)
                )
                for prop in sorted(component.properties, key=field_name)
            }
        )
    }


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Context | None,
) -> None:
    """Send all filled in fields with the update_component_config service."""
    # all non-reserved keys are passed on, so that the service rejects stale fields
    properties = {
        key: value for key, value in config.items() if key not in RESERVED_KEYS
    }
    await hass.services.async_call(
        DOMAIN,
        SERVICE_UPDATE_COMPONENT_CONFIG,
        {ATTR_DEVICE_ID: config[CONF_DEVICE_ID], ATTR_PROPERTIES: properties},
        blocking=True,
        context=context,
    )
