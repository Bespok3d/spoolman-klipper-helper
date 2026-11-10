# ruff: noqa: PLR2004  Tests assert against literal extruder indexes.
"""Regression tests for the U1 print-task helpers."""
import json
from pathlib import Path
from typing import Any

from klipper_fakes import (
    REMAPPED_HEADER,
    REMAPPED_TABLE,
    FakePrintStats,
    FakePrintTaskConfig,
    FakeSdcard,
    write_remapped_gcode,
)
from u1_tools import (
    TOOL_COUNT_MACRO,
    U1Tools,
    _element_at,
    _load_spools_config,
    live_extruder_map,
    tools_the_header_lists,
)


class FakeToolCountMacro:
    def __init__(self, count: int) -> None:
        self._count = count

    def get_status(self, eventtime: Any) -> dict[str, Any]:
        return {"count": self._count}


class FakePrinter:
    def __init__(
        self, config_dir: str = "", extruder_map: Any = None, state: str = "standby",
        file_path: str | None = None, declared_tool_count: int | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._objects: dict[str, Any] = {
            "print_task_config": FakePrintTaskConfig(
                {"extruder_map_table": extruder_map} if extruder_map is not None else None
            ),
            "print_stats": FakePrintStats(state),
            "virtual_sdcard": FakeSdcard(file_path),
        }
        if declared_tool_count is not None:
            self._objects[TOOL_COUNT_MACRO] = FakeToolCountMacro(declared_tool_count)

    def get_snapmaker_config_dir(self) -> str:
        return self._config_dir

    def lookup_object(self, name: str, default: Any = None) -> Any:
        return self._objects.get(name, default)


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


def printing_remapped(tmp_path: Path, state: str = "printing") -> FakePrinter:
    return FakePrinter(
        extruder_map=REMAPPED_TABLE, state=state, file_path=write_remapped_gcode(tmp_path)
    )


def test_live_map_counts_only_the_tools_the_print_uses(tmp_path: Path) -> None:
    assert live_extruder_map(printing_remapped(tmp_path)) == [2, 0, None, None, None, 2]


def test_a_paused_print_keeps_its_map(tmp_path: Path) -> None:
    assert live_extruder_map(printing_remapped(tmp_path, "paused")) == [2, 0, None, None, None, 2]


def test_outside_a_print_the_map_is_the_home_map() -> None:
    assert live_extruder_map(FakePrinter(extruder_map=REMAPPED_TABLE)) == [0, 1, 2, 3]


def test_a_print_with_no_tool_list_uses_the_declared_count() -> None:
    printer = FakePrinter(extruder_map=REMAPPED_TABLE, state="printing", declared_tool_count=2)
    assert live_extruder_map(printer) == [2, 0, None, None, None, None]


def test_a_print_with_no_tool_list_and_no_count_uses_the_home_tools() -> None:
    printer = FakePrinter(extruder_map=REMAPPED_TABLE, state="printing")
    assert live_extruder_map(printer) == [2, 0, 0, 0, None, None]


def test_the_header_lists_tools_counted_from_zero() -> None:
    assert tools_the_header_lists(REMAPPED_HEADER) == (0, 1, 5)
    assert tools_the_header_lists("G28\n") is None


def test_extruder_for_tool_follows_the_remapped_print(tmp_path: Path) -> None:
    logs = RecordingLogs()
    tools = U1Tools(printing_remapped(tmp_path), logs)
    assert tools.extruder_for_tool(0) == 2
    assert tools.extruder_for_tool(5) == 2
    assert tools.extruder_for_tool(2) is None
    assert logs.errors == []


def test_extruder_for_tool_resolves_from_live_map_without_priming() -> None:
    # The bug: a manual tool change while idle logged a phantom "Cannot resolve extruder" even
    # though the firmware map was intact. Outside a print every home tool feeds its own lane.
    logs = RecordingLogs()
    tools = U1Tools(FakePrinter(extruder_map=[0, 1, 2, 3, 0, 0]), logs)
    assert tools.extruder_for_tool(1) == 1
    assert logs.errors == []


def test_extruder_for_tool_reports_a_tool_that_feeds_no_lane() -> None:
    logs = RecordingLogs()
    tools = U1Tools(FakePrinter(extruder_map=[0, 1, 2, 3]), logs)
    assert tools.extruder_for_tool(9) is None
    assert "Tool 9 feeds no lane" in logs.verbose_lines


def test_live_extruder_map_falls_back_to_identity_when_absent() -> None:
    assert live_extruder_map(FakePrinter()) == [0, 1, 2, 3]
