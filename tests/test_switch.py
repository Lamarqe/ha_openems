"""Tests for the OpenEMS switch platform."""

from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.openems import switch
from custom_components.openems.switch import (
    OpenEMSSwitchDescription,
    OpenEMSSwitchEntity,
)

from .helpers_component import make_channel, make_edge, make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel(is_on=None):
    ch = MagicMock()
    ch.is_on = is_on
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
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_creates_switch_entities(hass: HomeAssistant) -> None:
    """Boolean properties become config switch entities."""
    edge = make_edge(
        {"ctrlEvcs0": [make_channel("_PropertyEnabledCharging", "BOOLEAN")]}
    )

    created: list[OpenEMSSwitchEntity] = []
    await switch.async_setup_entry(hass, make_entry(edge), created.extend)

    assert len(created) == 1
    description = created[0].entity_description
    assert description.translation_key == "enabled_charging"
    assert description.entity_category == EntityCategory.CONFIG


async def test_async_setup_entry_skips_disabled_component(hass: HomeAssistant) -> None:
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
    await switch.async_setup_entry(hass, mock_entry, created.extend)

    assert created == []
