"""OpenEMS Helper methods eg for Entity creation."""

from dataclasses import dataclass
import re

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN
from .openems import OpenEMSBackend, OpenEMSChannel, OpenEMSComponent, OpenEMSProperty


@dataclass
class OpenEMSUnitClass:
    """Describe the Unit and its Nature."""

    unit: str
    sensor_device_class: SensorDeviceClass | None = None
    number_device_class: NumberDeviceClass | None = None
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT


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


def component_device(component: OpenEMSComponent) -> DeviceInfo:
    """Provide the device of an OpenEMSComponent."""
    return DeviceInfo(
        name=component.edge.hostname + " " + component.name,
        model=component.alias,
        identifiers={(DOMAIN, component.edge.hostname + " " + component.name)},
        via_device=(DOMAIN, component.edge.hostname),
        entry_type=DeviceEntryType.SERVICE,
    )


def find_channel_in_backend(
    backend: OpenEMSBackend, unique_id: str
) -> OpenEMSChannel | None:
    """Search for a unique ID in a backend and return the channel when found."""
    component: OpenEMSComponent
    for component in backend.the_edge.components.values():
        channel: OpenEMSChannel
        for channel in component.channels:
            if channel.unique_id() == unique_id:
                # found it, return it.
                return channel
    # not found.
    return None


def translation_key(channel: OpenEMSChannel) -> str:
    """Generate translation key for given channel."""
    if isinstance(channel, OpenEMSProperty):
        channel_name = channel.name[9:]
    else:
        channel_name = channel.name
    return re.sub(r"\d+$", "", channel.component.name) + "/" + channel_name
