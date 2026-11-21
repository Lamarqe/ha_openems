"""Tests that OpenEMS channel options match the integration translations."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from openems_client import openems
from openems_client.openems import CONFIG
from yarl import URL

from openems_client.model import ChannelKind


def _make_backend():
    """Return a minimal mock backend for OpenEMSEdge construction."""
    backend = MagicMock()
    backend.multi_edge = False
    backend.connection.conn_url = URL("ws://localhost:8085/openems-backend-ui")
    backend.connection.notify_data_received = MagicMock()
    return backend


def make_channel_json(id_, type_, unit="unit", accessMode="RO"):
    """Create a simple channel JSON dict for tests."""
    return {"id": id_, "type": type_, "unit": unit, "accessMode": accessMode}


async def test_heat_pump_sg_ready_translations() -> None:
    """SG-Ready heat pump select and status options have translations (issue #61)."""
    component_config = {
        "_host": {"Hostname": "h1"},
        "ctrlIoHeatPump0": {
            "_PropertyAlias": "Heat pump",
            "properties": {},
            # enum properties are reported by OpenEMS as STRING without options
            "channels": [
                make_channel_json("_PropertyMode", "STRING", ""),
                make_channel_json("_PropertyManualState", "STRING", ""),
                make_channel_json("_PropertyAutomaticForceOnSoc", "INTEGER", ""),
                make_channel_json("_PropertyAutomaticLockSoc", "INTEGER", ""),
                make_channel_json("_PropertyAutomaticLockCtrlEnabled", "BOOLEAN", ""),
                make_channel_json("_PropertyAutomaticLockGridBuyPower", "INTEGER", ""),
                make_channel_json("_PropertyMinimumSwitchingTime", "INTEGER", ""),
                {
                    "id": "Status",
                    "type": "INTEGER",
                    "category": "ENUM",
                    "unit": "",
                    "options": {
                        "Undefined": -1,
                        "Blocks everything till an internal maximum time of default two hours": 0,
                        "Default energy-efficient operation": 1,
                        "Recommendation to use more available power": 2,
                        "Force all possible consumption of the heat pump": 3,
                    },
                },
            ],
        },
    }
    edge = openems.OpenEMSEdge(_make_backend(), "edge-1", component_config)
    try:
        comp = edge.components["ctrlIoHeatPump0"]
        selects = {
            p.name: p
            for p in comp.channels_of(ChannelKind.SELECT, openems.OpenEMSEnumProperty)
        }
        status = next(
            c for c in comp.channels_of(ChannelKind.ENUM_SENSOR) if c.name == "Status"
        )

        strings = json.loads(
            (
                Path(__file__).parents[1] / "custom_components/openems/strings.json"
            ).read_text(encoding="utf-8")
        )
        for prop in selects.values():
            states = strings["entity"]["select"][prop.key]["state"]
            # keys are shared by all components, so they may contain further states
            assert set(prop.option_keys) <= set(states)
        status_states = strings["entity"]["sensor"][status.key]["state"]
        assert set(status.option_keys) <= set(status_states)
    finally:
        await edge.stop()


def test_heat_pump_sg_ready_defaults() -> None:
    """SG-Ready heat pump component and entities are primary, so enabled by default."""
    for name in (
        "_PropertyMode",
        "_PropertyManualState",
        "_PropertyAutomaticForceOnSoc",
        "_PropertyAutomaticLockSoc",
        "_PropertyAutomaticLockCtrlEnabled",
        "_PropertyAutomaticLockGridBuyPower",
        "_PropertyMinimumSwitchingTime",
        "Status",
    ):
        assert CONFIG.is_primary_channel("ctrlIoHeatPump0", name), name
    assert CONFIG.has_primary_channels("ctrlIoHeatPump0")
    assert not CONFIG.has_primary_channels("ctrlDebugLog0")
