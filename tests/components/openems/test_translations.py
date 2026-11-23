"""Consistency checks of the translations which hassfest does not do."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from openems_client import openems
from openems_client.model import ChannelKind, channel_key
from openems_client.openems import CONFIG
import pytest
from yarl import URL

import custom_components.openems as integration

INTEGRATION_PATH = Path(integration.__file__).parent
TRANSLATION_FILES = ["strings.json", "translations/en.json", "translations/de.json"]


def _load_json(file_name: str) -> dict[str, Any]:
    return json.loads((INTEGRATION_PATH / file_name).read_text(encoding="utf-8"))


def test_primary_channels_have_translated_names() -> None:
    """Every primary channel of the client library has a name in strings.json."""
    named_keys = {
        key
        for platform_strings in _load_json("strings.json")["entity"].values()
        for key, value in platform_strings.items()
        if "name" in value
    }
    # string property without enum or time options, no entity is created for it
    channel_names = {
        name for entry in CONFIG.primary_channels for name in entry["channels"]
    } - {"_PropertyTargetTime"}

    assert not {name for name in channel_names if channel_key(name) not in named_keys}


@pytest.mark.parametrize("section", ["services", "exceptions", "device_automation"])
def test_translation_files_have_same_keys(section: str) -> None:
    """The maintained translation files provide the same keys as strings.json."""

    def keys(value: object, prefix: str = "") -> set[str]:
        if isinstance(value, dict):
            return {k for key, v in value.items() for k in keys(v, f"{prefix}/{key}")}
        return {prefix}

    expected = keys(_load_json("strings.json")[section])
    for file_name in TRANSLATION_FILES[1:]:
        assert keys(_load_json(file_name)[section]) == expected, file_name


@pytest.mark.parametrize("file_name", TRANSLATION_FILES)
def test_select_options_are_available_for_selectors(file_name: str) -> None:
    """Device action dropdowns use the same option translations as the select entities."""
    translations = _load_json(file_name)
    for key, select in translations["entity"]["select"].items():
        assert translations["selector"][key]["options"] == select["state"], key


def test_heat_pump_sg_ready_translations() -> None:
    """SG-Ready heat pump select and status options have translations (issue #61)."""
    backend = MagicMock()
    backend.multi_edge = False
    backend.connection.conn_url = URL("ws://192.0.2.1:8085/websocket")
    channels = [
        # enum properties are reported by OpenEMS as STRING without options
        {"id": "_PropertyMode", "type": "STRING", "unit": "", "accessMode": "RO"},
        {
            "id": "_PropertyManualState",
            "type": "STRING",
            "unit": "",
            "accessMode": "RO",
        },
        {
            "id": "Status",
            "type": "INTEGER",
            "category": "ENUM",
            "unit": "",
            "accessMode": "RO",
            "options": {
                "Undefined": -1,
                "Blocks everything till an internal maximum time of default two hours": 0,
                "Default energy-efficient operation": 1,
                "Recommendation to use more available power": 2,
                "Force all possible consumption of the heat pump": 3,
            },
        },
    ]
    edge = openems.OpenEMSEdge(
        backend,
        "0",
        {
            "_host": {"Hostname": "fems12345"},
            "ctrlIoHeatPump0": {"properties": {}, "channels": channels},
        },
    )
    component = edge.components["ctrlIoHeatPump0"]
    strings = _load_json("strings.json")["entity"]

    # keys are shared by all components, so they may contain further states
    for prop in component.channels_of(ChannelKind.SELECT, openems.OpenEMSEnumProperty):
        assert set(prop.option_keys) <= set(strings["select"][prop.key]["state"])
    for channel in component.channels_of(ChannelKind.ENUM_SENSOR):
        assert set(channel.option_keys) <= set(strings["sensor"][channel.key]["state"])
