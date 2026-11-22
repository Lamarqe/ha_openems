"""Helper methods using HA classes, eg for Entity creation."""

from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
import functools
from typing import Any, Concatenate

from openems_client.connection import ConnectionProperties
from openems_client.exceptions import OpenEMSError
from openems_client.model import ChannelKind
from openems_client.openems import (
    OpenEMSBackend,
    OpenEMSChannel,
    OpenEMSComponent,
    OpenEMSEnumProperty,
    OpenEMSNumberProperty,
    OpenEMSProperty,
    OpenEMSTimeProperty,
)

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_TYPE,
    CONF_URL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1


def component_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store caching the edge configuration of a config entry."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


@dataclass
class DeviceRuntimeData:
    """Data class to store all relevant runtime data."""

    info: dr.DeviceInfo
    entry: dr.DeviceEntry


@dataclass
class RuntimeData:
    """Data class to store all relevant runtime data."""

    backend: OpenEMSBackend
    edge_device: DeviceRuntimeData
    # components with an enabled device, the platforms create entities only for these
    enabled_components: list[OpenEMSComponent]


type OpenEMSConfigEntry = ConfigEntry[RuntimeData]


def component_identifier(component: OpenEMSComponent) -> tuple[str, str]:
    """Provide the device registry identifier of an OpenEMSComponent."""
    return (DOMAIN, component.unique_id)


def component_device(
    edge_device: dr.DeviceEntry, component: OpenEMSComponent
) -> dr.DeviceInfo:
    """Provide the device of an OpenEMSComponent."""
    return dr.DeviceInfo(
        name=component.edge.hostname + " " + component.name,
        model=component.alias,
        model_id=component.factory_id,
        identifiers={component_identifier(component)},
        via_device_id=edge_device.id,
    )


def component_from_device(hass: HomeAssistant, device_id: str) -> OpenEMSComponent:
    """Return the OpenEMSComponent represented by a device.

    Raise ServiceValidationError if the device does not represent a component of a loaded edge.
    """
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        entry: OpenEMSConfigEntry | None = hass.config_entries.async_get_entry(
            device.config_entry_id
        )
        if (
            entry is not None
            and entry.domain == DOMAIN
            and entry.state is ConfigEntryState.LOADED
        ):
            for component in entry.runtime_data.backend.the_edge.components.values():
                if component_identifier(component) in device.identifiers:
                    return component
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="no_component_device",
        translation_placeholders={"device_id": device_id},
    )


def write_only_channels(component: OpenEMSComponent) -> dict[str, OpenEMSChannel]:
    """Return the write-only channels of a component by name."""
    return {
        channel.name: channel
        for channel in component.channels_of(ChannelKind.WRITE_ONLY, OpenEMSChannel)
    }


def property_selector(prop: OpenEMSProperty) -> dict[str, Any]:
    """Return the selector config matching the type of the property."""
    if isinstance(prop, OpenEMSEnumProperty):
        return {
            "select": {
                "options": prop.option_keys,
                "mode": "dropdown",
                # option translations in section "selector" of strings.json
                "translation_key": prop.key,
            }
        }
    if isinstance(prop, OpenEMSNumberProperty):
        number_config: dict[str, Any] = {
            "min": prop.lower_limit,
            "max": prop.upper_limit,
            "step": prop.step,
            "mode": "box",
        }
        if prop.unit:
            number_config["unit_of_measurement"] = prop.unit
        return {"number": number_config}
    if isinstance(prop, OpenEMSTimeProperty):
        return {"time": {}}
    return {"boolean": {}}


def map_user_input(user_input: dict[str, str]) -> ConnectionProperties:
    """Map user input to connection parameters."""
    return ConnectionProperties(
        host=user_input.get(CONF_HOST),
        password=user_input[CONF_PASSWORD],
        type=user_input[CONF_TYPE],
        url=user_input.get(CONF_URL),
        username=user_input[CONF_USERNAME],
    )


def handle_write_errors[_EntityT: Entity, **_P](
    func: Callable[Concatenate[_EntityT, _P], Awaitable[None]],
) -> Callable[Concatenate[_EntityT, _P], Coroutine[Any, Any, None]]:
    """Raise a translated HomeAssistantError when writing a value to the edge fails."""

    @functools.wraps(func)
    async def wrapper(self: _EntityT, *args: _P.args, **kwargs: _P.kwargs) -> None:
        try:
            await func(self, *args, **kwargs)
        except OpenEMSError as ex:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"entity_id": self.entity_id},
            ) from ex

    return wrapper
