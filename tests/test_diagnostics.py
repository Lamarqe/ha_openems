"""Tests for the OpenEMS diagnostics."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.openems.const import DOMAIN
from custom_components.openems.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_contain_cached_configuration(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Diagnostics contain the cached edge configuration and redacted entry data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.1", CONF_USERNAME: "user", CONF_PASSWORD: "secret"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock()
    entry.runtime_data.backend.the_edge.registered_channels = {}
    entry.runtime_data.backend.the_edge.current_channel_data = {}
    storage_key = f"{DOMAIN}.{entry.entry_id}"
    components = {"ess0": {"channels": [{"id": "Soc", "type": "INTEGER"}]}}
    hass_storage[storage_key] = {
        "version": 1,
        "minor_version": 1,
        "key": storage_key,
        "data": components,
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["components"] == components
    assert diagnostics["entry_data"] == {
        CONF_HOST: REDACTED,
        CONF_USERNAME: REDACTED,
        CONF_PASSWORD: REDACTED,
    }


async def test_diagnostics_map_registered_channels_to_entities(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Registered channels show the entities using them."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    for platform, unique_id, object_id in (
        ("sensor", "fems1/0/charger1/ActualEnergy", "fems1_charger1_actual_energy"),
        ("sensor", "fems1/0/ess0/Soc", "fems1_ess0_soc"),
        ("number", "fems1/0/ess0/SocLimit", "fems1_ess0_soc_limit"),
    ):
        entity_registry.async_get_or_create(
            platform,
            DOMAIN,
            unique_id,
            config_entry=entry,
            suggested_object_id=object_id,
        )
    entry.runtime_data = MagicMock()
    entry.runtime_data.backend.the_edge.registered_channels = {
        "charger1/ActualEnergy": {MagicMock(unique_id="fems1/0/charger1/ActualEnergy")},
        "ess0/Soc": {
            MagicMock(unique_id="fems1/0/ess0/Soc"),
            MagicMock(unique_id="fems1/0/ess0/SocLimit"),
        },
        "_sum/State": {MagicMock(unique_id="fems1/0/_sum/State")},
    }
    entry.runtime_data.backend.the_edge.current_channel_data = {}

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["registered_channels"] == {
        "charger1/ActualEnergy": "sensor.fems1_charger1_actual_energy",
        "ess0/Soc": ["number.fems1_ess0_soc_limit", "sensor.fems1_ess0_soc"],
        "_sum/State": None,
    }
