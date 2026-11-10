# ruff: noqa: PLR2004  Tests assert against literal extruder indexes.
"""Regression tests for the U1 print-task helpers."""
import json
from pathlib import Path
from typing import Any

from u1_tools import U1Tools, _element_at, _load_spools_config, live_extruder_map


class FakePrintTaskConfig:
    def __init__(self, extruder_map: Any) -> None:
        self.print_task_config = (
            {"extruder_map_table": extruder_map} if extruder_map is not None else None
        )


class FakePrinter:
    def __init__(self, config_dir: str = "", extruder_map: Any = None) -> None:
        self._config_dir = config_dir
        self._task = FakePrintTaskConfig(extruder_map)

    def get_snapmaker_config_dir(self) -> str:
        return self._config_dir

    def lookup_object(self, name: str, default: Any = None) -> Any:
        return self._task if name == "print_task_config" else default


class RecordingLogs:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.verbose_lines: list[str] = []

    def verbose(self, message: str) -> None:
        self.verbose_lines.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


def write_print_task(tmp_path: Path, payload: dict[str, Any]) -> FakePrinter:
    (tmp_path / "print_task.json").write_text(json.dumps(payload))
    return FakePrinter(str(tmp_path))


def test_element_at_within_bounds() -> None:
    assert _element_at(["a", "b"], 1, "x") == "b"


def test_element_at_out_of_bounds_returns_default() -> None:
    assert _element_at(["a"], 5, "x") == "x"


def test_load_spools_config_uses_longest_array_for_row_count(tmp_path: Path) -> None:
    printer = write_print_task(tmp_path, {
        "filament_vendor": ["Acme", "Globex"],
        "filament_type": ["PLA"],
    })
    spools = _load_spools_config(printer)
    assert [row["VENDOR"] for row in spools] == ["Acme", "Globex"]
    assert spools[1]["MAIN_TYPE"] == "NONE"


def test_load_spools_config_always_yields_at_least_one_row(tmp_path: Path) -> None:
    printer = write_print_task(tmp_path, {})
    spools = _load_spools_config(printer)
    assert len(spools) == 1
    assert spools[0]["VENDOR"] == "NONE"


def test_extruder_for_tool_resolves_from_live_map_without_priming() -> None:
    # The bug: a manual tool change while idle (no print ran, so the old cache was never primed
    # or was wiped at print end) logged a phantom "Cannot resolve extruder" even though the
    # firmware map was intact. The live map has nothing to prime, so the tool resolves straight
    # away and no error is logged.
    logs = RecordingLogs()
    tools = U1Tools(FakePrinter(extruder_map=[0, 1, 2, 3, 0, 0]), logs)
    assert tools.extruder_for_tool(1) == 1
    assert tools.extruder_for_tool(4) == 0
    assert logs.errors == []


def test_extruder_for_tool_reports_a_tool_absent_from_the_map() -> None:
    logs = RecordingLogs()
    tools = U1Tools(FakePrinter(extruder_map=[0, 1, 2, 3]), logs)
    assert tools.extruder_for_tool(9) is None
    assert any("Cannot resolve extruder for T9" in line for line in logs.errors)


def test_live_extruder_map_falls_back_to_identity_when_absent() -> None:
    assert live_extruder_map(FakePrinter()) == [0, 1, 2, 3]
