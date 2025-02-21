"""OpenEMS Helper methods eg for Entity creation."""

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass


def unit_to_deviceclass(unit: str) -> SensorDeviceClass | None:
    """Derive SensorDeviceClass from unit string."""
    match unit:
        case "kWh" | "Wh":
            return SensorDeviceClass.ENERGY
        case "W":
            return SensorDeviceClass.POWER
        case "A" | "mA":
            return SensorDeviceClass.CURRENT
        case "Hz" | "mHz":
            return SensorDeviceClass.FREQUENCY
        case "h" | "min" | "s" | "ms":
            return SensorDeviceClass.DURATION
        case "%":
            return SensorDeviceClass.BATTERY
        case "V" | "mV":
            return SensorDeviceClass.VOLTAGE
        case "var":
            return SensorDeviceClass.REACTIVE_POWER
        case "VA":
            return SensorDeviceClass.APPARENT_POWER
        case _:
            return None


def device_to_state_class(device_class: SensorDeviceClass) -> SensorStateClass | None:
    """Derive SensorStateClass from SensorDeviceClass."""
    match device_class:
        case SensorDeviceClass.ENERGY:
            return None
        case SensorDeviceClass.ENUM:
            return None
        case _:
            return SensorStateClass.MEASUREMENT
