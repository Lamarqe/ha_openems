"""Tests for the OpenEMS number platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import UNDEFINED

from custom_components.openems import number
from custom_components.openems.number import (
    OpenEMSNumberDescription,
    OpenEMSNumberEntity,
)

from .helpers_component import HOSTNAME, make_channel, make_edge, make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel(native_value=None, lower=0.0, upper=1000.0, step=1.0):
    ch = MagicMock()
    ch.value = native_value
    ch.lower_limit = lower
    ch.upper_limit = upper
    ch.step = step
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


async def test_async_added_adds_listener_removed_with_entity() -> None:
    """The entity listens to the channel until it is removed."""
    channel = MagicMock()
    entity = _make_entity(channel)

    with patch.object(NumberEntity, "async_added_to_hass", new=AsyncMock()):
        await entity.async_added_to_hass()

    channel.add_listener.assert_called_once_with(entity.async_schedule_update_ha_state)
    remove_listener = channel.add_listener.return_value
    remove_listener.assert_not_called()
    entity._call_on_remove_callbacks()
    remove_listener.assert_called_once()


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_creates_number_entities(hass: HomeAssistant) -> None:
    """Number properties become config entities with unit and device class."""
    edge = make_edge(
        {"ctrlEvcs0": [make_channel("_PropertyEnergySessionLimit", unit="Wh")]}
    )

    created: list[OpenEMSNumberEntity] = []
    with patch(
        "custom_components.openems.number.dr.async_get", return_value=MagicMock()
    ):
        await number.async_setup_entry(hass, make_entry(edge), created.extend)

    assert len(created) == 1
    description = created[0].entity_description
    assert description.key == f"{HOSTNAME}/0/ctrlEvcs0/_PropertyEnergySessionLimit"
    assert description.entity_category == EntityCategory.CONFIG
    assert description.device_class == NumberDeviceClass.ENERGY
    assert description.native_unit_of_measurement == "Wh"
    assert description.translation_key == "energy_session_limit"
    # primary channel: translated name
    assert description.name is UNDEFINED
    assert description.entity_registry_enabled_default is True


async def test_async_setup_entry_number_mode_from_fixed_step(
    hass: HomeAssistant,
) -> None:
    """Numbers with a fixed step become input fields, others use the automatic mode."""
    mock_component = MagicMock()
    mock_component.name = "ctrlIoHeatPump0"
    mock_component.alias = "Heat pump"
    mock_component.edge.hostname = "host"
    channels = []
    for name, fixed_step in (
        ("_PropertyMinimumSwitchingTime", 1),
        ("_PropertyAutomaticLockSoc", None),
    ):
        channel = MagicMock()
        channel.name = name
        channel.fixed_step = fixed_step
        channel.unit = None
        channel.quantity = None
        channel.unique_id = f"host/edge-1/ctrlIoHeatPump0/{name}"
        channel.component = mock_component
        channels.append(channel)
    mock_component.channels_of.return_value = channels

    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry-id"
    mock_entry.runtime_data.enabled_components = [mock_component]

    created: list[OpenEMSNumberEntity] = []
    with patch(
        "custom_components.openems.number.dr.async_get", return_value=MagicMock()
    ):
        await number.async_setup_entry(hass, mock_entry, created.extend)

    modes = [entity.entity_description.mode for entity in created]
    assert modes == [NumberMode.BOX, NumberMode.AUTO]


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
    with patch(
        "custom_components.openems.number.dr.async_get", return_value=MagicMock()
    ):
        await number.async_setup_entry(hass, mock_entry, created.extend)

    assert not created
