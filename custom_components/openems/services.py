"""Services for OpenEMS devices."""

from collections import defaultdict
from typing import Any

from openems_client.exceptions import OpenEMSError
from openems_client.openems import OpenEMSComponent, OpenEMSProperty
import probatio

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, OpenEMSService, OpenEMSServiceArgument
from .helpers_ha import component_from_device, write_only_channels

UPDATE_COMPONENT_CONFIG_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): probatio.All(cv.ensure_list, [cv.string]),
        probatio.Required(OpenEMSServiceArgument.PROPERTIES): probatio.Schema(
            {cv.string: object}
        ),
    }
)

_SECONDS = probatio.All(probatio.Coerce(float), probatio.Range(min=0))

WRITE_CHANNEL_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): cv.string,
        probatio.Required(OpenEMSServiceArgument.CHANNEL): cv.string,
        probatio.Required(OpenEMSServiceArgument.VALUE): probatio.Any(str, int, float),
        probatio.Optional(OpenEMSServiceArgument.UPDATE_CYCLE, default=0): _SECONDS,
        probatio.Optional(OpenEMSServiceArgument.TIMEOUT, default=0): _SECONDS,
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
        for name, value in call.data[OpenEMSServiceArgument.PROPERTIES].items():
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
        try:
            await component.update_properties(values)
        except OpenEMSError as ex:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="update_component_config_failed",
                translation_placeholders={"component": component.name},
            ) from ex


async def async_write_channel(call: ServiceCall) -> None:
    """Write the value of a write-only channel via the REST API of the edge."""
    component = component_from_device(call.hass, call.data[ATTR_DEVICE_ID])
    name: str = call.data[OpenEMSServiceArgument.CHANNEL]
    value = call.data[OpenEMSServiceArgument.VALUE]
    channels = write_only_channels(component)
    if (channel := channels.get(name)) is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_write_only_channel",
            translation_placeholders={
                "component": component.name,
                "channel": name,
                "valid_channels": ", ".join(sorted(channels)) or "-",
            },
        )
    if not component.edge.rest_writable:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rest_write_not_available",
            translation_placeholders={"edge": component.edge.hostname},
        )
    placeholders = {"component": component.name, "channel": name}
    try:
        await channel.write_via_rest(
            value,
            call.data[OpenEMSServiceArgument.UPDATE_CYCLE],
            call.data[OpenEMSServiceArgument.TIMEOUT],
        )
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_channel_value",
            translation_placeholders={**placeholders, "value": str(value)},
        ) from err
    except OpenEMSError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="write_channel_failed",
            translation_placeholders=placeholders,
        ) from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the OpenEMS services."""
    hass.services.async_register(
        DOMAIN,
        OpenEMSService.UPDATE_COMPONENT_CONFIG,
        async_update_component_config,
        schema=UPDATE_COMPONENT_CONFIG_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        OpenEMSService.WRITE_CHANNEL,
        async_write_channel,
        schema=WRITE_CHANNEL_SCHEMA,
    )
