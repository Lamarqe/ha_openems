"""Tests for the OpenEMS sensor platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.openems import sensor
from custom_components.openems.const import \
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING
from custom_components.openems.sensor import (OpenEMSSensorDescription,
                                              OpenEMSSensorEntity)

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
    entity = _make_entity(
        channel, state_class=SensorStateClass.TOTAL_INCREASING)
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
    entity = _make_entity(
        channel, state_class=SensorStateClass.TOTAL_INCREASING)
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
    entity = _make_entity(
        channel, state_class=SensorStateClass.TOTAL_INCREASING)
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


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def test_async_setup_entry_creates_sensor_entities(hass: HomeAssistant) -> None:
    """async_setup_entry creates one entity per sensor in the component."""
    mock_channel = MagicMock()
    mock_channel.options = None
    mock_channel.unit = "W"
    mock_channel.name = "ActivePower"
    mock_channel.unique_id.return_value = "test-host/edge-1/comp1/ActivePower"
    mock_channel.orig_json = {
        "id": "ActivePower", "type": "INTEGER", "unit": "W"}

    mock_component = MagicMock()
    mock_component.name = "comp1"
    mock_component.alias = "Test Component"
    mock_component.edge.hostname = "test-host"
    mock_component.create_entities = True
    mock_component.sensors = [mock_channel]
    mock_component.derived_sensors = []

    # link channel back to its component so translation_key() can read .component.name
    mock_channel.component = mock_component

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "test-host"
    mock_backend.the_edge.components = {"comp1": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.add_component_callbacks = {}

    created: list[OpenEMSSensorEntity] = []
    with patch("custom_components.openems.sensor.dr.async_get", return_value=MagicMock()):
        await sensor.async_setup_entry(hass, mock_entry, created.extend)

    assert len(created) == 1
    assert isinstance(created[0], OpenEMSSensorEntity)


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
    mock_entry.runtime_data.add_component_callbacks = {}

    created: list = []
    with patch("custom_components.openems.sensor.dr.async_get", return_value=MagicMock()):
        await sensor.async_setup_entry(hass, mock_entry, created.extend)

    assert created == []


async def test_async_setup_entry_registers_add_component_callback(
    hass: HomeAssistant,
) -> None:
    """async_setup_entry stores a callback for dynamic component entity creation."""
    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "test-host"
    mock_backend.the_edge.components = {}

    callbacks: dict = {}
    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.add_component_callbacks = callbacks

    with patch("custom_components.openems.sensor.dr.async_get", return_value=MagicMock()):
        await sensor.async_setup_entry(hass, mock_entry, MagicMock())

    assert "sensor" in callbacks
    assert callable(callbacks["sensor"])
