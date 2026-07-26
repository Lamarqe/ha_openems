"""Tests for the OpenEMS switch platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.openems import switch
from custom_components.openems.switch import (OpenEMSSwitchDescription,
                                              OpenEMSSwitchEntity)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_channel(is_on=None):
    ch = MagicMock()
    ch.is_on = is_on
    ch.orig_json = {}
    return ch


def _make_entity(channel=None) -> OpenEMSSwitchEntity:
    if channel is None:
        channel = _make_channel()
    desc = OpenEMSSwitchDescription(key="test/key", name="EnabledCharging")
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    return OpenEMSSwitchEntity(channel, desc, device_info)


# ---------------------------------------------------------------------------
# is_on
# ---------------------------------------------------------------------------

def test_is_on_true_when_channel_is_on() -> None:
    assert _make_entity(_make_channel(is_on=True)).is_on is True


def test_is_on_false_when_channel_is_off() -> None:
    assert _make_entity(_make_channel(is_on=False)).is_on is False


def test_is_on_none_when_channel_has_no_value() -> None:
    assert _make_entity(_make_channel(is_on=None)).is_on is None


# ---------------------------------------------------------------------------
# async_turn_on / async_turn_off
# ---------------------------------------------------------------------------

async def test_async_turn_on_calls_channel_update_with_true() -> None:
    channel = _make_channel()
    channel.update_value = AsyncMock()
    entity = _make_entity(channel)
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_on()

    channel.update_value.assert_awaited_once_with(True)


async def test_async_turn_off_calls_channel_update_with_false() -> None:
    channel = _make_channel()
    channel.update_value = AsyncMock()
    entity = _make_entity(channel)
    entity.async_write_ha_state = MagicMock()

    await entity.async_turn_off()

    channel.update_value.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# Callback wiring
# ---------------------------------------------------------------------------

async def test_async_added_registers_callback() -> None:
    channel = _make_channel()
    entity = _make_entity(channel)

    with patch.object(SwitchEntity, "async_added_to_hass", new=AsyncMock()):
        await entity.async_added_to_hass()

    channel.register_callback.assert_called_once_with(
        entity.async_schedule_update_ha_state)


async def test_async_will_remove_unregisters_callback() -> None:
    channel = _make_channel()
    entity = _make_entity(channel)

    with patch.object(SwitchEntity, "async_will_remove_from_hass", new=AsyncMock()):
        await entity.async_will_remove_from_hass()

    channel.unregister_callback.assert_called_once()


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def test_async_setup_entry_creates_switch_entities(hass: HomeAssistant) -> None:
    mock_channel = MagicMock()
    mock_channel.name = "_PropertyEnabledCharging"
    mock_channel.unique_id.return_value = "host/edge-1/comp/_PropertyEnabledCharging"
    mock_channel.orig_json = {}

    mock_component = MagicMock()
    mock_component.name = "ctrlEvcs0"
    mock_component.alias = "EV Charger"
    mock_component.edge.hostname = "host"
    mock_component.create_entities = True
    mock_component.boolean_properties = [mock_channel]
    mock_channel.component = mock_component

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = {"ctrlEvcs0": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.add_component_callbacks = {}

    created: list[OpenEMSSwitchEntity] = []
    with patch("custom_components.openems.switch.dr.async_get", return_value=MagicMock()):
        await switch.async_setup_entry(hass, mock_entry, created.extend)

    assert len(created) == 1
    assert isinstance(created[0], OpenEMSSwitchEntity)


async def test_async_setup_entry_skips_disabled_component(hass: HomeAssistant) -> None:
    mock_component = MagicMock()
    mock_component.name = "ignored"
    mock_component.create_entities = False

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = {"ignored": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.add_component_callbacks = {}

    created: list = []
    with patch("custom_components.openems.switch.dr.async_get", return_value=MagicMock()):
        await switch.async_setup_entry(hass, mock_entry, created.extend)

    assert created == []


async def test_async_setup_entry_registers_add_component_callback(hass: HomeAssistant) -> None:
    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = {}

    callbacks: dict = {}
    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.add_component_callbacks = callbacks

    with patch("custom_components.openems.switch.dr.async_get", return_value=MagicMock()):
        await switch.async_setup_entry(hass, mock_entry, MagicMock())

    assert "switch" in callbacks
    assert callable(callbacks["switch"])
