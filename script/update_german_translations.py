#!/usr/bin/env python3
"""Add missing English translation keys to the German translation file."""

import json
from pathlib import Path

EN_TRANSLATIONS_PATH = Path(
    "/workspaces/ha_core/homeassistant/components/openems/translations/en.json"
)
DE_TRANSLATIONS_PATH = Path(
    "/workspaces/ha_core/homeassistant/components/openems/translations/de.json"
)


def add_missing_values(english: dict, german: dict) -> None:
    """Add values that are missing from the German translation mapping."""
    for key, english_value in english.items():
        if key not in german:
            german[key] = english_value
        elif isinstance(english_value, dict) and isinstance(german[key], dict):
            add_missing_values(english_value, german[key])


def strip_translation_values(value: object) -> object:
    """Remove invalid leading or trailing whitespace from translation values."""
    if isinstance(value, dict):
        return {key: strip_translation_values(item) for key, item in value.items()}
    if isinstance(value, str):
        return value.strip()
    return value


def main() -> None:
    """Update the German translations without replacing existing values."""
    english = json.loads(EN_TRANSLATIONS_PATH.read_text(encoding="utf-8"))
    german = json.loads(DE_TRANSLATIONS_PATH.read_text(encoding="utf-8"))
    add_missing_values(english, german)
    german = strip_translation_values(german)
    DE_TRANSLATIONS_PATH.write_text(
        json.dumps(german, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
