"""Tests for the OpenEMS binary sensor platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.openems import binary_sensor
from custom_components.openems.binary_sensor import (
    OpenEMSBinarySensorDescription,
    OpenEMSBinarySensorEntity,
)
from openems_client.openems import OpenEMSChannel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity(channel=None) -> OpenEMSBinarySensorEntity:
    """Build an OpenEMSBinarySensorEntity backed by a mock channel."""
    if channel is None:
        channel = MagicMock()
        channel.value = None
    desc = OpenEMSBinarySensorDescription(key="test/key", name="TestChannel")
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    return OpenEMSBinarySensorEntity(channel, desc, device_info)


def _make_real_channel(value=None) -> OpenEMSChannel:
    """Build a real OpenEMSChannel wired to a mock component/edge."""
    comp = MagicMock()
    comp.name = "ctrlEvcs0"
    comp.edge.hostname = "host"
    comp.edge.id = "edge-1"
    comp.edge.register_channel = MagicMock()
    comp.edge.unregister_channel = MagicMock()
    channel_json = {"id": "IsChargingEnabled", "type": "BOOLEAN", "unit": ""}
    channel = OpenEMSChannel(component=comp, channel_json=channel_json)
    if value is not None:
        channel.handle_data_update("ctrlEvcs0/IsChargingEnabled", value)
    return channel


# ---------------------------------------------------------------------------
# is_on behaviour
# ---------------------------------------------------------------------------


def test_is_on_none_when_channel_has_no_value() -> None:
    """Entity returns None when the channel carries no value yet."""
    channel = MagicMock()
    channel.value = None
    assert _make_entity(channel).is_on is None


def test_is_on_truthy_for_nonzero_int() -> None:
    """is_on is truthy when the channel value is 1."""
    channel = _make_real_channel(value=1)
    entity = _make_entity(channel)
    assert entity.is_on == 1


def test_is_on_falsy_for_zero() -> None:
    """is_on is falsy (0) when the channel reports 0."""
    channel = _make_real_channel(value=0)
    entity = _make_entity(channel)
    assert entity.is_on == 0


def test_is_on_none_for_string_value() -> None:
    """is_on returns None when native_value is not an int (e.g. unavailable)."""
    channel = MagicMock()
    channel.value = "unavailable"
    assert _make_entity(channel).is_on is None


# ---------------------------------------------------------------------------
# Callback wiring
# ---------------------------------------------------------------------------


async def test_async_added_adds_listener_removed_with_entity() -> None:
    """The entity listens to the channel until it is removed."""
    channel = MagicMock()
    entity = _make_entity(channel)

    with patch.object(BinarySensorEntity, "async_added_to_hass", new=AsyncMock()):
        await entity.async_added_to_hass()

    channel.add_listener.assert_called_once_with(entity.async_schedule_update_ha_state)
    remove_listener = channel.add_listener.return_value
    remove_listener.assert_not_called()
    entity._call_on_remove_callbacks()
    remove_listener.assert_called_once()


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_creates_binary_sensor_entities(
    hass: HomeAssistant,
) -> None:
    """async_setup_entry creates one entity per boolean_sensor in the component."""
    mock_channel = MagicMock()
    mock_channel.name = "IsChargingEnabled"
    mock_channel.unique_id.return_value = "host/edge-1/ctrlEvcs0/IsChargingEnabled"

    mock_component = MagicMock()
    mock_component.name = "ctrlEvcs0"
    mock_component.alias = "EV Charger"
    mock_component.edge.hostname = "host"
    mock_component.boolean_sensors = [mock_channel]
    mock_channel.component = mock_component

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = {"ctrlEvcs0": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.enabled_components = [mock_component]

    created: list[OpenEMSBinarySensorEntity] = []
    with patch(
        "custom_components.openems.binary_sensor.dr.async_get", return_value=MagicMock()
    ):
        await binary_sensor.async_setup_entry(hass, mock_entry, created.extend)

    assert len(created) == 1
    assert isinstance(created[0], OpenEMSBinarySensorEntity)


async def test_async_setup_entry_skips_disabled_component(
    hass: HomeAssistant,
) -> None:
    """Components without an enabled device produce no binary sensor entities."""
    mock_component = MagicMock()
    mock_component.name = "ignored"

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = {"ignored": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.enabled_components = []

    created: list = []
    with patch(
        "custom_components.openems.binary_sensor.dr.async_get", return_value=MagicMock()
    ):
        await binary_sensor.async_setup_entry(hass, mock_entry, created.extend)

    assert created == []
