"""Consistency checks of services.yaml and the translation files."""

import json
import re
from pathlib import Path

import pytest
import yaml

INTEGRATION_PATH = Path(__file__).parent.parent / "custom_components" / "openems"
TRANSLATION_FILES = ["strings.json", "translations/en.json", "translations/de.json"]


def _load_json(file_name: str) -> dict:
    return json.loads((INTEGRATION_PATH / file_name).read_text(encoding="utf-8"))


def _services_yaml() -> dict:
    return yaml.safe_load(
        (INTEGRATION_PATH / "services.yaml").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("file_name", TRANSLATION_FILES)
def test_all_services_and_fields_are_translated(file_name: str) -> None:
    """Each service and field of services.yaml has a name and description."""
    services = _load_json(file_name)["services"]
    for service_name, service_def in _services_yaml().items():
        translation = services[service_name]
        assert translation["name"]
        assert translation["description"]
        assert set(translation["fields"]) == set(service_def["fields"])
        for field in translation["fields"].values():
            assert field["name"]
            assert field["description"]


@pytest.mark.parametrize("section", ["services", "exceptions", "device_automation"])
def test_translation_files_have_same_keys(section: str) -> None:
    """All translation files provide the same keys as strings.json."""

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


def test_services_yaml_has_no_device_target() -> None:
    """Hassfest rejects device filters on targets, a device selector field must be used."""
    for service_name, service_def in _services_yaml().items():
        assert "device" not in (service_def.get("target") or {}), service_name


@pytest.mark.parametrize("file_name", TRANSLATION_FILES)
def test_exceptions_have_messages(file_name: str) -> None:
    """Hassfest requires a mapping with key "message" per exception."""
    for key, value in _load_json(file_name)["exceptions"].items():
        assert isinstance(value, dict), key
        assert value["message"], key


@pytest.mark.parametrize("file_name", TRANSLATION_FILES)
def test_placeholders_are_identifiers(file_name: str) -> None:
    """Hassfest requires each {placeholder} to be a valid identifier."""

    def strings(value: object, path: str = ""):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from strings(item, f"{path}/{key}")
        elif isinstance(value, str):
            yield path, value

    for path, text in strings(_load_json(file_name)):
        for placeholder in re.findall(r"\{([^}]*)\}", text):
            assert re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", placeholder), (
                f"{path}: {{{placeholder}}}"
            )
