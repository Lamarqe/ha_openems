"""Device actions for OpenEMS components."""

from typing import Any

from openems_client.openems import OpenEMSProperty
import probatio

from homeassistant.const import ATTR_DEVICE_ID, CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import DOMAIN, OpenEMSService, OpenEMSServiceArgument
from .helpers_ha import component_from_device, property_selector, write_only_channels

ACTION_TYPE_UPDATE_COMPONENT_CONFIG = "update_component_config"
ACTION_TYPE_WRITE_CHANNEL = "write_channel"

WRITE_CHANNEL_FIELDS = (
    OpenEMSServiceArgument.CHANNEL,
    OpenEMSServiceArgument.VALUE,
    OpenEMSServiceArgument.UPDATE_CYCLE,
    OpenEMSServiceArgument.TIMEOUT,
)

_SECONDS_SELECTOR = selector.selector(
    {"number": {"min": 0, "mode": "box", "unit_of_measurement": "s"}}
)

# The property fields are stored flat in the action config, next to these keys.
RESERVED_KEYS = {
    *(str(key) for key in cv.DEVICE_ACTION_BASE_SCHEMA.schema),
    CONF_TYPE,
}

ACTION_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        probatio.Required(CONF_TYPE): probatio.In(
            [ACTION_TYPE_UPDATE_COMPONENT_CONFIG, ACTION_TYPE_WRITE_CHANNEL]
        )
    },
    extra=probatio.ALLOW_EXTRA,
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
    action_types: list[str] = []
    if component.properties:
        action_types.append(ACTION_TYPE_UPDATE_COMPONENT_CONFIG)
    if component.edge.rest_writable and write_only_channels(component):
        action_types.append(ACTION_TYPE_WRITE_CHANNEL)
    return [
        {CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id, CONF_TYPE: action_type}
        for action_type in action_types
    ]


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, probatio.Schema]:
    """List the fields of the action.

    Updating the configuration has one optional field per property of the component,
    writing a channel offers the write-only channels of the component.
    """
    try:
        component = component_from_device(hass, config[CONF_DEVICE_ID])
    except ServiceValidationError:
        return {}
    if config[CONF_TYPE] == ACTION_TYPE_WRITE_CHANNEL:
        return {
            "extra_fields": probatio.Schema(
                {
                    probatio.Required(
                        OpenEMSServiceArgument.CHANNEL
                    ): selector.selector(
                        {
                            "select": {
                                "options": sorted(write_only_channels(component)),
                                "mode": "dropdown",
                            }
                        }
                    ),
                    probatio.Required(OpenEMSServiceArgument.VALUE): selector.selector(
                        {"text": {}}
                    ),
                    probatio.Optional(
                        OpenEMSServiceArgument.UPDATE_CYCLE, default=0
                    ): _SECONDS_SELECTOR,
                    probatio.Optional(
                        OpenEMSServiceArgument.TIMEOUT, default=0
                    ): _SECONDS_SELECTOR,
                }
            )
        }
    return {
        "extra_fields": probatio.Schema(
            {
                probatio.Optional(field_name(prop)): selector.selector(
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
    """Call the action of the device action with the filled in fields."""
    if config[CONF_TYPE] == ACTION_TYPE_WRITE_CHANNEL:
        await hass.services.async_call(
            DOMAIN,
            OpenEMSService.WRITE_CHANNEL,
            {
                ATTR_DEVICE_ID: config[CONF_DEVICE_ID],
                **{key: config[key] for key in WRITE_CHANNEL_FIELDS if key in config},
            },
            blocking=True,
            context=context,
        )
        return
    # all non-reserved keys are passed on, so that the service rejects stale fields
    properties = {
        key: value for key, value in config.items() if key not in RESERVED_KEYS
    }
    await hass.services.async_call(
        DOMAIN,
        OpenEMSService.UPDATE_COMPONENT_CONFIG,
        {
            ATTR_DEVICE_ID: config[CONF_DEVICE_ID],
            OpenEMSServiceArgument.PROPERTIES: properties,
        },
        blocking=True,
        context=context,
    )
