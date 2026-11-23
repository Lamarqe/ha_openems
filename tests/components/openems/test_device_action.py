"""Tests for the OpenEMS device actions."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.openems.const import DOMAIN
from homeassistant.components import automation
from homeassistant.components.device_automation import (
    DeviceAutomationType,
    async_get_device_automations,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant.typing import WebSocketGenerator

from .conftest import HOST, HOSTNAME


@pytest.fixture
def edge_config(edge_config: dict[str, Any]) -> dict[str, Any]:
    """Return the edge configuration with a REST app with write access."""
    edge_config["ctrlApiRest0"]["factoryId"] = "Controller.Api.Rest.ReadWrite"
    return edge_config


def _device_id(hass: HomeAssistant, entry: MockConfigEntry, name: str) -> str:
    identifier = f"{HOSTNAME} {name}" if name else HOSTNAME
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )
    assert device
    return device.id


async def _run_action(hass: HomeAssistant, action: dict[str, Any]) -> None:
    """Run the device action in an automation."""
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: {
                "trigger": {"platform": "event", "event_type": "test_event"},
                "action": {"domain": DOMAIN, **action},
            }
        },
    )
    hass.bus.async_fire("test_event")
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("device", "types"),
    [
        pytest.param("ctrlEvcs0", ["update_component_config"], id="properties"),
        pytest.param("ess0", ["write_channel"], id="write_only_channels"),
        pytest.param("meter0", [], id="neither"),
        pytest.param("", [], id="edge"),
    ],
)
async def test_get_actions(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device: str,
    types: list[str],
) -> None:
    """Components offer the actions matching their channels."""
    device_id = _device_id(hass, init_integration, device)

    actions = await async_get_device_automations(
        hass, DeviceAutomationType.ACTION, [device_id]
    )

    assert [
        action["type"] for action in actions[device_id] if action["domain"] == DOMAIN
    ] == types


@pytest.mark.parametrize(
    ("device", "action_type"),
    [
        pytest.param(
            "ctrlEvcs0", "update_component_config", id="update_component_config"
        ),
        pytest.param(
            "ctrlGridOptimizedCharge0", "update_component_config", id="time_property"
        ),
        pytest.param("ess0", "write_channel", id="write_channel"),
    ],
)
async def test_action_capabilities(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
    device: str,
    action_type: str,
) -> None:
    """The fields of the actions match the properties or write-only channels."""
    assert await async_setup_component(hass, "device_automation", {})
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "device_automation/action/capabilities",
            "action": {
                "domain": DOMAIN,
                "device_id": _device_id(hass, init_integration, device),
                "type": action_type,
            },
        }
    )

    assert (await client.receive_json())["result"] == snapshot


async def test_update_component_config_action(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """The filled in fields are sent in one request."""
    await _run_action(
        hass,
        {
            "device_id": _device_id(hass, init_integration, "ctrlEvcs0"),
            "type": "update_component_config",
            "enabledCharging": True,
            "chargeMode": "force_charge",
        },
    )

    assert mock_openems_client.backend.connection.rpc_server.component_updates() == [
        ("ctrlEvcs0", {"enabledCharging": True, "chargeMode": "FORCE_CHARGE"})
    ]


async def test_update_component_config_action_with_stale_field(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """A field of a property which does not exist anymore sends nothing."""
    await _run_action(
        hass,
        {
            "device_id": _device_id(hass, init_integration, "ctrlEvcs0"),
            "type": "update_component_config",
            "removedProperty": 1,
        },
    )

    assert mock_openems_client.backend.connection.rpc_server.component_updates() == []


async def test_write_channel_action(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """The device action writes the channel."""
    aioclient_mock.post(f"http://{HOST}:8084/rest/channel/ess0/SetActivePowerEquals")

    await _run_action(
        hass,
        {
            "device_id": _device_id(hass, init_integration, "ess0"),
            "type": "write_channel",
            "channel": "SetActivePowerEquals",
            "value": "-2000",
        },
    )

    assert aioclient_mock.mock_calls[0][2] == {"value": -2000}
