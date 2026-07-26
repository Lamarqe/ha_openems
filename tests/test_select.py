"""Tests for the OpenEMS select platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.openems import select as select_module
from custom_components.openems.select import (OpenEMSSelectDescription,
                                              OpenEMSSelectEntity)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OPTIONS = ["EXCESS_POWER", "MANUAL", "OFF"]


def _make_channel(current_option=None):
    ch = MagicMock()
    ch.current_option = current_option
    ch.property_options = _OPTIONS
    ch.orig_json = {}
    return ch


def _make_entity(channel=None) -> OpenEMSSelectEntity:
    if channel is None:
        channel = _make_channel()
    desc = OpenEMSSelectDescription(
        key="test/key",
        name="ChargeMode",
        options=["excess_power", "manual", "off"],
    )
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    return OpenEMSSelectEntity(channel, desc, device_info)


# ---------------------------------------------------------------------------
# current_option
# ---------------------------------------------------------------------------

def test_current_option_none_when_channel_has_no_value() -> None:
    """Returns None when the channel has no active option."""
    assert _make_entity(_make_channel(current_option=None)
                        ).current_option is None


def test_current_option_converted_to_snake_case() -> None:
    """The raw option string is lowercased / snake_cased."""
    assert _make_entity(_make_channel(
        current_option="EXCESS_POWER")).current_option == "excess_power"


def test_current_option_already_lowercase_unchanged() -> None:
    assert _make_entity(_make_channel(
        current_option="MANUAL")).current_option == "manual"


# ---------------------------------------------------------------------------
# async_select_option
# ---------------------------------------------------------------------------

async def test_async_select_option_calls_channel_update_with_original_value() -> None:
    """Selecting a snake_case option translates back to the original option string."""
    channel = _make_channel()
    channel.update_value = AsyncMock()
    entity = _make_entity(channel)
    entity.async_write_ha_state = MagicMock()

    await entity.async_select_option("excess_power")

    channel.update_value.assert_awaited_once_with("EXCESS_POWER")


async def test_async_select_option_unknown_option_does_nothing() -> None:
    """Selecting an option not in the list must not call update_value."""
    channel = _make_channel()
    channel.update_value = AsyncMock()
    entity = _make_entity(channel)

    await entity.async_select_option("unknown_option")

    channel.update_value.assert_not_awaited()


# ---------------------------------------------------------------------------
# Callback wiring
# ---------------------------------------------------------------------------

async def test_async_added_registers_callback() -> None:
    channel = _make_channel()
    entity = _make_entity(channel)

    with patch.object(SelectEntity, "async_added_to_hass", new=AsyncMock()):
        await entity.async_added_to_hass()

    channel.register_callback.assert_called_once_with(
        entity.async_schedule_update_ha_state)


async def test_async_will_remove_unregisters_callback() -> None:
    channel = _make_channel()
    entity = _make_entity(channel)

    with patch.object(SelectEntity, "async_will_remove_from_hass", new=AsyncMock()):
        await entity.async_will_remove_from_hass()

    channel.unregister_callback.assert_called_once()


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def test_async_setup_entry_creates_select_entities(hass: HomeAssistant) -> None:
    mock_channel = MagicMock()
    mock_channel.name = "_PropertyChargeMode"
    mock_channel.property_options = _OPTIONS
    mock_channel.unique_id.return_value = "host/edge-1/comp/_PropertyChargeMode"
    mock_channel.orig_json = {}

    mock_component = MagicMock()
    mock_component.name = "ctrlEvcs0"
    mock_component.alias = "EV Charger"
    mock_component.edge.hostname = "host"
    mock_component.create_entities = True
    mock_component.enum_properties = [mock_channel]
    mock_channel.component = mock_component

    mock_backend = MagicMock()
    mock_backend.the_edge.hostname = "host"
    mock_backend.the_edge.components = {"ctrlEvcs0": mock_component}

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.backend = mock_backend
    mock_entry.runtime_data.add_component_callbacks = {}

    created: list[OpenEMSSelectEntity] = []
    with patch("custom_components.openems.select.dr.async_get", return_value=MagicMock()):
        await select_module.async_setup_entry(hass, mock_entry, created.extend)

    assert len(created) == 1
    assert isinstance(created[0], OpenEMSSelectEntity)


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
    with patch("custom_components.openems.select.dr.async_get", return_value=MagicMock()):
        await select_module.async_setup_entry(hass, mock_entry, created.extend)

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

    with patch("custom_components.openems.select.dr.async_get", return_value=MagicMock()):
        await select_module.async_setup_entry(hass, mock_entry, MagicMock())

    assert "select" in callbacks
    assert callable(callbacks["select"])
