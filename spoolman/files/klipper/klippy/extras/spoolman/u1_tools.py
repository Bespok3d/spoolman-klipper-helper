"""U1 device facts: the print-task file reads and the machine's tooling geometry.

The single home of the U1's physical/virtual tooling constants and of everything read from the
firmware's print_task.json. Modules that need a U1 fact import it from here, so the future
mainline-Klipper split has one device seam to cut, not a constant scattered per file.
"""
import json
import os
from typing import Any

EXTRUDERS_COUNT = 4
MAX_TOOLS_COUNT = 32


def _load_print_task(printer: Any) -> dict[str, Any]:
    path = os.path.join(printer.get_snapmaker_config_dir(), "print_task.json")
    with open(path) as task_file:
        task: dict[str, Any] = json.load(task_file)
        return task


def _load_extruder_map(printer: Any) -> list[Any]:
    table: list[Any] = _load_print_task(printer).get("extruder_map_table", [])
    return table


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
        self.extruder_map_table: list[Any] = [None] * MAX_TOOLS_COUNT

    def update_map(self) -> None:
        self.extruder_map_table = _load_extruder_map(self.printer)
        self.logs.verbose(f"Tools-extruders map updated: {self.extruder_map_table}")

    def clear_map(self) -> None:
        self.extruder_map_table = [None] * MAX_TOOLS_COUNT
        self.logs.verbose("Tools-extruders map cleared")

    def extruder_for_tool(self, tool_id: int) -> Any:
        extruder = _element_at(self.extruder_map_table, tool_id)
        if extruder is None:
            self.logs.error(f"Cannot resolve extruder for T{tool_id}")
        else:
            self.logs.verbose(f"Tool {tool_id} is Extruder {extruder}")
        return extruder

    def get_spools_config(self) -> list[dict[str, Any]]:
        return _load_spools_config(self.printer)
