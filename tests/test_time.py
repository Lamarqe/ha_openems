"""Tests for the OpenEMS time platform."""

from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.openems import time as time_module
from custom_components.openems.time import (OpenEMSTimeDescription,
                                            OpenEMSTimeEntity)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_channel(native_value=None):
    ch = MagicMock()
    ch.native_value = native_value
    ch.orig_json = {}
    return ch


def _make_entity(channel=None) -> OpenEMSTimeEntity:
    if channel is None:
        channel = _make_channel()
    desc = OpenEMSTimeDescription(key="test/key", name="TargetTime")
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    return OpenEMSTimeEntity(channel, desc, device_info)


# ---------------------------------------------------------------------------
# native_value
# ---------------------------------------------------------------------------

def test_native_value_none_when_channel_has_no_value() -> None:
    assert _make_entity(_make_channel(native_value=None)).native_value is None


def test_native_value_proxied_from_channel() -> None:
    t = time(8, 30)
    assert _make_entity(_make_channel(native_value=t)).native_value == t


# ---------------------------------------------------------------------------
# async_set_value
# ---------------------------------------------------------------------------

async def test_async_set_value_calls_channel() -> None:
    """async_set_value must delegate to the channel's async_set_value."""
    channel = _make_channel()
    channel.async_set_value = AsyncMock()
    entity = _make_entity(channel)
    entity.async_write_ha_state = MagicMock()

    t = time(12, 0)
    await entity.async_set_value(t)

    channel.async_set_value.assert_awaited_once_with(t)


# ---------------------------------------------------------------------------
# Callback wiring
# ---------------------------------------------------------------------------

async def test_async_added_registers_callback() -> None:
    channel = _make_channel()
    entity = _make_entity(channel)

    with patch.object(TimeEntity, "async_added_to_hass", new=AsyncMock()):
        await entity.async_added_to_hass()

    channel.register_callback.assert_called_once_with(
        entity.async_schedule_update_ha_state)


async def test_async_will_remove_unregisters_callback() -> None:
    channel = _make_channel()
    entity = _make_entity(channel)

    with patch.object(TimeEntity, "async_will_remove_from_hass", new=AsyncMock()):
        await entity.async_will_remove_from_hass()

    channel.unregister_callback.assert_called_once()


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def test_async_setup_entry_creates_time_entities(hass: HomeAssistant) -> None:
    mock_channel = MagicMock()
    mock_channel.name = "_PropertyManualTargetTime"
    mock_channel.unique_id.return_value = "host/edge-1/comp/_PropertyManualTargetTime"
    mock_channel.orig_json = {}

    mock_component = MagicMock()
    mock_component.name = "ctrlEvcs0"
    mock_component.alias = "EV Charger"
    mock_component.edge.hostname = "host"
    mock_component.create_entities = True
    mock_component.time_properties = [mock_channel]
    mock_channel.component = mock_component

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = {"ctrlEvcs0": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.add_component_callbacks = {}

    created: list[OpenEMSTimeEntity] = []
    with patch("custom_components.openems.time.dr.async_get", return_value=MagicMock()):
        await time_module.async_setup_entry(hass, mock_entry, created.extend)

    assert len(created) == 1
    assert isinstance(created[0], OpenEMSTimeEntity)


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
    with patch("custom_components.openems.time.dr.async_get", return_value=MagicMock()):
        await time_module.async_setup_entry(hass, mock_entry, created.extend)

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

    with patch("custom_components.openems.time.dr.async_get", return_value=MagicMock()):
        await time_module.async_setup_entry(hass, mock_entry, MagicMock())

    assert "time" in callbacks
    assert callable(callbacks["time"])
