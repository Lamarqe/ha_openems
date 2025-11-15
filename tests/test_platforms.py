"""Platform-level tests for OpenEMS entity wrappers."""

import pytest

from homeassistant.components.openems import openems
from homeassistant.core import HomeAssistant


@pytest.mark.asyncio
async def test_entity_lifecycle_and_unique_id(hass: HomeAssistant, dummy_backend, dummy_edge) -> None:
    """Test entities are prepared and unique_ids are stable."""
    edge = openems.OpenEMSEdge(dummy_backend, "edge1")
    # component with different channel types
    comp = {
        "_PropertyAlias": "a",
        "properties": {},
        "channels": [
            {"id": "ch1", "type": "INTEGER", "unit": "u"},
            {"id": "ch2", "type": "DOUBLE", "unit": "kWh"},
        ],
    }
    edge.set_component_config({"_host": {"Hostname": "edge1"}, "c1": comp})
    await edge.prepare_entities()
    # ensure sensors created
    comp_obj = edge.components["c1"]
    assert len(comp_obj.sensors) >= 1
    # check unique ids
    for s in comp_obj.sensors:
        assert s.unique_id().startswith("edge1")
