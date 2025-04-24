"""OpenEMS Helper methods eg for Entity creation."""

from dataclasses import dataclass
import re

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN
from .openems import (
    OpenEMSBackend,
    OpenEMSChannel,
    OpenEMSComponent,
    OpenEMSEdge,
    OpenEMSProperty,
)


@dataclass
class OpenEMSSensorUnitClass:
    """Describe the Unit and its Nature."""

    unit: str
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT


def unit_description(unit: str) -> OpenEMSSensorUnitClass:
    """Correct unit and derive SensorDeviceClass and SensorStateClass."""
    # reference:  openems/io.openems.common/src/io/openems/common/channel/Unit.java
    sensor_type: OpenEMSSensorUnitClass = OpenEMSSensorUnitClass(unit=unit)
    match unit:
        case "kWh" | "Wh":
            sensor_type.device_class = SensorDeviceClass.ENERGY
            sensor_type.state_class = SensorStateClass.TOTAL
        case "Wh_Σ":
            sensor_type.unit = "Wh"
            sensor_type.device_class = SensorDeviceClass.ENERGY
            sensor_type.state_class = SensorStateClass.TOTAL_INCREASING
        case "W" | "mW" | "kW":
            sensor_type.device_class = SensorDeviceClass.POWER
        case "A" | "mA":
            sensor_type.device_class = SensorDeviceClass.CURRENT
        case "Hz" | "mHz":
            sensor_type.device_class = SensorDeviceClass.FREQUENCY
        case "sec_Σ":
            sensor_type.unit = "s"
            sensor_type.device_class = SensorDeviceClass.DURATION
        case "h" | "min" | "s" | "ms":
            sensor_type.device_class = SensorDeviceClass.DURATION
        case "sec":
            sensor_type.unit = "s"
            sensor_type.device_class = SensorDeviceClass.DURATION
        case "%":
            sensor_type.device_class = SensorDeviceClass.BATTERY
        case "V" | "mV":
            sensor_type.device_class = SensorDeviceClass.VOLTAGE
        case "bar" | "mbar":
            sensor_type.device_class = SensorDeviceClass.PRESSURE
        case "var":
            sensor_type.device_class = SensorDeviceClass.REACTIVE_POWER
        case "VA":
            sensor_type.device_class = SensorDeviceClass.APPARENT_POWER
        case "C":
            sensor_type.unit = "°C"
            sensor_type.device_class = SensorDeviceClass.TEMPERATURE
    return sensor_type


def component_device(component: OpenEMSComponent) -> DeviceInfo:
    """Provide the device of an OpenEMSComponent."""
    return DeviceInfo(
        name=component.edge.hostname + " " + component.name,
        model=component.alias,
        identifiers={(DOMAIN, component.edge.hostname, component.name)},
        via_device=(DOMAIN, component.edge.hostname),
        entry_type=DeviceEntryType.SERVICE,
    )


def find_channel_in_backend(
    backend: OpenEMSBackend, unique_id: str
) -> OpenEMSChannel | None:
    """Search for a unique ID in a backend and return the channel when found."""
    edge: OpenEMSEdge
    for edge in backend.edges.values():
        component: OpenEMSComponent
        for component in edge.components.values():
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
