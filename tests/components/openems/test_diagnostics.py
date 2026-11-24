"""Tests for the OpenEMS diagnostics."""

from typing import Any

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant.typing import ClientSessionGenerator

SERIAL_NUMBER = "battery0/Tower0BmsSerialNumber"


@pytest.fixture
def edge_config(edge_config: dict[str, Any]) -> dict[str, Any]:
    """Add personal data and secrets, as the configuration of real edges contains."""
    edge_config["_meta"]["properties"] |= {
        "_lastChangeBy": "jane.doe@example.com: Jane Doe",
        "latitude": 52.52,
        "longitude": 13.405,
        "placeName": "Berlin",
        "postcode": "10115",
    }
    edge_config["_host"]["properties"]["networkConfiguration"] = {
        "interfaces": {"eth0": {"addresses": [{"address": "192.0.2.50"}]}}
    }
    edge_config["evcs0"]["properties"] |= {
        "ip": "192.0.2.60",
        "comment": "Installed by jane.doe@example.com",
    }
    edge_config["ctrlApiRest0"]["properties"]["apikey"] = "secret-key"
    edge_config["battery0"] = {
        "factoryId": "Battery.Fenecon.Home",
        "properties": {},
        "channels": [
            {
                "id": "Tower0BmsSerialNumber",
                "type": "STRING",
                "unit": "",
                "accessMode": "RO",
            }
        ],
    }
    return edge_config


@pytest.fixture
def channel_data(channel_data: dict[str, Any]) -> dict[str, Any]:
    """Add the value of a serial number channel."""
    return {**channel_data, SERIAL_NUMBER: "519100001009210110000001"}


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Personal data, secrets, serial numbers and the hostname are redacted."""
    hass.config_entries.async_update_entry(
        init_integration,
        data={**init_integration.data, CONF_URL: "ws://user:pw@example.com/ws"},
    )

    assert (
        await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
        == snapshot
    )
