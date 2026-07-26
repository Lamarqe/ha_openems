"""Tests for the OpenEMS number platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.openems import number
from custom_components.openems.number import (OpenEMSNumberDescription,
                                              OpenEMSNumberEntity)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_channel(native_value=None, lower=0.0, upper=1000.0, step=1.0):
    ch = MagicMock()
    ch.native_value = native_value
    ch.lower_limit = lower
    ch.upper_limit = upper
    ch.step = step
    ch.orig_json = {}
    return ch


def _make_entity(channel=None) -> OpenEMSNumberEntity:
    if channel is None:
        channel = _make_channel()
    desc = OpenEMSNumberDescription(key="test/key", name="TestProp")
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    return OpenEMSNumberEntity(channel, desc, device_info)


# ---------------------------------------------------------------------------
# Property proxies
# ---------------------------------------------------------------------------

def test_native_value_proxied_from_channel() -> None:
    """native_value returns whatever the channel reports."""
    assert _make_entity(_make_channel(native_value=42.0)).native_value == 42.0


def test_native_value_none_when_channel_has_no_value() -> None:
    assert _make_entity(_make_channel(native_value=None)).native_value is None


def test_native_min_value_proxied_from_channel() -> None:
    assert _make_entity(_make_channel(lower=10.0)).native_min_value == 10.0


def test_native_max_value_proxied_from_channel() -> None:
    assert _make_entity(_make_channel(upper=500.0)).native_max_value == 500.0


def test_native_step_proxied_from_channel() -> None:
    assert _make_entity(_make_channel(step=5.0)).native_step == 5.0


# ---------------------------------------------------------------------------
# async_set_native_value
# ---------------------------------------------------------------------------

async def test_async_set_native_value_calls_channel_update() -> None:
    """async_set_native_value must forward the new value to the channel."""
    channel = _make_channel()
    channel.update_value = AsyncMock()
    entity = _make_entity(channel)
    entity.async_write_ha_state = MagicMock()

    await entity.async_set_native_value(123.0)

    channel.update_value.assert_awaited_once_with(123.0)


# ---------------------------------------------------------------------------
# Callback wiring
# ---------------------------------------------------------------------------

async def test_async_added_registers_callback() -> None:
    channel = _make_channel()
    entity = _make_entity(channel)

    with patch.object(NumberEntity, "async_added_to_hass", new=AsyncMock()):
        await entity.async_added_to_hass()

    channel.register_callback.assert_called_once_with(
        entity.async_schedule_update_ha_state)


async def test_async_will_remove_unregisters_callback() -> None:
    channel = _make_channel()
    entity = _make_entity(channel)

    with patch.object(NumberEntity, "async_will_remove_from_hass", new=AsyncMock()):
        await entity.async_will_remove_from_hass()

    channel.unregister_callback.assert_called_once()


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def test_async_setup_entry_creates_number_entities(hass: HomeAssistant) -> None:
    mock_channel = MagicMock()
    mock_channel.name = "_PropertyEnergyLimit"
    mock_channel.unit = "W"
    mock_channel.unique_id.return_value = "host/edge-1/comp/EnergyLimit"
    mock_channel.orig_json = {}

    mock_component = MagicMock()
    mock_component.name = "ctrlEvcs0"
    mock_component.alias = "EV Charger"
    mock_component.edge.hostname = "host"
    mock_component.create_entities = True
    mock_component.number_properties = [mock_channel]
    mock_channel.component = mock_component

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = {"ctrlEvcs0": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.add_component_callbacks = {}

    created: list[OpenEMSNumberEntity] = []
    with patch("custom_components.openems.number.dr.async_get", return_value=MagicMock()):
        await number.async_setup_entry(hass, mock_entry, created.extend)

    assert len(created) == 1
    assert isinstance(created[0], OpenEMSNumberEntity)


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
    with patch("custom_components.openems.number.dr.async_get", return_value=MagicMock()):
        await number.async_setup_entry(hass, mock_entry, created.extend)

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

    with patch("custom_components.openems.number.dr.async_get", return_value=MagicMock()):
        await number.async_setup_entry(hass, mock_entry, MagicMock())

    assert "number" in callbacks
    assert callable(callbacks["number"])
