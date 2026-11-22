"""Tests for the OpenEMS binary sensor platform."""

from unittest.mock import MagicMock

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.openems import binary_sensor
from custom_components.openems.binary_sensor import (
    OpenEMSBinarySensorDescription,
    OpenEMSBinarySensorEntity,
)
from openems_client.model import ChannelKind
from openems_client.openems import OpenEMSChannel
import pytest

from .helpers_component import make_channel, make_edge, make_entry

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


def _make_real_channel(value: int | None = None) -> OpenEMSChannel:
    """Build a real binary sensor channel of the edge."""
    edge = make_edge({"ctrlEvcs0": [make_channel("IsChargingEnabled", "BOOLEAN")]})
    channel = edge.components["ctrlEvcs0"].channels_of(
        ChannelKind.BINARY_SENSOR, OpenEMSChannel
    )[0]
    channel.handle_data_update("ctrlEvcs0/IsChargingEnabled", value)
    return channel


# ---------------------------------------------------------------------------
# is_on behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="no_value"),
        pytest.param(1, True, id="on"),
        pytest.param(0, False, id="off"),
    ],
)
def test_is_on(value: int | None, expected: bool | None) -> None:
    """is_on reflects the boolean value of the channel."""
    assert _make_entity(_make_real_channel(value)).is_on is expected


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_creates_binary_sensor_entities(
    hass: HomeAssistant,
) -> None:
    """Binary sensors are created, state channels with a level signal a problem."""
    edge = make_edge(
        {
            "ctrlEvcs0": [
                make_channel("IsChargingEnabled", "BOOLEAN"),
                make_channel(
                    "ConfigNotActivated", "BOOLEAN", category="STATE", level="FAULT"
                ),
            ]
        }
    )

    created: list[OpenEMSBinarySensorEntity] = []
    await binary_sensor.async_setup_entry(hass, make_entry(edge), created.extend)

    descriptions = {e._channel.name: e.entity_description for e in created}
    assert descriptions["IsChargingEnabled"].device_class is None
    assert descriptions["IsChargingEnabled"].name == "IsChargingEnabled"
    assert descriptions["IsChargingEnabled"].translation_key == "is_charging_enabled"
    assert (
        descriptions["ConfigNotActivated"].device_class
        == BinarySensorDeviceClass.PROBLEM
    )


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
    await binary_sensor.async_setup_entry(hass, mock_entry, created.extend)

    assert created == []
