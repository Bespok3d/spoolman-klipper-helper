"""Mirror a manually picked Spoolman spool's color and material into the U1 print task.

The filament NAME on the AFC panel resolves live from the tool macro's spool_id; color and
material do NOT. Both the AFC panel and the touchscreen read color/material from the firmware
print_task_config (by physical extruder). When a user picks a Spoolman spool for an untagged
lane there is nothing that writes that spool's color/material back, so those views stay stale.

This component stays passive: it OBSERVES the tool macros' spool_id via a Moonraker status
subscription (additive, never intercepting any gcode) and REACTS by issuing the firmware's own
SET_PRINT_FILAMENT_CONFIG. RFID-tagged channels are left untouched (the tag is the source of truth).
A pick made mid-print is deferred and applied when the print leaves printing/paused.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from moonraker.confighelper import ConfigHelper

PHYSICAL_EXTRUDER_COUNT = 4
RGB_HEX_LENGTH = 6
RGBA_HEX_LENGTH = 8
OPAQUE_ALPHA = "FF"
TOOL_OBJECT_PREFIX = "gcode_macro T"
ACTIVE_PRINT_STATES = ("printing", "paused")
GCODE_UNSAFE_CHARS = ('"', "'", "\n", "\r", ";")
CONFIG_FIELD_BY_ARG = {
    "VENDOR": "filament_vendor",
    "FILAMENT_TYPE": "filament_type",
    "FILAMENT_SUBTYPE": "filament_sub_type",
    "FILAMENT_COLOR_RGBA": "filament_color_rgba",
}
QUOTED_ARGS = ("VENDOR", "FILAMENT_TYPE", "FILAMENT_SUBTYPE")
# Spoolman has no sub-type concept, but the firmware's SET_PRINT_FILAMENT_CONFIG rejects a
# FILAMENT_TYPE that arrives without both VENDOR and FILAMENT_SUBTYPE ("incomplete parameters").
NO_SUBTYPE = ""

SUBSCRIPTION_OBJECTS: dict[str, list[str] | None] = {
    **{f"{TOOL_OBJECT_PREFIX}{tool_index}": ["spool_id"]
       for tool_index in range(PHYSICAL_EXTRUDER_COUNT)},
    "print_task_config": None,
    "print_stats": ["state"],
}

StatusCallback = Callable[[dict, float], Awaitable[None]]


def coerce_spool_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None


def normalize_color_rgba(color_hex: str) -> str:
    cleaned = (color_hex or "").strip().upper()
    if len(cleaned) == RGBA_HEX_LENGTH:
        return cleaned
    if len(cleaned) == RGB_HEX_LENGTH:
        return cleaned + OPAQUE_ALPHA
    return ""


def filament_config_args_from_spool(spool: dict, physical_extruder: int) -> dict[str, str]:
    filament = spool.get("filament") or {}
    vendor = (filament.get("vendor") or {}).get("name") or ""
    material = filament.get("material") or ""
    color = normalize_color_rgba(filament.get("color_hex") or "")
    args: dict[str, str] = {"CONFIG_EXTRUDER": str(physical_extruder)}
    if material:
        args["VENDOR"] = vendor
        args["FILAMENT_TYPE"] = material
        args["FILAMENT_SUBTYPE"] = NO_SUBTYPE
    if color:
        args["FILAMENT_COLOR_RGBA"] = color
    return args


def _strip_unsafe(value: str) -> str:
    return "".join(char for char in value if char not in GCODE_UNSAFE_CHARS)


def _format_arg(key: str, value: str) -> str:
    if key in QUOTED_ARGS:
        return f'{key}="{_strip_unsafe(value)}"'
    return f"{key}={value}"


def set_print_filament_config_gcode(args: dict[str, str]) -> str:
    pairs = " ".join(_format_arg(key, value) for key, value in args.items())
    return f"SET_PRINT_FILAMENT_CONFIG {pairs}"


def _value_at(values: list, index: int) -> Any:
    return values[index] if 0 <= index < len(values) else None


def physical_extruder_for_tool(extruder_map_table: list, tool_index: int) -> int | None:
    physical = _value_at(extruder_map_table, tool_index)
    if not isinstance(physical, int):
        return None
    if 0 <= physical < PHYSICAL_EXTRUDER_COUNT:
        return physical
    return None


def channel_is_official(filament_official: list, physical_extruder: int) -> bool:
    return bool(_value_at(filament_official, physical_extruder))


def has_filament_fields(args: dict[str, str]) -> bool:
    return any(arg in args for arg in CONFIG_FIELD_BY_ARG)


def changed_tool_spools(
    previous_spool_by_tool: dict[int, int | None], status: dict
) -> dict[int, int | None]:
    changed: dict[int, int | None] = {}
    for tool_index in range(PHYSICAL_EXTRUDER_COUNT):
        macro_status = status.get(f"{TOOL_OBJECT_PREFIX}{tool_index}")
        if not isinstance(macro_status, dict) or "spool_id" not in macro_status:
            continue
        spool_id = coerce_spool_id(macro_status.get("spool_id"))
        if spool_id != previous_spool_by_tool.get(tool_index):
            changed[tool_index] = spool_id
    return changed


def config_already_matches(
    print_task_config: dict, physical_extruder: int, desired_args: dict[str, str]
) -> bool:
    checks = [
        _value_at(print_task_config.get(field, []), physical_extruder) == desired_args[arg]
        for arg, field in CONFIG_FIELD_BY_ARG.items()
        if arg in desired_args
    ]
    return bool(checks) and all(checks)


class PrintTaskBridge:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.http_client = self.server.lookup_component("http_client")
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        self.spoolman_url = _parse_spoolman_url(config)

        self.print_task: dict = {}
        self.print_state = ""
        self.last_seen_spool_by_tool: dict[int, int | None] = {}
        self.pending_by_tool: dict[int, int | None] = {}
        self.subscribed = False

        self.server.register_event_handler("server:klippy_ready", self._on_klippy_ready)
        logging.info(f"PrintTaskBridge loaded, server: {self.spoolman_url}")

    async def _on_klippy_ready(self) -> None:
        primed = await self._query_objects(SUBSCRIPTION_OBJECTS)
        self._absorb_print_task(primed)
        self.print_state = _state_of(primed)
        self.last_seen_spool_by_tool = changed_tool_spools({}, primed)
        if not self.subscribed:
            await self._subscribe(SUBSCRIPTION_OBJECTS, self._on_status_update)
            self.subscribed = True

    async def _on_status_update(self, status: dict, eventtime: float) -> None:
        self._absorb_print_task(status)
        await self._handle_state_transition(status)
        changed = changed_tool_spools(self.last_seen_spool_by_tool, status)
        self.last_seen_spool_by_tool.update(changed)
        for tool_index, spool_id in changed.items():
            await self._reconcile_tool(tool_index, spool_id)

    def _absorb_print_task(self, status: dict) -> None:
        task = status.get("print_task_config")
        if isinstance(task, dict):
            self.print_task.update(task)

    async def _handle_state_transition(self, status: dict) -> None:
        new_state = _state_of(status)
        if not new_state:
            return
        leaving_active = (
            self.print_state in ACTIVE_PRINT_STATES and new_state not in ACTIVE_PRINT_STATES
        )
        self.print_state = new_state
        if leaving_active:
            await self._drain_pending()

    async def _drain_pending(self) -> None:
        pending = self.pending_by_tool
        self.pending_by_tool = {}
        for tool_index, spool_id in pending.items():
            await self._reconcile_tool(tool_index, spool_id)

    async def _reconcile_tool(self, tool_index: int, spool_id: int | None) -> None:
        physical_extruder = self._writable_extruder(tool_index, spool_id)
        if physical_extruder is None:
            return
        if self.print_state in ACTIVE_PRINT_STATES:
            self.pending_by_tool[tool_index] = spool_id
            return
        await self._apply_spool(physical_extruder, spool_id)

    def _writable_extruder(self, tool_index: int, spool_id: int | None) -> int | None:
        if coerce_spool_id(spool_id) is None:
            return None
        physical = physical_extruder_for_tool(
            self.print_task.get("extruder_map_table", []), tool_index
        )
        if physical is None:
            return None
        if channel_is_official(self.print_task.get("filament_official", []), physical):
            return None
        return physical

    async def _apply_spool(self, physical_extruder: int, spool_id: int | None) -> None:
        spool = await self._fetch_spool(spool_id)
        if spool is None:
            return
        desired = filament_config_args_from_spool(spool, physical_extruder)
        if not has_filament_fields(desired):
            return
        if config_already_matches(self.print_task, physical_extruder, desired):
            return
        await self._run_gcode(set_print_filament_config_gcode(desired))

    async def _fetch_spool(self, spool_id: int | None) -> dict | None:
        url = f"{self.spoolman_url}/api/v1/spool/{spool_id}"
        response = await self.http_client.request(method="GET", url=url)
        if response.has_error():
            logging.warning(f"PrintTaskBridge spool fetch failed for {spool_id}: {response.error}")
            return None
        return response.json()

    async def _subscribe(self, objects: dict, callback: StatusCallback) -> None:
        await self.klippy_apis.subscribe_objects(objects, callback)

    async def _query_objects(self, objects: dict) -> dict:
        result = await self.klippy_apis.query_objects(objects)
        return result if isinstance(result, dict) else {}

    async def _run_gcode(self, script: str) -> None:
        await self.klippy_apis.run_gcode(script)


def _parse_spoolman_url(config: ConfigHelper) -> str:
    orig_url = config.get("server")
    url_match = re.match(r"(?i:(?P<scheme>https?)://)?(?P<host>.+)", orig_url)
    if url_match is None:
        raise config.error(f"[print_task_bridge] invalid server url: {orig_url}")
    scheme = url_match.group("scheme") or "http"
    host = url_match.group("host").rstrip("/")
    return f"{scheme}://{host}"


def _state_of(status: dict) -> str:
    stats = status.get("print_stats")
    if isinstance(stats, dict):
        return stats.get("state") or ""
    return ""


def load_component(config: ConfigHelper) -> PrintTaskBridge:
    return PrintTaskBridge(config)
