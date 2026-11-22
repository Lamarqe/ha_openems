"""Tests for the OpenEMS select platform."""

from datetime import timedelta
import logging
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.openems import select as select_module
from custom_components.openems.const import DOMAIN
from custom_components.openems.select import (
    OpenEMSSelectDescription,
    OpenEMSSelectEntity,
)

from openems_client.model import ChannelKind
from openems_client.openems import OpenEMSEnumProperty
import pytest

from .helpers_component import (
    make_channel,
    make_edge,
    make_entry,
    setup_evcs_component,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel() -> OpenEMSEnumProperty:
    """Build the real charge mode select channel of an EVCS controller."""
    edge = make_edge({"ctrlEvcs0": [make_channel("_PropertyChargeMode", "STRING")]})
    return edge.components["ctrlEvcs0"].channels_of(
        ChannelKind.SELECT, OpenEMSEnumProperty
    )[0]


def _make_entity(channel: MagicMock | OpenEMSEnumProperty) -> OpenEMSSelectEntity:
    desc = OpenEMSSelectDescription(
        key="test/key",
        name="ChargeMode",
        options=["force_charge", "excess_power"],
    )
    device_info = DeviceInfo(name="dev", identifiers={("openems", "dev")})
    return OpenEMSSelectEntity(channel, desc, device_info)


# ---------------------------------------------------------------------------
# current_option
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="no_value"),
        pytest.param("EXCESS_POWER", "excess_power", id="option_key"),
    ],
)
def test_current_option(value: str | None, expected: str | None) -> None:
    """The current option is the option key of the backend value."""
    channel = _make_channel()
    channel.handle_data_update("ctrlEvcs0/_PropertyChargeMode", value)
    assert _make_entity(channel).current_option == expected


# ---------------------------------------------------------------------------
# async_select_option
# ---------------------------------------------------------------------------


async def test_async_select_option_writes_option_key() -> None:
    """The selected option key is written, the channel converts it for the backend."""
    channel = MagicMock()
    channel.update_value = AsyncMock()
    entity = _make_entity(channel)
    entity.async_write_ha_state = MagicMock()

    await entity.async_select_option("excess_power")

    channel.update_value.assert_awaited_once_with("excess_power")
    entity.async_write_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_creates_select_entities(hass: HomeAssistant) -> None:
    """Enum properties become config select entities with their option keys."""
    edge = make_edge({"ctrlEvcs0": [make_channel("_PropertyChargeMode", "STRING")]})

    created: list[OpenEMSSelectEntity] = []
    await select_module.async_setup_entry(hass, make_entry(edge), created.extend)

    assert len(created) == 1
    description = created[0].entity_description
    assert description.options == ["force_charge", "excess_power"]
    assert description.translation_key == "charge_mode"
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
    await select_module.async_setup_entry(hass, mock_entry, created.extend)

    assert created == []


async def test_entity_name_from_translation(hass: HomeAssistant) -> None:
    """Channels with a translated name use it instead of the channel id."""
    loaded_component = setup_evcs_component(hass)
    loaded_component.entry.runtime_data.enabled_components = [
        loaded_component.component
    ]
    loaded_component.entry.runtime_data.edge_device.entry = loaded_component.edge_device
    try:
        platform = EntityPlatform(
            hass=hass,
            logger=logging.getLogger(__name__),
            domain="select",
            platform_name=DOMAIN,
            platform=None,
            scan_interval=timedelta(seconds=30),
            entity_namespace=None,
        )
        platform.config_entry = loaded_component.entry
        await platform.platform_data.async_load_translations()
        created: list[OpenEMSSelectEntity] = []
        await select_module.async_setup_entry(
            hass, loaded_component.entry, created.extend
        )
        await platform.async_add_entities(created)

        state = hass.states.get(created[0].entity_id)
        assert state is not None
        assert state.name == "h1 ctrlEvcs0 Charge mode"
    finally:
        loaded_component.stop()
