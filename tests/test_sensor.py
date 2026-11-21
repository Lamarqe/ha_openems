"""Tests for the OpenEMS sensor platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import UNDEFINED, UndefinedType
import pytest

from custom_components.openems import sensor
from custom_components.openems.const import (
    CONF_ADVANCED_OPTIONS,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
)
from openems_client.model import ChannelKind
from openems_client.openems import OpenEMSChannel, OpenEMSEdge
from custom_components.openems.sensor import (
    OpenEMSSensorDescription,
    OpenEMSSensorEntity,
)

from .helpers_component import HOSTNAME, make_channel, make_edge, make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity(
    channel=None, state_class=None, ignore_decreasing: bool = False
) -> OpenEMSSensorEntity:
    """Build an OpenEMSSensorEntity backed by a mock channel."""
    if channel is None:
        channel = MagicMock()
        channel.value = None
    desc = OpenEMSSensorDescription(
        key="test/key",
        name="TestChannel",
        state_class=state_class,
        ignore_decreasing=ignore_decreasing,
    )
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    return OpenEMSSensorEntity(channel, desc, device_info)


# ---------------------------------------------------------------------------
# native_value behaviour
# ---------------------------------------------------------------------------


def test_native_value_none_when_channel_has_no_value() -> None:
    """Entity returns None when the underlying channel carries no value."""
    channel = MagicMock()
    channel.value = None
    assert _make_entity(channel).native_value is None


def test_native_value_option_key_passed_through() -> None:
    """Option keys of ENUM sensors are returned as provided by the channel."""
    channel = MagicMock()
    channel.value = "excess_power"
    assert _make_entity(channel).native_value == "excess_power"


def test_native_value_numeric_passed_through_unchanged() -> None:
    """Numeric values are returned as-is."""
    channel = MagicMock()
    channel.value = 42.5
    assert _make_entity(channel).native_value == 42.5


@pytest.mark.parametrize(
    ("ignore_decreasing", "new_value", "expected_value"),
    [
        pytest.param(True, 50.0, 100.0, id="decrease_ignored"),
        pytest.param(True, 150.0, 150.0, id="increase_accepted"),
        pytest.param(False, 50.0, 50.0, id="decrease_accepted_when_disabled"),
    ],
)
def test_total_increasing_channel_update(
    ignore_decreasing: bool, new_value: float, expected_value: float
) -> None:
    """A channel update keeps the previous value only for an ignored decrease."""
    channel = MagicMock()
    entity = _make_entity(
        channel,
        state_class=SensorStateClass.TOTAL_INCREASING,
        ignore_decreasing=ignore_decreasing,
    )
    entity.async_schedule_update_ha_state = MagicMock()
    entity.previous_increasing_value_not_null = 100.0

    channel.value = new_value
    entity._handle_channel_update()

    assert entity.native_value == expected_value
    assert entity.previous_increasing_value_not_null == expected_value
    entity.async_schedule_update_ha_state.assert_called_once()


def test_native_value_does_not_change_the_previous_value() -> None:
    """Reading the value has no side effects, only channel updates are remembered."""
    channel = MagicMock()
    channel.value = 150.0
    entity = _make_entity(
        channel, state_class=SensorStateClass.TOTAL_INCREASING, ignore_decreasing=True
    )
    entity.previous_increasing_value_not_null = 100.0

    assert entity.native_value == 150.0
    assert entity.previous_increasing_value_not_null == 100.0


# ---------------------------------------------------------------------------
# Callback wiring
# ---------------------------------------------------------------------------


async def test_async_added_adds_listener_removed_with_entity() -> None:
    """The entity listens to the channel until it is removed."""
    channel = MagicMock()
    entity = _make_entity(channel)

    with patch.object(SensorEntity, "async_added_to_hass", new=AsyncMock()):
        await entity.async_added_to_hass()

    channel.add_listener.assert_called_once_with(entity._handle_channel_update)
    remove_listener = channel.add_listener.return_value
    remove_listener.assert_not_called()
    entity._call_on_remove_callbacks()
    remove_listener.assert_called_once()


def test_enum_channel_provides_option_key() -> None:
    """An ENUM sensor shows the option key of the backend integer value."""
    edge = make_edge(
        {
            "ctrlEvcs0": [
                make_channel(
                    "ChargeMode",
                    category="ENUM",
                    options={"EXCESS_POWER": 0, "MANUAL": 1, "OFF": 2},
                )
            ]
        }
    )
    channel = edge.components["ctrlEvcs0"].channels_of(ChannelKind.ENUM_SENSOR)[0]
    channel.handle_data_update("ctrlEvcs0/ChargeMode", 1)

    desc = OpenEMSSensorDescription(
        key=channel.unique_id,
        name="ChargeMode",
        device_class=SensorDeviceClass.ENUM,
    )
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    entity = OpenEMSSensorEntity(channel, desc, device_info)

    assert entity.native_value == "manual"


async def _setup_sensors(
    hass: HomeAssistant, edge: OpenEMSEdge, ignore_decreasing: bool = False
) -> dict[str, OpenEMSSensorEntity]:
    """Set up the sensor platform, return the entities by channel name."""
    created: list[OpenEMSSensorEntity] = []
    with patch(
        "custom_components.openems.sensor.dr.async_get", return_value=MagicMock()
    ):
        await sensor.async_setup_entry(
            hass, make_entry(edge, ignore_decreasing), created.extend
        )
    return {entity._channel.name: entity for entity in created}


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("component_name", "expected_name", "enabled"),
    [
        pytest.param("meter0", UNDEFINED, True, id="primary_channel_translated_name"),
        pytest.param("comp1", "ActivePower", False, id="other_channel_channel_id"),
    ],
)
async def test_async_setup_entry_creates_sensor_entities(
    hass: HomeAssistant,
    component_name: str,
    expected_name: str | UndefinedType,
    enabled: bool,
) -> None:
    """async_setup_entry creates one entity per sensor in the component."""
    edge = make_edge({component_name: [make_channel("ActivePower", unit="W")]})

    entities = await _setup_sensors(hass, edge, ignore_decreasing=True)

    description = entities["ActivePower"].entity_description
    assert description.key == f"{HOSTNAME}/0/{component_name}/ActivePower"
    assert description.name == expected_name
    assert description.translation_key == "active_power"
    assert description.entity_registry_enabled_default is enabled
    assert description.ignore_decreasing is True


@pytest.mark.parametrize(
    ("channel_id", "unit", "device_class", "state_class", "native_unit"),
    [
        pytest.param(
            "ActivePower",
            "W",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            "W",
            id="power",
        ),
        pytest.param(
            "Capacity",
            "Wh",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL,
            "Wh",
            id="energy",
        ),
        pytest.param(
            "ActiveEnergy",
            "Wh_Σ",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
            "Wh",
            id="energy_counter",
        ),
        pytest.param(
            "OffGridTime",
            "sec_Σ",
            SensorDeviceClass.DURATION,
            SensorStateClass.TOTAL_INCREASING,
            "s",
            id="duration_counter",
        ),
        pytest.param(
            "Soc",
            "%",
            SensorDeviceClass.BATTERY,
            SensorStateClass.MEASUREMENT,
            "%",
            id="state_of_charge",
        ),
        pytest.param(
            "Efficiency", "%", None, SensorStateClass.MEASUREMENT, "%", id="percentage"
        ),
        pytest.param(
            "CpuTemperature",
            "°C",
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
            "°C",
            id="temperature",
        ),
        pytest.param(
            "Counter", "", None, SensorStateClass.MEASUREMENT, None, id="no_unit"
        ),
    ],
)
async def test_async_setup_entry_sensor_classes(
    hass: HomeAssistant,
    channel_id: str,
    unit: str,
    device_class: SensorDeviceClass | None,
    state_class: SensorStateClass,
    native_unit: str | None,
) -> None:
    """Units of the channels determine device class, state class and unit."""
    edge = make_edge({"comp1": [make_channel(channel_id, unit=unit)]})

    description = (await _setup_sensors(hass, edge))[channel_id].entity_description

    assert description.device_class == device_class
    assert description.state_class == state_class
    assert description.native_unit_of_measurement == native_unit


async def test_async_setup_entry_text_and_float_sensors(
    hass: HomeAssistant,
) -> None:
    """Text channels become sensors without classes, floats are numeric values."""
    edge = make_edge(
        {
            "battery0": [
                make_channel("Tower0BmsSerialNumber", "STRING"),
                make_channel("CellTemperature", "FLOAT", "°C"),
            ]
        }
    )

    entities = await _setup_sensors(hass, edge)

    serial = entities["Tower0BmsSerialNumber"]
    description = serial.entity_description
    assert description.device_class is None
    assert description.state_class is None
    assert description.native_unit_of_measurement is None
    assert description.translation_key == "tower0_bms_serial_number"
    serial._channel.handle_data_update("battery0/Tower0BmsSerialNumber", "SN-0815")
    assert serial.native_value == "SN-0815"

    temperature = entities["CellTemperature"]
    assert temperature.entity_description.device_class == SensorDeviceClass.TEMPERATURE
    temperature._channel.handle_data_update("battery0/CellTemperature", 21.5)
    assert temperature.native_value == 21.5


async def test_async_setup_entry_enum_sensor_sets_options(
    hass: HomeAssistant,
) -> None:
    """ENUM channels expose their option keys on the entity description."""
    edge = make_edge(
        {
            "ctrlEvcs0": [
                make_channel(
                    "ChargeMode",
                    category="ENUM",
                    options={"EXCESS_POWER": 0, "FORCE_CHARGE": 1},
                )
            ]
        }
    )

    description = (await _setup_sensors(hass, edge))["ChargeMode"].entity_description

    assert description.device_class == SensorDeviceClass.ENUM
    assert description.state_class is None
    assert description.options == ["excess_power", "force_charge"]


async def test_async_setup_entry_creates_derived_sensors(
    hass: HomeAssistant,
) -> None:
    """Battery components get the voltage difference sensors of the library."""
    edge = make_edge(
        {
            "battery0": [
                make_channel("MaxCellVoltage", unit="mV"),
                make_channel("MinCellVoltage", unit="mV"),
            ]
        }
    )

    entities = await _setup_sensors(hass, edge)

    description = entities["VoltageDifference"].entity_description
    assert description.device_class == SensorDeviceClass.VOLTAGE
    assert description.native_unit_of_measurement == "mV"
    assert description.translation_key == "voltage_difference"


async def test_async_setup_entry_skips_disabled_component(
    hass: HomeAssistant,
) -> None:
    """Components without an enabled device produce no sensor entities."""
    mock_component = MagicMock()
    mock_component.name = "ignored"

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "test-host"
    mock_backend.the_edge.components = {"ignored": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.options = {
        CONF_ADVANCED_OPTIONS: {CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: True}
    }
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.enabled_components = []

    created: list = []
    with patch(
        "custom_components.openems.sensor.dr.async_get", return_value=MagicMock()
    ):
        await sensor.async_setup_entry(hass, mock_entry, created.extend)

    assert created == []


async def test_rest_write_action_writes_via_rest() -> None:
    """The REST write action passes value, update cycle and timeout to the channel."""
    channel = MagicMock(spec=OpenEMSChannel)
    channel.write_via_rest = AsyncMock()
    entity = _make_entity(channel)

    await entity.update_value(value="12.5", update_cycle=30, timeout=600)

    channel.write_via_rest.assert_awaited_once_with(12.5, 30, 600)
