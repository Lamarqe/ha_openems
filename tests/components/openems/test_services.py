"""Tests for the OpenEMS actions."""

from typing import Any
from unittest.mock import MagicMock

from aiohttp import ClientError
import probatio
import pytest

from custom_components.openems.const import DOMAIN, OpenEMSService
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from .conftest import HOST, HOSTNAME

REST_URL = f"http://{HOST}:8084/rest/channel/ess0/SetActivePowerEquals"


@pytest.fixture
def rest_mode() -> str:
    """Return the access of the REST app of the edge."""
    return "ReadWrite"


@pytest.fixture
def edge_config(edge_config: dict[str, Any], rest_mode: str) -> dict[str, Any]:
    """Return the edge configuration with the REST app of the given access."""
    edge_config["ctrlApiRest0"]["factoryId"] = f"Controller.Api.Rest.{rest_mode}"
    return edge_config


def _device_id(hass: HomeAssistant, entry: MockConfigEntry, name: str) -> str:
    identifier = f"{HOSTNAME} {name}" if name else HOSTNAME
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )
    assert device
    return device.id


async def _update(hass: HomeAssistant, data: dict[str, Any]) -> None:
    await hass.services.async_call(
        DOMAIN, OpenEMSService.UPDATE_COMPONENT_CONFIG, data, blocking=True
    )


async def _write(hass: HomeAssistant, data: dict[str, Any]) -> None:
    await hass.services.async_call(
        DOMAIN, OpenEMSService.WRITE_CHANNEL, data, blocking=True
    )


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        pytest.param(
            {"enabledCharging": True, "chargeMode": "excess_power"},
            {"enabledCharging": True, "chargeMode": "EXCESS_POWER"},
            id="config_names",
        ),
        pytest.param(
            {"_PropertyChargeMode": "EXCESS_POWER"},
            {"chargeMode": "EXCESS_POWER", "enabledCharging": True},
            id="update_group_member_added",
        ),
    ],
)
async def test_update_component_config(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_openems_client: MagicMock,
    properties: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """All properties of a component are sent in one request."""
    await _update(
        hass,
        {
            "device_id": _device_id(hass, init_integration, "ctrlEvcs0"),
            "properties": properties,
        },
    )

    assert mock_openems_client.backend.connection.rpc_server.component_updates() == [
        ("ctrlEvcs0", expected)
    ]


async def test_update_component_config_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """An update the edge does not receive raises a translated error."""
    mock_openems_client.backend.connection.rpc_server.connected = False

    with pytest.raises(HomeAssistantError) as exc_info:
        await _update(
            hass,
            {
                "device_id": _device_id(hass, init_integration, "ctrlEvcs0"),
                "properties": {"enabledCharging": True},
            },
        )

    assert exc_info.value.translation_key == "update_component_config_failed"
    assert exc_info.value.translation_placeholders == {"component": "ctrlEvcs0"}


@pytest.mark.parametrize(
    ("device", "properties", "translation_key"),
    [
        pytest.param(
            "ctrlEvcs0",
            {"chargeMode": "force_charge", "unknown": 1},
            "unknown_property",
            id="unknown_property",
        ),
        pytest.param(
            "ctrlEvcs0",
            {"chargeMode": "invalid"},
            "invalid_property_value",
            id="invalid_option",
        ),
        pytest.param(
            "ctrlEvcs0",
            {"enabledCharging": "maybe"},
            "invalid_property_value",
            id="invalid_boolean",
        ),
        pytest.param(
            "ctrlEvcs0",
            {"energySessionLimit": "abc"},
            "invalid_property_value",
            id="invalid_number",
        ),
        pytest.param(
            "", {"enabledCharging": True}, "no_component_device", id="edge_device"
        ),
    ],
)
async def test_update_component_config_invalid(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_openems_client: MagicMock,
    device: str,
    properties: dict[str, Any],
    translation_key: str,
) -> None:
    """Invalid updates are rejected before anything is sent."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _update(
            hass,
            {
                "device_id": _device_id(hass, init_integration, device),
                "properties": properties,
            },
        )

    assert exc_info.value.translation_key == translation_key
    assert mock_openems_client.backend.connection.rpc_server.component_updates() == []


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"properties": {"enabledCharging": True}}, id="no_device"),
        pytest.param({"device_id": "abc"}, id="no_properties"),
        pytest.param({"entity_id": "number.x", "value": 1}, id="legacy_form"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_update_component_config_schema(
    hass: HomeAssistant, data: dict[str, Any]
) -> None:
    """Device and properties are required, the legacy fields are rejected."""
    with pytest.raises(probatio.Invalid):
        await _update(hass, data)


@pytest.mark.parametrize(
    ("data", "value"),
    [
        pytest.param(
            {"value": "-2000", "update_cycle": 30, "timeout": 600}, -2000, id="repeated"
        ),
        pytest.param({"value": 1500}, 1500, id="once"),
    ],
)
async def test_write_channel(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    data: dict[str, Any],
    value: int,
) -> None:
    """The value of a write-only channel is written via the REST API."""
    aioclient_mock.post(REST_URL)

    await _write(
        hass,
        {
            "device_id": _device_id(hass, init_integration, "ess0"),
            "channel": "SetActivePowerEquals",
            **data,
        },
    )

    assert aioclient_mock.call_count == 1
    assert aioclient_mock.mock_calls[0][2] == {"value": value}


@pytest.mark.parametrize(
    ("device", "channel", "value", "translation_key", "placeholders"),
    [
        pytest.param(
            "ess0",
            "Soc",
            1,
            "unknown_write_only_channel",
            {
                "component": "ess0",
                "channel": "Soc",
                "valid_channels": "SetActivePowerEquals",
            },
            id="readable_channel",
        ),
        pytest.param(
            "meter0",
            "SetActivePowerEquals",
            1,
            "unknown_write_only_channel",
            {
                "component": "meter0",
                "channel": "SetActivePowerEquals",
                "valid_channels": "-",
            },
            id="no_write_only_channels",
        ),
        pytest.param(
            "ess0",
            "SetActivePowerEquals",
            "much",
            "invalid_channel_value",
            {"component": "ess0", "channel": "SetActivePowerEquals", "value": "much"},
            id="invalid_value",
        ),
    ],
)
async def test_write_channel_invalid(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    device: str,
    channel: str,
    value: Any,
    translation_key: str,
    placeholders: dict[str, str],
) -> None:
    """Only valid values of write-only channels are written."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _write(
            hass,
            {
                "device_id": _device_id(hass, init_integration, device),
                "channel": channel,
                "value": value,
            },
        )

    assert exc_info.value.translation_key == translation_key
    assert exc_info.value.translation_placeholders == placeholders
    assert aioclient_mock.call_count == 0


async def test_write_channel_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """A write which does not reach the edge raises a translated error."""
    aioclient_mock.post(REST_URL, exc=ClientError())

    with pytest.raises(HomeAssistantError) as exc_info:
        await _write(
            hass,
            {
                "device_id": _device_id(hass, init_integration, "ess0"),
                "channel": "SetActivePowerEquals",
                "value": 1,
            },
        )

    assert exc_info.value.translation_key == "write_channel_failed"
    assert exc_info.value.translation_placeholders == {
        "component": "ess0",
        "channel": "SetActivePowerEquals",
    }


@pytest.mark.parametrize("rest_mode", ["ReadOnly"])
async def test_write_channel_without_rest_write_access(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Edges without a REST app with write access are reported."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await _write(
            hass,
            {
                "device_id": _device_id(hass, init_integration, "ess0"),
                "channel": "SetActivePowerEquals",
                "value": 1,
            },
        )

    assert exc_info.value.translation_key == "rest_write_not_available"
    assert exc_info.value.translation_placeholders == {"edge": HOSTNAME}


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"channel": "SetActivePowerEquals", "value": 1}, id="no_device"),
        pytest.param({"device_id": "abc", "value": 1}, id="no_channel"),
        pytest.param(
            {"device_id": "abc", "channel": "SetActivePowerEquals"}, id="no_value"
        ),
        pytest.param(
            {
                "device_id": "abc",
                "channel": "SetActivePowerEquals",
                "value": 1,
                "update_cycle": -1,
            },
            id="negative_update_cycle",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_write_channel_schema(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Device, channel and value are required, update cycle and timeout are positive."""
    with pytest.raises(probatio.Invalid):
        await _write(hass, data)
