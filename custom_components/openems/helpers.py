"""OpenEMS Helper methods eg for Entity creation."""

from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass


@dataclass
class OpenEMSSensorUnitClass:
    """Describe the Unit and its Nature."""

    unit: str
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT


def unit_description(unit: str) -> OpenEMSSensorUnitClass:
    """Correct unit and derive SensorDeviceClass and SensorStateClass."""
    sensor_type: OpenEMSSensorUnitClass = OpenEMSSensorUnitClass(unit=unit)
    match unit:
        case "kWh" | "Wh":
            sensor_type.device_class = SensorDeviceClass.ENERGY
            sensor_type.state_class = SensorStateClass.TOTAL
        case "Wh_Σ":
            sensor_type.unit = "Wh"
            sensor_type.device_class = SensorDeviceClass.ENERGY
            sensor_type.state_class = SensorStateClass.TOTAL_INCREASING
        case "W":
            sensor_type.device_class = SensorDeviceClass.POWER
        case "A" | "mA":
            sensor_type.device_class = SensorDeviceClass.CURRENT
        case "Hz" | "mHz":
            sensor_type.device_class = SensorDeviceClass.FREQUENCY
        case "h" | "min" | "s" | "ms":
            sensor_type.device_class = SensorDeviceClass.DURATION
        case "%":
            sensor_type.device_class = SensorDeviceClass.BATTERY
        case "V" | "mV":
            sensor_type.device_class = SensorDeviceClass.VOLTAGE
        case "var":
            sensor_type.device_class = SensorDeviceClass.REACTIVE_POWER
        case "VA":
            sensor_type.device_class = SensorDeviceClass.APPARENT_POWER
    return sensor_type
