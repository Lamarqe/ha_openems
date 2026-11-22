"""Tests for the OpenEMS diagnostics."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
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
