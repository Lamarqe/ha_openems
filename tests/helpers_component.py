"""Helpers to set up a loaded config entry with a real OpenEMS component."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.openems import openems
from custom_components.openems.const import DOMAIN

HOSTNAME = "h1"


@dataclass
class LoadedComponent:
    """References to the objects created by setup_evcs_component."""

    hass: HomeAssistant
    entry: MockConfigEntry
    edge: openems.OpenEMSEdge
    component: openems.OpenEMSComponent
    update_config: AsyncMock
    component_device: dr.DeviceEntry
    edge_device: dr.DeviceEntry

    def stop(self) -> None:
        """Stop the edge and prevent unloading of the mocked entry at teardown."""
        self.edge.stop()
        self.entry.mock_state(self.hass, ConfigEntryState.NOT_LOADED)


def setup_evcs_component(hass: HomeAssistant) -> LoadedComponent:
    """Create an edge with component ctrlEvcs0 and register entry and devices.

    The caller must call stop() at the end of the test.
    """
    backend = MagicMock()
    backend.multi_edge = False
    backend.connection.conn_url = URL("ws://localhost:8085/openems-backend-ui")
    component_config = {
        "_host": {"Hostname": HOSTNAME},
        "ctrlEvcs0": {
            "_PropertyAlias": "EVCS",
            "properties": {},
            "channels": [
                {"id": "_PropertyEnabledCharging", "type": "BOOLEAN", "unit": ""},
                {"id": "_PropertyChargeMode", "type": "STRING", "unit": ""},
                {"id": "_PropertyEnergySessionLimit", "type": "INTEGER", "unit": "Wh"},
            ],
        },
    }
    edge = openems.OpenEMSEdge(backend, "edge0", component_config)
    backend.the_edge = edge
    component = edge.components["ctrlEvcs0"]
    update_config = AsyncMock()
    component.update_config = update_config

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = MagicMock(backend=backend)

    device_registry = dr.async_get(hass)
    edge_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, HOSTNAME)}
    )
    component_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, HOSTNAME + " ctrlEvcs0")},
        via_device_id=edge_device.id,
    )
    return LoadedComponent(
        hass=hass,
        entry=entry,
        edge=edge,
        component=component,
        update_config=update_config,
        component_device=component_device,
        edge_device=edge_device,
    )
