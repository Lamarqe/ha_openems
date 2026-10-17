"""Helper methods using HA classes, eg for Entity creation."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntFlag
import re
from typing import Any, ClassVar

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_TYPE,
    CONF_URL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, SLASH_ESC
from .entry_data import ConnectionProperties
from .helpers import to_snake_case
from .openems import (
    OpenEMSBackend,
    OpenEMSChannel,
    OpenEMSComponent,
    OpenEMSDataHandler,
    OpenEMSEnumProperty,
    OpenEMSNumberProperty,
    OpenEMSProperty,
    OpenEMSTimeProperty,
)


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
    add_component_callbacks: ClassVar[dict[str, Callable]] = {}


type OpenEMSConfigEntry = ConfigEntry[RuntimeData]


@dataclass
class OpenEMSUnitClass:
    """Describe the Unit and its Nature."""

    unit: str
    sensor_device_class: SensorDeviceClass | None = None
    number_device_class: NumberDeviceClass | None = None
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT


class OpenEMSEntityFeature(IntFlag):
    """Supported features of openems sensor entities."""

    READ = 1
    WRITE = 2


def unit_description(unit: str) -> OpenEMSUnitClass:
    """Correct unit and derive SensorDeviceClass and SensorStateClass."""
    # reference:  openems/io.openems.common/src/io/openems/common/channel/Unit.java
    sensor_type: OpenEMSUnitClass = OpenEMSUnitClass(unit=unit)
    match unit:
        case "kWh" | "Wh":
            sensor_type.sensor_device_class = SensorDeviceClass.ENERGY
            sensor_type.number_device_class = NumberDeviceClass.ENERGY
            sensor_type.state_class = SensorStateClass.TOTAL
        case "Wh_Σ":
            sensor_type.unit = "Wh"
            sensor_type.sensor_device_class = SensorDeviceClass.ENERGY
            sensor_type.number_device_class = NumberDeviceClass.ENERGY
            sensor_type.state_class = SensorStateClass.TOTAL_INCREASING
        case "W" | "mW" | "kW":
            sensor_type.sensor_device_class = SensorDeviceClass.POWER
            sensor_type.number_device_class = NumberDeviceClass.POWER
        case "A" | "mA":
            sensor_type.sensor_device_class = SensorDeviceClass.CURRENT
            sensor_type.number_device_class = NumberDeviceClass.CURRENT
        case "Hz" | "mHz":
            sensor_type.sensor_device_class = SensorDeviceClass.FREQUENCY
            sensor_type.number_device_class = NumberDeviceClass.FREQUENCY
        case "sec_Σ":
            sensor_type.unit = "s"
            sensor_type.sensor_device_class = SensorDeviceClass.DURATION
            sensor_type.number_device_class = NumberDeviceClass.DURATION
        case "h" | "min" | "s" | "ms":
            sensor_type.sensor_device_class = SensorDeviceClass.DURATION
            sensor_type.number_device_class = NumberDeviceClass.DURATION
        case "sec":
            sensor_type.unit = "s"
            sensor_type.sensor_device_class = SensorDeviceClass.DURATION
            sensor_type.number_device_class = NumberDeviceClass.DURATION
        case "%":
            sensor_type.sensor_device_class = SensorDeviceClass.BATTERY
            sensor_type.number_device_class = NumberDeviceClass.BATTERY
        case "V" | "mV":
            sensor_type.sensor_device_class = SensorDeviceClass.VOLTAGE
            sensor_type.number_device_class = NumberDeviceClass.VOLTAGE
        case "bar" | "mbar":
            sensor_type.sensor_device_class = SensorDeviceClass.PRESSURE
            sensor_type.number_device_class = NumberDeviceClass.PRESSURE
        case "var":
            sensor_type.sensor_device_class = SensorDeviceClass.REACTIVE_POWER
            sensor_type.number_device_class = NumberDeviceClass.REACTIVE_POWER
        case "VA":
            sensor_type.sensor_device_class = SensorDeviceClass.APPARENT_POWER
            sensor_type.number_device_class = NumberDeviceClass.APPARENT_POWER
        case "C":
            sensor_type.unit = "°C"
            sensor_type.sensor_device_class = SensorDeviceClass.TEMPERATURE
            sensor_type.number_device_class = NumberDeviceClass.TEMPERATURE
    return sensor_type


def supported_features(channel: OpenEMSChannel) -> OpenEMSEntityFeature | None:
    """Derive supported features for given channel."""
    match channel.orig_json.get("accessMode", ""):
        case "RO":
            return OpenEMSEntityFeature.READ
        case "RW":
            return OpenEMSEntityFeature.READ | OpenEMSEntityFeature.WRITE
        case "WO":
            return OpenEMSEntityFeature.WRITE
        case _:
            return None


def component_identifier(component: OpenEMSComponent) -> tuple[str, str]:
    """Provide the device registry identifier of an OpenEMSComponent."""
    return (DOMAIN, component.edge.hostname + " " + component.name)


def component_device(
    edge_device: dr.DeviceEntry, component: OpenEMSComponent
) -> dr.DeviceInfo:
    """Provide the device of an OpenEMSComponent."""
    return dr.DeviceInfo(
        name=component.edge.hostname + " " + component.name,
        model=component.alias,
        identifiers={component_identifier(component)},
        via_device_id=edge_device.id,
        entry_type=dr.DeviceEntryType.SERVICE,
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
            edge = entry.runtime_data.backend.the_edge
            # identifier structure: "<hostname> <component name>", see component_identifier.
            # The hostname may contain spaces itself (multi edge setups).
            prefix = edge.hostname + " "
            for domain, identifier in device.identifiers:
                if (
                    domain == DOMAIN
                    and identifier.startswith(prefix)
                    and (
                        component := edge.components.get(
                            identifier.removeprefix(prefix)
                        )
                    )
                ):
                    return component
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="no_component_device",
        translation_placeholders={"device_id": device_id},
    )


def property_selector(prop: OpenEMSProperty) -> dict[str, Any]:
    """Return the selector config matching the type of the property."""
    if isinstance(prop, OpenEMSEnumProperty):
        return {
            "select": {
                "options": [to_snake_case(o) for o in prop.property_options],
                "mode": "dropdown",
                # option translations in section "selector" of strings.json
                "translation_key": translation_key(prop),
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
            number_config["unit_of_measurement"] = unit_description(prop.unit).unit
        return {"number": number_config}
    if isinstance(prop, OpenEMSTimeProperty):
        return {"time": {}}
    return {"boolean": {}}


def translation_key(channel: OpenEMSDataHandler) -> str:
    """Generate translation key for given channel."""
    if isinstance(channel, OpenEMSProperty):
        channel_name = channel.name[9:]
    else:
        channel_name = channel.name
    component_name = re.sub(r"\d+$", "", channel.component.name).lstrip("_")
    return to_snake_case(component_name) + SLASH_ESC + to_snake_case(channel_name)


def map_user_input(user_input: dict[str, str]) -> ConnectionProperties:
    """Map user input to connection parameters."""
    return ConnectionProperties(
        host=user_input.get(CONF_HOST),
        password=user_input[CONF_PASSWORD],
        type=user_input[CONF_TYPE],
        url=user_input.get(CONF_URL),
        username=user_input[CONF_USERNAME],
    )
