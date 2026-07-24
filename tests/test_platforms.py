"""Platform-level tests for OpenEMS entity wrappers."""

from homeassistant.core import HomeAssistant

from custom_components.openems import openems


async def test_entity_lifecycle_and_unique_id(hass: HomeAssistant, dummy_backend) -> None:
    """Test entities are prepared and unique_ids are stable."""
    comp = {
        "_PropertyAlias": "a",
        "properties": {},
        "channels": [
            {"id": "ch1", "type": "INTEGER", "unit": "u"},
            {"id": "ch2", "type": "DOUBLE", "unit": "kWh"},
        ],
    }
    component_config = {"_host": {"Hostname": "edge1"}, "c1": comp}
    edge = openems.OpenEMSEdge(dummy_backend, "edge1", component_config)
    try:
        comp_obj = edge.components["c1"]
        assert len(comp_obj.sensors) >= 1
        for s in comp_obj.sensors:
            assert s.unique_id().startswith("edge1")
    finally:
        edge.stop()
