#!/usr/bin/env python3
"""Update OpenEMS sensor translations from a config entry."""

import json
import re
from pathlib import Path

CONFIG_ENTRIES_PATH = Path("/workspaces/ha_core/config/.storage/core.config_entries")
STRINGS_PATH = Path("/workspaces/ha_core/homeassistant/components/openems/strings.json")
SLASH_ESC = "_s_l_a_s_h_"
SNAKE_REPLACE_PATTERN = re.compile(r"[^-a-zA-Z0-9]")


def to_snake_case(name: str) -> str:
    """Convert a name to the format used by the OpenEMS integration."""
    return SNAKE_REPLACE_PATTERN.sub("_", name).lower()


def translation_key(component_name: str, channel_name: str) -> str:
    """Generate the translation key used by the OpenEMS integration."""
    component_name = re.sub(r"\d+$", "", component_name)
    channel_name = channel_name.removeprefix("_Property")
    return to_snake_case(component_name) + SLASH_ESC + to_snake_case(channel_name)


def main() -> None:
    """Update sensor translations for every OpenEMS channel with options."""
    storage = json.loads(CONFIG_ENTRIES_PATH.read_text(encoding="utf-8"))
    openems_entry = next(
        entry
        for entry in storage["data"]["entries"]
        if entry.get("domain") == "openems"
    )
    strings = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))
    sensors = strings.setdefault("entity", {}).setdefault("sensor", {})

    for component_name, component in openems_entry["data"]["components"].items():
        for channel in component["channels"]:
            if not isinstance(channel.get("options"), dict):
                continue

            state = {
                to_snake_case(option_name): option_name
                for option_name in channel["options"]
            }
            key = translation_key(component_name, channel["id"])
            sensors.setdefault(key, {})["state"] = state

    STRINGS_PATH.write_text(
        json.dumps(strings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
