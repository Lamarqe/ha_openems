"""Tests for the setup, unload, migration and devices of the OpenEMS integration."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from openems_client.edge_config import EdgeConfigChange
from openems_client.exceptions import (
    OpenEMSAuthError,
    OpenEMSConnectionError,
    OpenEMSError,
)
import pytest
from syrupy.assertion import SnapshotAssertion

from custom_components.openems.const import (
    CONF_ADVANCED_OPTIONS,
    CONF_EDGE,
    CONF_FORWARD_INTERVAL,
    CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import WebSocketGenerator

from .conftest import (
    ENTRY_DATA,
    ENTRY_OPTIONS,
    HOSTNAME,
    setup_integration,
    store_edge_config,
)

ESS_SOC = "sensor.fems12345_ess0_soc"


def _cached(hass_storage: dict[str, Any], entry: MockConfigEntry) -> Any:
    return hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]


async def test_setup_reads_and_caches_configuration(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
    edge_config: dict[str, Any],
) -> None:
    """Without a cached configuration, setup reads it from the edge and caches it."""
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert _cached(hass_storage, mock_config_entry) == edge_config
    mock_openems_client.check_edge_config.assert_not_awaited()


@pytest.mark.parametrize(
    ("change", "backends"),
    [
        pytest.param(EdgeConfigChange(), 1, id="unchanged"),
        pytest.param(EdgeConfigChange(added=frozenset({"meter1"})), 2, id="changed"),
    ],
)
async def test_setup_checks_cached_configuration(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
    edge_config: dict[str, Any],
    change: EdgeConfigChange,
    backends: int,
) -> None:
    """A cached configuration is checked after the setup, changes reload the entry."""
    store_edge_config(hass_storage, mock_config_entry, edge_config)
    current = {**edge_config, "meter1": edge_config["meter0"]}
    mock_openems_client.check_edge_config.side_effect = None
    mock_openems_client.check_edge_config.return_value = (current, change)

    await setup_integration(hass, mock_config_entry)

    mock_openems_client.read_edge_config.assert_not_awaited()
    assert _cached(hass_storage, mock_config_entry) == current
    assert mock_openems_client.create_backend.call_count == backends
    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_setup_check_error_keeps_configuration(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
    edge_config: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed check keeps the cached configuration."""
    store_edge_config(hass_storage, mock_config_entry, edge_config)
    mock_openems_client.check_edge_config.side_effect = OpenEMSError("no answer")

    await setup_integration(hass, mock_config_entry)

    assert _cached(hass_storage, mock_config_entry) == edge_config
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert f"Cannot check the configuration of edge {HOSTNAME}" in caplog.text


@pytest.mark.parametrize(
    ("error", "state", "translation_key"),
    [
        pytest.param(
            OpenEMSAuthError("rejected"),
            ConfigEntryState.SETUP_ERROR,
            "invalid_auth",
            id="login_rejected",
        ),
        pytest.param(
            OpenEMSConnectionError("unreachable"),
            ConfigEntryState.SETUP_RETRY,
            "cannot_connect",
            id="unreachable",
        ),
        pytest.param(
            OpenEMSError("internal error"),
            ConfigEntryState.SETUP_RETRY,
            "login_failed",
            id="login_error",
        ),
    ],
)
async def test_setup_login_errors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
    error: OpenEMSError,
    state: ConfigEntryState,
    translation_key: str,
) -> None:
    """Login errors fail the setup with a translated reason."""
    mock_openems_client.connect.side_effect = error

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is state
    assert mock_config_entry.error_reason_translation_key == translation_key


async def test_setup_rejected_login_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """A rejected login starts the reauthentication."""
    mock_openems_client.connect.side_effect = OpenEMSAuthError("rejected")

    await setup_integration(hass, mock_config_entry)

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_setup_configuration_error_closes_connection(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """A configuration which cannot be read closes the connection, setup is retried."""
    mock_openems_client.read_edge_config.side_effect = OpenEMSError("no answer")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_config_entry.error_reason_translation_key == "cannot_read_configuration"
    mock_openems_client.close.assert_awaited_once()


async def test_setup_failure_stops_backend(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """A setup failing after the backend took over the connection stops the backend."""
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=HomeAssistantError("platform setup failed"),
    ):
        await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    mock_openems_client.backend.connection.stop.assert_awaited_once()


async def test_unload_stops_backend(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """Unloading the entry stops the backend."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    mock_openems_client.backend.connection.stop.assert_awaited_once()


async def test_remove_entry_removes_cached_configuration(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    init_integration: MockConfigEntry,
) -> None:
    """Removing the entry removes the cached edge configuration."""
    assert await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    assert f"{DOMAIN}.{init_integration.entry_id}" not in hass_storage


@pytest.mark.usefixtures("init_integration")
async def test_devices(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """The edge and its components are devices, components without primary channels start disabled."""
    for device in dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    ):
        assert device == snapshot(name=device.name)


async def test_component_devices_keep_the_choice_of_the_user(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_openems_client: MagicMock,
) -> None:
    """Disabled component devices have no entities, enabling them reloads the entry."""
    mock_config_entry.add_to_hass(hass)
    for name, disabled_by in (
        ("ess0", dr.DeviceEntryDisabler.USER),
        ("_host", None),
    ):
        device_registry.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, f"{HOSTNAME} {name}")},
            disabled_by=disabled_by,
        )

    await setup_integration(hass, mock_config_entry)

    host = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{HOSTNAME} _host"), mock_config_entry.entry_id
    )
    assert not host.disabled
    ess = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{HOSTNAME} ess0"), mock_config_entry.entry_id
    )
    assert ess.disabled_by is dr.DeviceEntryDisabler.USER
    assert not entity_registry.async_get(ESS_SOC)

    device_registry.async_update_device(ess.id, disabled_by=None)
    await hass.async_block_till_done()

    assert mock_openems_client.create_backend.call_count == 2
    assert entity_registry.async_get(ESS_SOC)


@pytest.mark.usefixtures("init_integration")
async def test_rejected_reconnect_login_starts_reauth(
    hass: HomeAssistant, mock_openems_client: MagicMock
) -> None:
    """A login rejected during a reconnect starts the reauthentication."""
    mock_openems_client.backend.connection.enable_reconnect.call_args.args[1]()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def _remove_device(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    device_registry: dr.DeviceRegistry,
    entry: MockConfigEntry,
    identifier: str,
) -> bool:
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, identifier)}
    )
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": device.id,
        }
    )
    return (await client.receive_json())["success"]


@pytest.mark.parametrize(
    ("identifier", "removable"),
    [
        pytest.param(HOSTNAME, False, id="edge"),
        pytest.param(f"{HOSTNAME} ess0", False, id="component"),
        pytest.param(f"{HOSTNAME} meter1", True, id="removed_component"),
        pytest.param("fems54321", True, id="other_edge"),
    ],
)
async def test_remove_device(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    device_registry: dr.DeviceRegistry,
    init_integration: MockConfigEntry,
    identifier: str,
    removable: bool,
) -> None:
    """Only devices of components which are not part of the edge anymore are removable."""
    assert (
        await _remove_device(
            hass, hass_ws_client, device_registry, init_integration, identifier
        )
        is removable
    )


@pytest.mark.parametrize("multi_edge", [True])
@pytest.mark.parametrize(
    ("identifier", "removable"),
    [
        pytest.param(f"{HOSTNAME} 0", False, id="edge"),
        pytest.param(f"{HOSTNAME} 0 ess0", False, id="component"),
        pytest.param(f"{HOSTNAME} 0 meter1", True, id="removed_component"),
        pytest.param(f"{HOSTNAME} 1", True, id="other_edge"),
        pytest.param(f"{HOSTNAME} 1 ess0", True, id="component_of_other_edge"),
    ],
)
async def test_remove_device_multi_edge(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    device_registry: dr.DeviceRegistry,
    init_integration: MockConfigEntry,
    identifier: str,
    removable: bool,
) -> None:
    """Identifiers containing the edge ID are compared as a whole."""
    assert (
        await _remove_device(
            hass, hass_ws_client, device_registry, init_integration, identifier
        )
        is removable
    )


V5_DATA = {"user_input": ENTRY_DATA, "components": {"ess0": {"channels": []}}}


async def _migrate(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v1(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    entity_registry: er.EntityRegistry,
) -> None:
    """Version 1 entries are migrated through all versions."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={key: ENTRY_DATA[key] for key in ("host", "username", "password")},
    )
    hass_storage["openems_config"] = {
        "version": 1,
        "minor_version": 1,
        "key": "openems_config",
        "data": {"0": {"components": {"ess0": {"channels": []}}}},
    }
    entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        Platform.SENSOR, DOMAIN, f"{HOSTNAME}/edge0/ess0/Soc", config_entry=entry
    )

    await _migrate(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (6, 5)
    assert entry.data == {**ENTRY_DATA, CONF_EDGE: "0", CONF_TYPE: "direct_edge"}
    assert entry.options == ENTRY_OPTIONS
    assert _cached(hass_storage, entry) == {"ess0": {"channels": []}}
    assert "openems_config" not in hass_storage
    assert entity_registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, f"{HOSTNAME}/0/ess0/Soc"
    )


@pytest.mark.parametrize(
    ("version", "translation_key"),
    [
        pytest.param(1, "migration_data_missing", id="without_v1_configuration"),
        pytest.param(0, "migration_unsupported_version", id="unsupported_version"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_error(
    hass: HomeAssistant, version: int, translation_key: str
) -> None:
    """Entries which cannot be migrated are not set up."""
    entry = MockConfigEntry(domain=DOMAIN, version=version, data=ENTRY_DATA)

    await _migrate(hass, entry)

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.error_reason_translation_key == translation_key
    assert entry.version == version


@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v2_joins_component_device_identifiers(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """3-part device identifiers are joined, valid identifiers stay unchanged."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, data=V5_DATA, options={"ess0": True}
    )
    entry.add_to_hass(hass)
    edge_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, HOSTNAME)}
    )
    component_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, HOSTNAME, "ess0")},
        via_device_id=edge_device.id,
    )

    await _migrate(hass, entry)

    assert entry.version == 6
    assert device_registry.async_get(edge_device.id).identifiers == {(DOMAIN, HOSTNAME)}
    assert device_registry.async_get(component_device.id).identifiers == {
        (DOMAIN, f"{HOSTNAME} ess0")
    }


@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v3(hass: HomeAssistant) -> None:
    """The component selection of version 3 becomes part of the options."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=3, data=V5_DATA, options={"ctrlDebugLog0": False}
    )

    await _migrate(hass, entry)

    assert entry.version == 6
    assert entry.options == ENTRY_OPTIONS


@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v4_moves_component_selection_to_devices(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """Deselected components get devices disabled by the user, options are cleaned."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        data=V5_DATA,
        options={
            "components": {"ess0": True, "meter0": False, "ctrlDebugLog0": False},
            **ENTRY_OPTIONS,
        },
    )
    entry.add_to_hass(hass)
    edge_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, HOSTNAME)}
    )
    for name in ("ess0", "meter0"):
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{HOSTNAME} {name}")},
            via_device_id=edge_device.id,
        )

    await _migrate(hass, entry)

    assert entry.unique_id == HOSTNAME
    assert entry.options == ENTRY_OPTIONS
    assert {
        name: device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{HOSTNAME} {name}"), entry.entry_id
        ).disabled_by
        for name in ("ess0", "meter0", "ctrlDebugLog0")
    } == {
        "ess0": None,
        "meter0": dr.DeviceEntryDisabler.USER,
        "ctrlDebugLog0": dr.DeviceEntryDisabler.USER,
    }


@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v4_without_edge_device(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """Without an edge device no component devices are created."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        data=V5_DATA,
        options={"components": {"ctrlDebugLog0": False}, **ENTRY_OPTIONS},
    )

    await _migrate(hass, entry)

    assert entry.version == 6
    assert entry.options == ENTRY_OPTIONS
    assert not dr.async_entries_for_config_entry(device_registry, entry.entry_id)


@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v5_moves_configuration_to_store(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The edge configuration moves to the store, the connection data is flattened."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=5, data=V5_DATA, options=ENTRY_OPTIONS
    )

    await _migrate(hass, entry)

    assert (entry.version, entry.minor_version) == (6, 5)
    assert entry.data == ENTRY_DATA
    assert _cached(hass_storage, entry) == {"ess0": {"channels": []}}


@pytest.mark.parametrize(
    "edge_identifier",
    [
        pytest.param(HOSTNAME, id="single_edge"),
        pytest.param(f"{HOSTNAME} 0", id="multi_edge"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v6_1_sets_unique_id_from_edge_device(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry, edge_identifier: str
) -> None:
    """The unique ID is taken from the identifier of the edge device."""
    entry = MockConfigEntry(domain=DOMAIN, version=6, minor_version=1, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    edge_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, edge_identifier)}
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{edge_identifier} ess0")},
        via_device_id=edge_device.id,
    )

    await _migrate(hass, entry)

    assert (entry.version, entry.minor_version) == (6, 5)
    assert entry.unique_id == edge_identifier


@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v6_2_removes_refresh_configuration_button(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The entity of the removed refresh configuration button is removed."""
    entry = MockConfigEntry(domain=DOMAIN, version=6, minor_version=2, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    for key in ("refresh_configuration", "restart"):
        entity_registry.async_get_or_create(
            Platform.BUTTON, DOMAIN, f"{HOSTNAME}/0/{key}", config_entry=entry
        )

    await _migrate(hass, entry)

    assert not entity_registry.async_get_entity_id(
        Platform.BUTTON, DOMAIN, f"{HOSTNAME}/0/refresh_configuration"
    )
    assert entity_registry.async_get_entity_id(
        Platform.BUTTON, DOMAIN, f"{HOSTNAME}/0/restart"
    )


@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v6_3_removes_write_only_sensors(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    entity_registry: er.EntityRegistry,
    edge_config: dict[str, Any],
) -> None:
    """The sensor entities of write-only channels are removed."""
    entry = MockConfigEntry(domain=DOMAIN, version=6, minor_version=3, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    store_edge_config(hass_storage, entry, edge_config)
    for channel in ("SetActivePowerEquals", "Soc"):
        entity_registry.async_get_or_create(
            Platform.SENSOR, DOMAIN, f"{HOSTNAME}/0/ess0/{channel}", config_entry=entry
        )

    await _migrate(hass, entry)

    assert not entity_registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, f"{HOSTNAME}/0/ess0/SetActivePowerEquals"
    )
    assert entity_registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, f"{HOSTNAME}/0/ess0/Soc"
    )


@pytest.mark.usefixtures("mock_setup_entry")
async def test_migrate_v6_4_resets_device_entry_type(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """The edge and its components are no web services."""
    entry = MockConfigEntry(domain=DOMAIN, version=6, minor_version=4, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, HOSTNAME)},
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    await _migrate(hass, entry)

    assert (entry.version, entry.minor_version) == (6, 5)
    assert device_registry.async_get(device.id).entry_type is None
