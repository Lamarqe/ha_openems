"""Unit tests for OpenEMSDerivedChannel.

Channel definitions are taken directly from the battery0 getChannelsOfComponent
response (tests/data/battery0.json), embedded here as a static Python literal so
the test is self-contained and does not read any data files at runtime.
The combined-sensor configuration comes from the real combined_sensors.json.
"""

import re
from unittest.mock import MagicMock

import pytest
from yarl import URL

from custom_components.openems.helpers_openems import expand_sensor_def
from custom_components.openems.openems import OpenEMSDerivedChannel, OpenEMSEdge

BATTERY0_CHANNELS = [
    # Needed for VoltageDifference derived sensor and integration test
    {
        "id": "MaxCellVoltage",
        "accessMode": "RO",
        "persistencePriority": "HIGH",
        "text": "",
        "type": "INTEGER",
        "unit": "mV",
        "category": "OPENEMS_TYPE",
    },
    {
        "id": "MinCellVoltage",
        "accessMode": "RO",
        "persistencePriority": "HIGH",
        "text": "",
        "type": "INTEGER",
        "unit": "mV",
        "category": "OPENEMS_TYPE",
    },
    # Two cells from Tower0Module0: needed for the max-minus-min test (non-trivial
    # spread) and for the partial-update test (one cell set, one still None).
    {
        "id": "Tower0Module0Cell000Voltage",
        "accessMode": "RO",
        "persistencePriority": "LOW",
        "text": "",
        "type": "INTEGER",
        "unit": "mV",
        "category": "OPENEMS_TYPE",
    },
    {
        "id": "Tower0Module0Cell001Voltage",
        "accessMode": "RO",
        "persistencePriority": "LOW",
        "text": "",
        "type": "INTEGER",
        "unit": "mV",
        "category": "OPENEMS_TYPE",
    },
    # One representative cell from each of the other three modules so that
    # expand_sensor_def produces a CellVoltageDifference sensor for every module.
    {
        "id": "Tower0Module1Cell000Voltage",
        "accessMode": "RO",
        "persistencePriority": "LOW",
        "text": "",
        "type": "INTEGER",
        "unit": "mV",
        "category": "OPENEMS_TYPE",
    },
    {
        "id": "Tower0Module2Cell000Voltage",
        "accessMode": "RO",
        "persistencePriority": "LOW",
        "text": "",
        "type": "INTEGER",
        "unit": "mV",
        "category": "OPENEMS_TYPE",
    },
    {
        "id": "Tower0Module3Cell000Voltage",
        "accessMode": "RO",
        "persistencePriority": "LOW",
        "text": "",
        "type": "INTEGER",
        "unit": "mV",
        "category": "OPENEMS_TYPE",
    },
]

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_backend():
    backend = MagicMock()
    backend.multi_edge = False
    backend.connection.conn_url = URL("ws://localhost:8085/openems-backend-ui")
    backend.connection.notify_data_received = MagicMock()
    return backend


def _make_component_with_edge(name: str = "battery0"):
    """Return a minimal mock component backed by a lightweight DummyEdge."""

    class DummyEdge:
        def __init__(self) -> None:
            self.hostname = "testhost"
            self.id = "edge0"
            self._registered_handlers: dict[str, set] = {}

        def register_channel(self, channel_names, handler):
            for ch in channel_names:
                self._registered_handlers.setdefault(ch, set()).add(handler)

        def unregister_channel(self, handler):
            for handlers in self._registered_handlers.values():
                handlers.discard(handler)

        @property
        def registered_channels(self) -> dict[str, set]:
            return self._registered_handlers

    comp = MagicMock()
    comp.name = name
    comp.json_properties = {}
    comp.edge = DummyEdge()
    return comp


def _voltage_difference_def() -> dict:
    """Return the VoltageDifference combined-sensor definition."""
    return {
        "id": "VoltageDifference",
        "template": "{{MaxCellVoltage}} - {{MinCellVoltage}}",
        "unit_of_measurement": "mV",
    }


# ---------------------------------------------------------------------------
# VoltageDifference derived channel - unit tests
# ---------------------------------------------------------------------------


def test_voltage_difference_native_value_none_before_any_update() -> None:
    """native_value is None until both reference channels carry a value."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    assert sensor.native_value is None


def test_voltage_difference_native_value_none_with_partial_update() -> None:
    """native_value remains None when only one of the two refs is set."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    sensor.handle_data_update("battery0/MaxCellVoltage", 3500)

    assert sensor.native_value is None


def test_voltage_difference_computes_correctly() -> None:
    """native_value equals MaxCellVoltage - MinCellVoltage once both are set."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    sensor.handle_data_update("battery0/MaxCellVoltage", 3500)
    sensor.handle_data_update("battery0/MinCellVoltage", 3300)

    assert sensor.native_value == pytest.approx(200.0)


def test_voltage_difference_updates_on_new_value() -> None:
    """native_value reflects the latest reference-channel values."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    sensor.handle_data_update("battery0/MaxCellVoltage", 3500)
    sensor.handle_data_update("battery0/MinCellVoltage", 3300)
    sensor.handle_data_update("battery0/MaxCellVoltage", 3600)

    assert sensor.native_value == pytest.approx(300.0)


def test_voltage_difference_callback_called_on_value_change() -> None:
    """Registered callback is invoked whenever the computed value changes."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    calls: list[None] = []
    sensor.register_callback(lambda: calls.append(None))

    sensor.handle_data_update("battery0/MaxCellVoltage", 3500)
    sensor.handle_data_update("battery0/MinCellVoltage", 3300)

    assert len(calls) == 1


def test_voltage_difference_callback_not_called_when_value_unchanged() -> None:
    """Callback is NOT invoked when a data update produces the same derived value."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    calls: list[None] = []
    sensor.register_callback(lambda: calls.append(None))

    sensor.handle_data_update("battery0/MaxCellVoltage", 3500)
    sensor.handle_data_update("battery0/MinCellVoltage", 3300)
    assert len(calls) == 1

    sensor.handle_data_update("battery0/MinCellVoltage", 3300)
    assert len(calls) == 1


def test_voltage_difference_ignores_non_reference_channel() -> None:
    """Updates for channels not in the sensor's reference set are ignored."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    calls: list[None] = []
    sensor.register_callback(lambda: calls.append(None))

    sensor.handle_data_update("battery0/SomeOtherChannel", 9999)

    assert sensor.native_value is None
    assert len(calls) == 0


def test_voltage_difference_becomes_none_on_null_reference() -> None:
    """When a reference channel receives None the derived value becomes None."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    calls: list[None] = []
    sensor.register_callback(lambda: calls.append(None))

    sensor.handle_data_update("battery0/MaxCellVoltage", 3500)
    sensor.handle_data_update("battery0/MinCellVoltage", 3300)
    assert sensor.native_value == pytest.approx(200.0)

    sensor.handle_data_update("battery0/MaxCellVoltage", None)

    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# VoltageDifference - register/unregister lifecycle
# ---------------------------------------------------------------------------


def test_voltage_difference_register_callback_adds_to_edge() -> None:
    """register_callback subscribes reference channels on the edge."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())
    sensor.register_callback(lambda: None)

    registered = comp.edge.registered_channels
    assert any("MaxCellVoltage" in k for k in registered)
    assert any("MinCellVoltage" in k for k in registered)


def test_voltage_difference_unregister_callback_removes_handler() -> None:
    """unregister_callback removes the sensor from all edge subscriptions."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())
    sensor.register_callback(lambda: None)
    sensor.unregister_callback()

    assert sensor.callback is None
    for handlers in comp.edge.registered_channels.values():
        assert sensor not in handlers


def test_voltage_difference_no_callback_after_unregister() -> None:
    """After unregistering, handle_data_update must not invoke the callback."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    calls: list[None] = []
    sensor.register_callback(lambda: calls.append(None))
    sensor.unregister_callback()

    sensor.handle_data_update("battery0/MaxCellVoltage", 3500)
    sensor.handle_data_update("battery0/MinCellVoltage", 3300)

    assert len(calls) == 0


# ---------------------------------------------------------------------------
# Tower module cell-voltage difference - multi-cell max-min computation
# ---------------------------------------------------------------------------


def _expand_tower_cell_voltage_diff() -> list[dict]:
    """Expand Tower{tId}Module{mId}CellVoltageDifference against BATTERY0_CHANNELS.

    Returns all expanded sensor definitions (one per tower/module combination).
    """
    sensor_def = {
        "id": "Tower{tId}Module{mId}CellVoltageDifference",
        "template": (
            "[{{Tower{tId}Module{mId}Cell{cId}Voltage}}] | max"
            " - [{{Tower{tId}Module{mId}Cell{cId}Voltage}}] | min"
        ),
        "unit_of_measurement": "mV",
    }
    channel_ids = [c["id"] for c in BATTERY0_CHANNELS]
    return expand_sensor_def(sensor_def, channel_ids)


def test_tower_cell_voltage_difference_expansion_covers_all_modules() -> None:
    """expand_sensor_def yields one definition per tower/module with cell-voltage channels."""
    expanded = _expand_tower_cell_voltage_diff()
    expanded_ids = {e["id"] for e in expanded}

    assert "Tower0Module0CellVoltageDifference" in expanded_ids
    assert "Tower0Module1CellVoltageDifference" in expanded_ids
    assert "Tower0Module2CellVoltageDifference" in expanded_ids
    assert "Tower0Module3CellVoltageDifference" in expanded_ids


def test_tower_cell_voltage_difference_computes_max_minus_min() -> None:
    """Derived sensor returns max(cell voltages) - min(cell voltages)."""
    comp = _make_component_with_edge()
    # Use Tower0Module0 (14 cells in the full dataset)
    expanded = {e["id"]: e for e in _expand_tower_cell_voltage_diff()}
    sensor = OpenEMSDerivedChannel(comp, expanded["Tower0Module0CellVoltageDifference"])

    # Set all cells to 3300 mV, then raise the last one to 3360 mV.
    # Expected spread: 3360 - 3300 = 60 mV.
    cell_ids_m0 = sorted(
        c["id"]
        for c in BATTERY0_CHANNELS
        if re.match(r"Tower0Module0Cell\d+Voltage$", c["id"])
    )
    for cid in cell_ids_m0:
        sensor.handle_data_update(f"battery0/{cid}", 3300)
    sensor.handle_data_update(f"battery0/{cell_ids_m0[-1]}", 3360)

    assert sensor.native_value == pytest.approx(60.0)


def test_tower_cell_voltage_difference_partial_update_stays_none() -> None:
    """Result is None until all cell-voltage references are populated."""
    comp = _make_component_with_edge()
    expanded = {e["id"]: e for e in _expand_tower_cell_voltage_diff()}
    sensor = OpenEMSDerivedChannel(comp, expanded["Tower0Module0CellVoltageDifference"])

    # Send only the first cell; the others remain None
    cell_ids_m0 = sorted(
        c["id"]
        for c in BATTERY0_CHANNELS
        if re.match(r"Tower0Module0Cell\d+Voltage$", c["id"])
    )
    sensor.handle_data_update(f"battery0/{cell_ids_m0[0]}", 3300)

    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Integration test: derived sensors are created by init_channels
# ---------------------------------------------------------------------------


def test_derived_sensors_created_via_init_channels() -> None:
    """OpenEMSEdge.init_channels populates derived_sensors from combined_sensors.json."""
    backend = _make_backend()

    battery0_conf = {
        "_PropertyAlias": "Batterie",
        "factoryId": "Battery.Fenecon.Home",
        "properties": {},
        "channels": [c.copy() for c in BATTERY0_CHANNELS],
    }
    component_config = {
        "_host": {"Hostname": "testhost"},
        "battery0": battery0_conf,
    }

    edge = OpenEMSEdge(backend, "edge0", component_config)
    try:
        battery0 = edge.components["battery0"]
        derived_ids = {s.name for s in battery0.derived_sensors}

        assert "VoltageDifference" in derived_ids
        # All four tower/module combinations must be present
        assert "Tower0Module0CellVoltageDifference" in derived_ids
        assert "Tower0Module1CellVoltageDifference" in derived_ids
        assert "Tower0Module2CellVoltageDifference" in derived_ids
        assert "Tower0Module3CellVoltageDifference" in derived_ids
    finally:
        edge.stop()


def test_derived_channel_unit_of_measurement() -> None:
    """Derived channel exposes the unit_of_measurement from the sensor definition."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    assert sensor.unit == "mV"


def test_derived_channel_unique_id_contains_component_and_channel() -> None:
    """unique_id encodes edge hostname, edge id, component name, and sensor id."""
    comp = _make_component_with_edge()
    sensor = OpenEMSDerivedChannel(comp, _voltage_difference_def())

    uid = sensor.unique_id()
    assert "testhost" in uid
    assert "battery0" in uid
    assert "VoltageDifference" in uid
