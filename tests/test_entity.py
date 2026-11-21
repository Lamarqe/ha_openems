"""Tests for the base entity of the channel based OpenEMS platforms."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.exceptions import HomeAssistantError
from openems_client.exceptions import OpenEMSError
from openems_client.model import ChannelKind
from openems_client.openems import OpenEMSChannel
import pytest

from custom_components.openems.binary_sensor import (
    OpenEMSBinarySensorDescription,
    OpenEMSBinarySensorEntity,
)
from custom_components.openems.sensor import (
    OpenEMSSensorDescription,
    OpenEMSSensorEntity,
)

from .helpers_component import make_channel, make_edge

DEVICE_INFO = DeviceInfo(name="dev", identifiers={("openems", "dev")})


def _make_entity() -> tuple[OpenEMSSensorEntity, OpenEMSChannel]:
    edge = make_edge({"meter0": [make_channel("ActivePower", unit="W")]})
    sensors = edge.components["meter0"].channels_of(ChannelKind.SENSOR, OpenEMSChannel)
    channel = sensors[0]
    entity = OpenEMSSensorEntity(
        channel, OpenEMSSensorDescription(key=channel.unique_id), DEVICE_INFO
    )
    return entity, channel


async def test_available_follows_the_edge() -> None:
    """Entities are available while the edge is connected and sends channel data."""
    entity, channel = _make_entity()
    edge = channel.component.edge
    assert not entity.available

    edge.currentData({"meter0/ActivePower": 1500})
    assert entity.available

    edge.set_unavailable()
    assert not entity.available


async def test_listener_writes_state_on_availability_change() -> None:
    """The entity listens to the channel until it is removed."""
    entity, channel = _make_entity()
    entity.async_schedule_update_ha_state = MagicMock()
    with patch.object(Entity, "async_added_to_hass", new=AsyncMock()):
        await entity.async_added_to_hass()

    channel.component.edge.currentData({})
    entity.async_schedule_update_ha_state.assert_called_once()

    entity._call_on_remove_callbacks()
    channel.component.edge.set_unavailable()
    entity.async_schedule_update_ha_state.assert_called_once()


@pytest.mark.parametrize(
    ("entity_class", "description_class"),
    [
        pytest.param(OpenEMSSensorEntity, OpenEMSSensorDescription, id="sensor"),
        pytest.param(
            OpenEMSBinarySensorEntity,
            OpenEMSBinarySensorDescription,
            id="binary_sensor",
        ),
    ],
)
async def test_rest_write_errors_raise_translated_error(
    entity_class: type[OpenEMSSensorEntity | OpenEMSBinarySensorEntity],
    description_class: type[OpenEMSSensorDescription | OpenEMSBinarySensorDescription],
) -> None:
    """Errors of the REST write action raise a translated HomeAssistantError."""
    channel = MagicMock(spec=OpenEMSChannel)
    channel.write_via_rest = AsyncMock(side_effect=OpenEMSError("HTTP 401"))
    entity = entity_class(channel, description_class(key="h1/0/meter0/X"), DEVICE_INFO)
    entity.entity_id = "sensor.meter0_x"

    with pytest.raises(HomeAssistantError) as exc_info:
        await entity.update_value(value=1, update_cycle=0, timeout=0)

    assert exc_info.value.translation_key == "write_failed"
    assert exc_info.value.translation_placeholders == {"entity_id": "sensor.meter0_x"}
