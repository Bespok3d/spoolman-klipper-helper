"""U1 device facts: the print-task reads and the machine's tooling geometry.

The single home of the U1's physical/virtual tooling constants and of everything read from the
firmware's print-task state. Modules that need a U1 fact import it from here, so the future
mainline-Klipper split has one device seam to cut, not a constant scattered per file.

The tool-to-extruder map has ONE source of truth: the live print_task_config object the firmware
keeps current (the same object the tracker reads). There is no cached copy to go stale when a
print ends, which is what made a manual tool change outside a print report a phantom "cannot
resolve extruder" while the tracker itself resolved the same tool fine.
"""
import json
import os
from typing import Any

EXTRUDERS_COUNT = 4
MAX_TOOLS_COUNT = 32


def live_extruder_map(printer: Any) -> list[Any]:
    task = printer.lookup_object("print_task_config", None)
    config = getattr(task, "print_task_config", None)
    table = config.get("extruder_map_table") if isinstance(config, dict) else None
    return table if isinstance(table, list) else list(range(EXTRUDERS_COUNT))


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
        self.logs.error(f"Cannot resolve extruder for T{tool_id}")
        return None

    def get_spools_config(self) -> list[dict[str, Any]]:
        return _load_spools_config(self.printer)
