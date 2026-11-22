"""Tests for writing write-only channels: action, device action and migration."""

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from openems_client import openems
from openems_client.exceptions import OpenEMSError
import probatio
import pytest
from yarl import URL

from custom_components.openems import async_migrate_entry, device_action
from custom_components.openems.const import DOMAIN, OpenEMSService
from custom_components.openems.services import async_setup_services
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

HOSTNAME = "fems1"
WRITE_ONLY = {"accessMode": "WO", "unit": ""}


@dataclass
class LoadedEdge:
    """A loaded config entry with the devices of an edge."""

    entry: MockConfigEntry
    edge: openems.OpenEMSEdge
    ess_device: dr.DeviceEntry
    meter_device: dr.DeviceEntry


def _make_edge(rest_mode: str | None) -> openems.OpenEMSEdge:
    backend = MagicMock()
    backend.multi_edge = False
    backend.connection.conn_url = URL("ws://192.0.2.1:8085/")
    config: dict[str, Any] = {
        "_host": {"Hostname": HOSTNAME},
        "ess0": {
            "properties": {},
            "channels": [
                {"id": "SetActivePowerEquals", "type": "INTEGER", **WRITE_ONLY},
                {"id": "SetReactivePowerEquals", "type": "INTEGER", **WRITE_ONLY},
                {"id": "Soc", "type": "INTEGER", "unit": "%", "accessMode": "RO"},
            ],
        },
        "meter0": {
            "properties": {},
            "channels": [
                {"id": "ActivePower", "type": "INTEGER", "unit": "W", "accessMode": "RO"}
            ],
        },
    }
    if rest_mode:
        config["ctrlApiRest0"] = {
            "factoryId": f"Controller.Api.Rest.{rest_mode}",
            "properties": {},
        }
    edge = openems.OpenEMSEdge(backend, "0", config)
    backend.the_edge = edge
    return edge


def _load_edge(hass: HomeAssistant, rest_mode: str | None) -> LoadedEdge:
    edge = _make_edge(rest_mode)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = MagicMock(backend=edge.backend)
    device_registry = dr.async_get(hass)
    edge_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, HOSTNAME)}
    )
    devices = {
        name: device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{HOSTNAME} {name}")},
            via_device_id=edge_device.id,
        )
        for name in ("ess0", "meter0")
    }
    async_setup_services(hass)
    return LoadedEdge(entry, edge, devices["ess0"], devices["meter0"])


@pytest.fixture(name="loaded")
def loaded_fixture(hass: HomeAssistant) -> Generator[LoadedEdge]:
    """Provide an edge with a REST app with write access."""
    loaded_edge = _load_edge(hass, "ReadWrite")
    yield loaded_edge
    loaded_edge.entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def _write(hass: HomeAssistant, **data: Any) -> None:
    await hass.services.async_call(
        DOMAIN, OpenEMSService.WRITE_CHANNEL, data, blocking=True
    )


async def test_write_channel(hass: HomeAssistant, loaded: LoadedEdge) -> None:
    """The value is written with the given update cycle and timeout."""
    with patch.object(
        openems.OpenEMSChannel, "write_via_rest", AsyncMock()
    ) as write_via_rest:
        await _write(
            hass,
            device_id=loaded.ess_device.id,
            channel="SetActivePowerEquals",
            value="-2000",
            update_cycle=30,
            timeout=600,
        )

    write_via_rest.assert_awaited_once_with("-2000", 30, 600)


async def test_write_channel_defaults(hass: HomeAssistant, loaded: LoadedEdge) -> None:
    """Without update cycle and timeout, the value is written once."""
    with patch.object(
        openems.OpenEMSChannel, "write_via_rest", AsyncMock()
    ) as write_via_rest:
        await _write(
            hass,
            device_id=loaded.ess_device.id,
            channel="SetActivePowerEquals",
            value=1500,
        )

    write_via_rest.assert_awaited_once_with(1500, 0, 0)


@pytest.mark.parametrize(
    ("device", "channel", "translation_key", "placeholders"),
    [
        pytest.param(
            "ess",
            "Soc",
            "unknown_write_only_channel",
            {
                "component": "ess0",
                "channel": "Soc",
                "valid_channels": "SetActivePowerEquals, SetReactivePowerEquals",
            },
            id="readable_channel",
        ),
        pytest.param(
            "meter",
            "SetActivePowerEquals",
            "unknown_write_only_channel",
            {
                "component": "meter0",
                "channel": "SetActivePowerEquals",
                "valid_channels": "-",
            },
            id="component_without_write_only_channels",
        ),
    ],
)
async def test_write_channel_rejects_other_channels(
    hass: HomeAssistant,
    loaded: LoadedEdge,
    device: str,
    channel: str,
    translation_key: str,
    placeholders: dict[str, str],
) -> None:
    """Only write-only channels of the component are written."""
    device_id = getattr(loaded, f"{device}_device").id

    with pytest.raises(ServiceValidationError) as exc_info:
        await _write(hass, device_id=device_id, channel=channel, value=1)

    assert exc_info.value.translation_key == translation_key
    assert exc_info.value.translation_placeholders == placeholders


async def test_write_channel_invalid_value(
    hass: HomeAssistant, loaded: LoadedEdge
) -> None:
    """A value which does not fit the channel is rejected before writing."""
    post = loaded.edge.backend.connection.session.post

    with pytest.raises(ServiceValidationError) as exc_info:
        await _write(
            hass,
            device_id=loaded.ess_device.id,
            channel="SetActivePowerEquals",
            value="much",
        )

    assert exc_info.value.translation_key == "invalid_channel_value"
    assert exc_info.value.translation_placeholders == {
        "component": "ess0",
        "channel": "SetActivePowerEquals",
        "value": "much",
    }
    post.assert_not_called()


async def test_write_channel_error(hass: HomeAssistant, loaded: LoadedEdge) -> None:
    """Errors of the edge raise a translated error."""
    with (
        patch.object(
            openems.OpenEMSChannel,
            "write_via_rest",
            AsyncMock(side_effect=OpenEMSError("HTTP 401")),
        ),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await _write(
            hass,
            device_id=loaded.ess_device.id,
            channel="SetActivePowerEquals",
            value=1,
        )

    assert exc_info.value.translation_key == "write_channel_failed"
    assert exc_info.value.translation_placeholders == {
        "component": "ess0",
        "channel": "SetActivePowerEquals",
    }


async def test_write_channel_without_rest_write_access(hass: HomeAssistant) -> None:
    """Edges without a REST app with write access are reported."""
    loaded = _load_edge(hass, "ReadOnly")
    try:
        with pytest.raises(ServiceValidationError) as exc_info:
            await _write(
                hass,
                device_id=loaded.ess_device.id,
                channel="SetActivePowerEquals",
                value=1,
            )
    finally:
        loaded.entry.mock_state(hass, ConfigEntryState.NOT_LOADED)

    assert exc_info.value.translation_key == "rest_write_not_available"
    assert exc_info.value.translation_placeholders == {"edge": HOSTNAME}


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"channel": "SetActivePowerEquals", "value": 1}, id="no_device"),
        pytest.param({"device_id": "abc", "value": 1}, id="no_channel"),
        pytest.param(
            {"device_id": "abc", "channel": "SetActivePowerEquals"}, id="no_value"
        ),
        pytest.param(
            {
                "device_id": "abc",
                "channel": "SetActivePowerEquals",
                "value": 1,
                "update_cycle": -1,
            },
            id="negative_update_cycle",
        ),
    ],
)
async def test_write_channel_schema(
    hass: HomeAssistant, loaded: LoadedEdge, data: dict[str, Any]
) -> None:
    """Device, channel and value are required, update cycle and timeout are positive."""
    with pytest.raises(probatio.Invalid):
        await _write(hass, **data)


@pytest.mark.parametrize(
    ("rest_mode", "device", "expected"),
    [
        pytest.param("ReadWrite", "ess", ["write_channel"], id="write_only_channels"),
        pytest.param("ReadWrite", "meter", [], id="no_write_only_channels"),
        pytest.param("ReadOnly", "ess", [], id="no_rest_write_access"),
    ],
)
async def test_device_action_offered(
    hass: HomeAssistant, rest_mode: str, device: str, expected: list[str]
) -> None:
    """Writing a channel is offered for components with write-only channels."""
    loaded = _load_edge(hass, rest_mode)
    try:
        device_id = getattr(loaded, f"{device}_device").id
        actions = await device_action.async_get_actions(hass, device_id)
    finally:
        loaded.entry.mock_state(hass, ConfigEntryState.NOT_LOADED)

    assert [action["type"] for action in actions] == expected


async def test_device_action_capabilities(
    hass: HomeAssistant, loaded: LoadedEdge
) -> None:
    """The write-only channels of the component are offered in a dropdown."""
    capabilities = await device_action.async_get_action_capabilities(
        hass,
        {
            "domain": DOMAIN,
            "device_id": loaded.ess_device.id,
            "type": device_action.ACTION_TYPE_WRITE_CHANNEL,
        },
    )

    schema = capabilities["extra_fields"].schema
    assert [str(key) for key in schema] == [
        "channel",
        "value",
        "update_cycle",
        "timeout",
    ]
    channel_selector = next(v for k, v in schema.items() if str(k) == "channel")
    assert channel_selector.config["options"] == [
        "SetActivePowerEquals",
        "SetReactivePowerEquals",
    ]


async def test_device_action_writes_channel(
    hass: HomeAssistant, loaded: LoadedEdge
) -> None:
    """The device action calls the write_channel action."""
    config = device_action.ACTION_SCHEMA(
        {
            "domain": DOMAIN,
            "device_id": loaded.ess_device.id,
            "type": device_action.ACTION_TYPE_WRITE_CHANNEL,
            "channel": "SetActivePowerEquals",
            "value": "-2000",
            "update_cycle": 30,
        }
    )

    with patch.object(
        openems.OpenEMSChannel, "write_via_rest", AsyncMock()
    ) as write_via_rest:
        await device_action.async_call_action_from_config(hass, config, {}, None)

    write_via_rest.assert_awaited_once_with("-2000", 30, 0)


async def test_migrate_v6_3_removes_write_only_sensors(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The sensor entities of write-only channels are removed."""
    entry = MockConfigEntry(domain=DOMAIN, version=6, minor_version=3, data={})
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {
            "ess0": {
                "channels": [
                    {"id": "SetActivePowerEquals", "type": "INTEGER", **WRITE_ONLY},
                    {"id": "Soc", "type": "INTEGER", "accessMode": "RO"},
                ]
            }
        },
    }
    entity_registry = er.async_get(hass)
    for unique_id in ("fems1/0/ess0/SetActivePowerEquals", "fems1/0/ess0/Soc"):
        entity_registry.async_get_or_create(
            Platform.SENSOR, DOMAIN, unique_id, config_entry=entry
        )

    assert await async_migrate_entry(hass, entry)

    assert (entry.version, entry.minor_version) == (6, 4)
    assert not entity_registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, "fems1/0/ess0/SetActivePowerEquals"
    )
    assert entity_registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, "fems1/0/ess0/Soc"
    )
