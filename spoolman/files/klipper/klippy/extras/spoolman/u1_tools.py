"""U1 device facts: the print-task reads and the machine's tooling geometry.

The single home of the U1's physical/virtual tooling constants and of everything read from the
firmware's print-task state. Modules that need a U1 fact import it from here, so the future
mainline-Klipper split has one device seam to cut, not a constant scattered per file.

The tool-to-extruder map has ONE source of truth: the live print_task_config object the firmware
keeps current. A logical tool can be fed by any lane and several tools can share one, but the
table only means that while a print is printing or paused, and only for the tools the print uses:
an entry the file never set still reads as lane 0, and between prints the firmware holds its
default table. So outside a print the map is the home map, T0 on E0 through T3 on E3, and during
one every tool the print does not use feeds no lane.
"""
import functools
import json
import os
import re
from typing import Any

EXTRUDERS_COUNT = 4
MAX_TOOLS_COUNT = 32
HOME_TOOLS = tuple(range(EXTRUDERS_COUNT))
ACTIVE_PRINT_STATES = ("printing", "paused")
TOOL_COUNT_MACRO = "gcode_macro AFC_TOOLS_IN_PLAY"
# The slicer writes the list of filaments a file uses near its top; a few kilobytes reach it.
HEADER_BYTES_TO_SCAN = 4096
SLICER_FILAMENT_LIST = re.compile(r"^; filament: *([0-9][0-9, ]*)\r?$", re.MULTILINE)


def print_state(printer: Any) -> str:
    stats = printer.lookup_object("print_stats", None)
    return getattr(stats, "state", "") or ""


def tools_the_header_lists(header: str) -> tuple[int, ...] | None:
    """The logical tools a file uses, from the slicer's `; filament: 2,1` header line, which counts
    filaments from 1. None when the file carries no such line, so the caller can tell "this file
    names no tools" from "this file uses none"."""
    match = SLICER_FILAMENT_LIST.search(header)
    if match is None:
        return None
    filament_numbers = (int(word) for word in match.group(1).split(",") if word.strip())
    return tuple(sorted({number - 1 for number in filament_numbers if number > 0}))


# Keyed by the file's modification time as well as its path, so a file uploaded again under the
# same name is read again instead of answering with the tools of the old one.
@functools.lru_cache(maxsize=4)
def _tools_in_file_version(file_path: str, modified_at: float) -> tuple[int, ...] | None:
    try:
        with open(file_path, encoding="utf-8", errors="replace") as gcode_file:
            return tools_the_header_lists(gcode_file.read(HEADER_BYTES_TO_SCAN))
    except OSError:
        return None


def tools_the_file_uses(file_path: str) -> tuple[int, ...] | None:
    try:
        modified_at = os.stat(file_path).st_mtime
    except OSError:
        return None
    return _tools_in_file_version(file_path, modified_at)


def _file_in_play(printer: Any) -> str | None:
    sdcard = printer.lookup_object("virtual_sdcard", None)
    file_path = sdcard.file_path() if sdcard is not None else None
    return file_path if isinstance(file_path, str) and file_path else None


def _declared_tool_count(printer: Any) -> int:
    macro = printer.lookup_object(TOOL_COUNT_MACRO, None)
    count = macro.get_status(None).get("count", 0) if macro is not None else 0
    return count if isinstance(count, int) else 0


def tools_in_play(printer: Any) -> tuple[int, ...]:
    """The logical tools the current print uses: none between prints; else the ones the file
    lists; else the count a web interface declared for it; else the four home tools, the tools a
    print with no list of its own can use without a remap."""
    if print_state(printer) not in ACTIVE_PRINT_STATES:
        return ()
    file_path = _file_in_play(printer)
    tools_in_file = tools_the_file_uses(file_path) if file_path else None
    if tools_in_file is not None:
        return tools_in_file
    declared_count = _declared_tool_count(printer)
    return tuple(range(declared_count)) if declared_count > 0 else HOME_TOOLS


def _firmware_map_table(printer: Any) -> list[Any] | None:
    task = printer.lookup_object("print_task_config", None)
    config = getattr(task, "print_task_config", None)
    table = config.get("extruder_map_table") if isinstance(config, dict) else None
    return table if isinstance(table, list) else None


def _lane_in_play(tool_index: int, lane: Any, in_play: tuple[int, ...]) -> int | None:
    return lane if tool_index in in_play and isinstance(lane, int) else None


def live_extruder_map(printer: Any) -> list[int | None]:
    """The lane each logical tool is fed by, indexed by tool; None for a tool that feeds no lane."""
    in_play = tools_in_play(printer)
    table = _firmware_map_table(printer)
    if not in_play or table is None:
        return list(HOME_TOOLS)
    return [_lane_in_play(tool_index, lane, in_play) for tool_index, lane in enumerate(table)]


def _load_print_task(printer: Any) -> dict[str, Any]:
    path = os.path.join(printer.get_snapmaker_config_dir(), "print_task.json")
    with open(path) as task_file:
        task: dict[str, Any] = json.load(task_file)
        return task


def _element_at(values: list[Any], index: int, default: Any = None) -> Any:
    return values[index] if index < len(values) else default


def _load_spools_config(printer: Any) -> list[dict[str, Any]]:
    data = _load_print_task(printer)

    vendors = data.get("filament_vendor", [])
    types = data.get("filament_type", [])
    sub_types = data.get("filament_sub_type", [])
    colors = data.get("filament_color", [])
    color_rgba = data.get("filament_color_rgba", [])
    officials = data.get("filament_official", [])
    sku = data.get("filament_sku", [])
    spool_id = data.get("filament_spool_id", [])

    count = max(
        len(vendors), len(types), len(sub_types),
        len(colors), len(color_rgba), len(officials),
        len(sku), 1,
    )

    return [
        {
            "VENDOR": _element_at(vendors, index, "NONE"),
            "MAIN_TYPE": _element_at(types, index, "NONE"),
            "SUB_TYPE": _element_at(sub_types, index, "NONE"),
            "COLOR": _element_at(colors, index, "FFFFFFFF"),
            "ARGB_COLOR": _element_at(color_rgba, index, "FFFFFFFF"),
            "OFFICIAL": _element_at(officials, index, False),
            "SKU": _element_at(sku, index, None),
            "SPOOL_ID": _element_at(spool_id, index, None),
        }
        for index in range(count)
    ]


class U1Tools:
    def __init__(self, config: Any, logs: Any) -> None:
        self.printer = config.get_printer() if hasattr(config, "get_printer") else config
        self.logs = logs

    def extruder_for_tool(self, tool_id: int) -> Any:
        extruder = _element_at(live_extruder_map(self.printer), tool_id)
        if isinstance(extruder, int):
            self.logs.verbose(f"Tool {tool_id} is Extruder {extruder}")
            return extruder
        self.logs.verbose(f"Tool {tool_id} feeds no lane")
        return None

    def get_spools_config(self) -> list[dict[str, Any]]:
        return _load_spools_config(self.printer)
