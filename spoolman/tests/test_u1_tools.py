"""Regression tests for the U1 print-task helpers."""
import json
from pathlib import Path
from typing import Any

from u1_tools import U1Tools, _element_at, _load_spools_config


class FakePrinter:
    def __init__(self, config_dir: str) -> None:
        self._config_dir = config_dir

    def get_snapmaker_config_dir(self) -> str:
        return self._config_dir


class FakeLogs:
    def verbose(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


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


def test_extruder_for_tool_resolves_and_defaults() -> None:
    tools = U1Tools(None, FakeLogs())
    tools.extruder_map_table = [0, 1, 2]
    assert tools.extruder_for_tool(1) == 1
    assert tools.extruder_for_tool(99) is None
