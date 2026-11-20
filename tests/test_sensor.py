"""Tests for the OpenEMS sensor platform."""

from openems_client.const import CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING
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
from openems_client.openems import OpenEMSChannel, OpenEMSEdge
from yarl import URL
from custom_components.openems.sensor import (
    OpenEMSSensorDescription,
    OpenEMSSensorEntity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity(channel=None, state_class=None) -> OpenEMSSensorEntity:
    """Build an OpenEMSSensorEntity backed by a mock channel."""
    if channel is None:
        channel = MagicMock()
        channel.native_value = None
    desc = OpenEMSSensorDescription(
        key="test/key",
        name="TestChannel",
        state_class=state_class,
    )
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    return OpenEMSSensorEntity(channel, desc, device_info)


# ---------------------------------------------------------------------------
# native_value behaviour
# ---------------------------------------------------------------------------


def test_native_value_none_when_channel_has_no_value() -> None:
    """Entity returns None when the underlying channel carries no value."""
    channel = MagicMock()
    channel.native_value = None
    assert _make_entity(channel).native_value is None


def test_native_value_string_converted_to_snake_case() -> None:
    """String values (ENUM sensors) are lowercased and normalised to snake_case."""
    channel = MagicMock()
    channel.native_value = "EXCESS_POWER"
    assert _make_entity(channel).native_value == "excess_power"


def test_native_value_numeric_passed_through_unchanged() -> None:
    """Numeric values are returned as-is."""
    channel = MagicMock()
    channel.native_value = 42.5
    assert _make_entity(channel).native_value == 42.5


def test_native_value_total_increasing_ignores_decrease_when_enabled() -> None:
    """A new value lower than the previous one is discarded when ignore_decreasing=True."""
    channel = MagicMock()
    channel.native_value = 50.0
    channel.component.edge.advanced_options = {
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: True
    }
    entity = _make_entity(channel, state_class=SensorStateClass.TOTAL_INCREASING)
    entity.previous_increasing_value_not_null = 100.0

    assert entity.native_value == 100.0
    assert entity.previous_increasing_value_not_null == 100.0  # not updated


def test_native_value_total_increasing_accepts_new_higher_value() -> None:
    """An increasing value is returned and stored for future comparisons."""
    channel = MagicMock()
    channel.native_value = 150.0
    channel.component.edge.advanced_options = {
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: True
    }
    entity = _make_entity(channel, state_class=SensorStateClass.TOTAL_INCREASING)
    entity.previous_increasing_value_not_null = 100.0

    assert entity.native_value == 150.0
    assert entity.previous_increasing_value_not_null == 150.0


def test_native_value_total_increasing_decrease_allowed_when_disabled() -> None:
    """A lower value is returned normally when ignore_decreasing=False."""
    channel = MagicMock()
    channel.native_value = 50.0
    channel.component.edge.advanced_options = {
        CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: False
    }
    entity = _make_entity(channel, state_class=SensorStateClass.TOTAL_INCREASING)
    entity.previous_increasing_value_not_null = 100.0

    assert entity.native_value == 50.0
    assert entity.previous_increasing_value_not_null == 50.0


# ---------------------------------------------------------------------------
# Callback wiring
# ---------------------------------------------------------------------------


async def test_async_added_registers_callback_on_channel() -> None:
    """async_added_to_hass passes the HA-update callback to the channel."""
    channel = MagicMock()
    channel.native_value = None
    entity = _make_entity(channel)

    with patch.object(SensorEntity, "async_added_to_hass", new=AsyncMock()):
        await entity.async_added_to_hass()

    channel.register_callback.assert_called_once_with(
        entity.async_schedule_update_ha_state
    )


async def test_async_will_remove_unregisters_callback() -> None:
    """async_will_remove_from_hass tears down the channel callback."""
    channel = MagicMock()
    channel.native_value = None
    entity = _make_entity(channel)

    with patch.object(SensorEntity, "async_will_remove_from_hass", new=AsyncMock()):
        await entity.async_will_remove_from_hass()

    channel.unregister_callback.assert_called_once()


def test_enum_channel_maps_integer_to_snake_case_option() -> None:
    """An ENUM channel converts the backend integer value to a snake_case option string.

    OpenEMSChannel stores options as {int_value: option_name}.  A data update
    with the matching integer should surface as the lower-cased option name on
    the entity.
    """
    comp = MagicMock()
    comp.name = "ctrlEvcs0"
    comp.edge.hostname = "host"
    comp.edge.id = "edge-1"
    comp.edge.register_channel = MagicMock()
    comp.edge.unregister_channel = MagicMock()

    channel_json = {"id": "ChargeMode", "type": "STRING", "unit": ""}
    # options are stored as {name: int_value}; OpenEMSChannel inverts them to {int_value: name}
    options = {"EXCESS_POWER": 0, "MANUAL": 1, "OFF": 2}
    channel_json["category"] = "ENUM"  # mark as enum so options are applied
    # rebuild with category set so the branch is exercised
    channel = OpenEMSChannel(
        component=comp,
        channel_json={**channel_json, "category": "ENUM"},
        options=options,
    )

    channel.handle_data_update("ctrlEvcs0/ChargeMode", 1)  # 1 → "MANUAL"

    desc = OpenEMSSensorDescription(
        key=channel.unique_id(),
        name="ChargeMode",
        device_class=SensorDeviceClass.ENUM,
        state_class=None,
    )
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    entity = OpenEMSSensorEntity(channel, desc, device_info)

    assert entity.native_value == "manual"


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("component_name", "expected_name"),
    [
        pytest.param("meter0", UNDEFINED, id="default_channel_translated_name"),
        pytest.param("comp1", "ActivePower", id="other_channel_channel_id"),
    ],
)
async def test_async_setup_entry_creates_sensor_entities(
    hass: HomeAssistant, component_name: str, expected_name: str | UndefinedType
) -> None:
    """async_setup_entry creates one entity per sensor in the component."""
    mock_channel = MagicMock()
    mock_channel.options = None
    mock_channel.unit = "W"
    mock_channel.name = "ActivePower"
    mock_channel.unique_id.return_value = (
        f"test-host/edge-1/{component_name}/ActivePower"
    )

    mock_component = MagicMock()
    mock_component.name = component_name
    mock_component.alias = "Test Component"
    mock_component.edge.hostname = "test-host"
    mock_component.create_entities = True
    mock_component.sensors = [mock_channel]
    mock_component.create_derived_channels.return_value = []

    # link channel back to its component so translation_key() can read .component.name
    mock_channel.component = mock_component

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "test-host"
    mock_backend.the_edge.components = {component_name: mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend

    created: list[OpenEMSSensorEntity] = []
    with patch(
        "custom_components.openems.sensor.dr.async_get", return_value=MagicMock()
    ):
        await sensor.async_setup_entry(hass, mock_entry, created.extend)

    assert len(created) == 1
    assert isinstance(created[0], OpenEMSSensorEntity)
    assert created[0].entity_description.name == expected_name


async def test_async_setup_entry_skips_component_with_create_entities_false(
    hass: HomeAssistant,
) -> None:
    """Components where create_entities=False produce no sensor entities."""
    mock_component = MagicMock()
    mock_component.name = "ignored"
    mock_component.create_entities = False

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "test-host"
    mock_backend.the_edge.components = {"ignored": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend

    created: list = []
    with patch(
        "custom_components.openems.sensor.dr.async_get", return_value=MagicMock()
    ):
        await sensor.async_setup_entry(hass, mock_entry, created.extend)

    assert created == []


async def test_combined_sensors_for_battery() -> None:
    """Battery components get the voltage difference sensors with calculated values."""
    channels = [
        {"id": channel_id, "type": "INTEGER", "unit": "mV", "accessMode": "RO"}
        for channel_id in (
            "MaxCellVoltage",
            "MinCellVoltage",
            "Tower0Module0Cell000Voltage",
            "Tower0Module0Cell001Voltage",
            "Tower0Module1Cell000Voltage",
        )
    ]
    backend = MagicMock()
    backend.multi_edge = False
    backend.connection.conn_url = URL("ws://localhost:8085/")
    edge = OpenEMSEdge(
        backend,
        "edge0",
        {
            "_host": {"Hostname": "testhost"},
            "battery0": {"properties": {}, "channels": channels},
        },
    )
    try:
        derived = {
            channel.name: channel
            for channel in edge.components["battery0"].create_derived_channels(
                sensor.combined_sensor_definitions("battery0")
            )
        }
        assert set(derived) == {
            "VoltageDifference",
            "Tower0Module0CellVoltageDifference",
            "Tower0Module1CellVoltageDifference",
        }
        assert all(channel.unit == "mV" for channel in derived.values())

        voltage_difference = derived["VoltageDifference"]
        voltage_difference.handle_data_update("battery0/MaxCellVoltage", 3500)
        voltage_difference.handle_data_update("battery0/MinCellVoltage", 3300)
        assert voltage_difference.native_value == 200

        module_difference = derived["Tower0Module0CellVoltageDifference"]
        module_difference.handle_data_update(
            "battery0/Tower0Module0Cell000Voltage", 3310
        )
        assert module_difference.native_value is None
        module_difference.handle_data_update(
            "battery0/Tower0Module0Cell001Voltage", 3350
        )
        assert module_difference.native_value == 40
    finally:
        await edge.stop()


def test_combined_sensors_only_for_matching_components() -> None:
    """Components without combined sensor definitions get none."""
    assert sensor.combined_sensor_definitions("battery0")
    assert sensor.combined_sensor_definitions("ess0") == ()
