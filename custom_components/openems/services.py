"""Services for OpenEMS devices."""

from collections import defaultdict
from typing import Any

import voluptuous as vol

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    service,
)

from .const import ATTR_PROPERTIES, ATTR_TIMEOUT, ATTR_UPDATE_CYCLE, ATTR_VALUE, DOMAIN
from .helpers_ha import (
    OpenEMSConfigEntry,
    component_from_device,
    find_channel_in_backend,
)
from .openems import OpenEMSComponent, OpenEMSNumberProperty, OpenEMSProperty

SERVICE_UPDATE_COMPONENT_CONFIG = "update_component_config"

UPDATE_COMPONENT_CONFIG_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
            vol.Optional(ATTR_PROPERTIES): vol.Schema({cv.string: object}),
            vol.Optional(ATTR_VALUE): vol.Coerce(float),
        }
    ),
    cv.has_at_least_one_key(ATTR_PROPERTIES, ATTR_VALUE),
    # properties are set per device (= component), value per number entity
    cv.key_dependency(ATTR_PROPERTIES, ATTR_DEVICE_ID),
    cv.key_dependency(ATTR_DEVICE_ID, ATTR_PROPERTIES),
    cv.key_dependency(ATTR_VALUE, ATTR_ENTITY_ID),
    cv.key_dependency(ATTR_ENTITY_ID, ATTR_VALUE),
)


def _number_property_from_entity(
    hass: HomeAssistant, entity_id: str
) -> OpenEMSNumberProperty:
    """Return the number property of an OpenEMS number entity."""
    if (entity_entry := er.async_get(hass).async_get(entity_id)) is not None and (
        entity_entry.config_entry_id is not None
    ):
        entry: OpenEMSConfigEntry | None = hass.config_entries.async_get_entry(
            entity_entry.config_entry_id
        )
        if (
            entry is not None
            and entry.domain == DOMAIN
            and entry.state is ConfigEntryState.LOADED
        ):
            channel = find_channel_in_backend(
                entry.runtime_data.backend, entity_entry.unique_id
            )
            if isinstance(channel, OpenEMSNumberProperty):
                return channel
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="no_number_entity",
        translation_placeholders={"entity_id": entity_id},
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
    for device_id in call.data.get(ATTR_DEVICE_ID, []):
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

    # legacy form: same value for all given number entities
    for entity_id in call.data.get(ATTR_ENTITY_ID, []):
        prop = _number_property_from_entity(call.hass, entity_id)
        updates[prop.component][prop] = _to_backend_value(prop, call.data[ATTR_VALUE])

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
