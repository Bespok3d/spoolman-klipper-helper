"""Mirror a manually picked Spoolman spool's color and material into the U1 print task.

The filament NAME on the AFC panel resolves live from the tool macro's spool_id; color and
material do NOT. Both the AFC panel and the touchscreen read color/material from the firmware
print_task_config (by physical extruder). When a user picks a Spoolman spool for an untagged
lane there is nothing that writes that spool's color/material back, so those views stay stale.

This component stays passive: it OBSERVES the tool macros' spool_id via a Moonraker status
subscription (additive, never intercepting any gcode) and REACTS by issuing the firmware's own
SET_PRINT_FILAMENT_CONFIG. A pick writes the spool's colour/material; CLEARING a lane (spool_id
back to None, e.g. CLEAR_ALL_SPOOLS or filament removed) resets the slot to its empty defaults so
the screen and AFC panel stop showing the removed spool. RFID-tagged channels are left untouched
(the tag is the source of truth). A change made mid-print is deferred and applied when the print
leaves printing/paused.

It also keeps Spoolman's active spool in step with the SELECTED spool: picking a spool for a lane
(its tool spool_id set to a real id) makes it the active/tracked spool, and pulling the last spool
off the machine (every tool spool_id back to None, while no print runs) clears it. An unknown lane
(no spool_id) is never in the active-spool path.

Optionally (track_location + a configured location name) it stamps this printer's name into each
loaded spool's Spoolman `location` field and clears it on unload, so the Spoolman inventory shows
which printer a spool is on.

A manually picked spool on an untagged lane fires no RFID event when pulled, so the bridge watches
the firmware's filament_exist flag and RELEASES that lane (clears its spool_id) when its filament
leaves -- which cascades through the usual cleanup (location, slot, lane name).
"""
from __future__ import annotations

import base64
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
# The firmware's empty/cleared print_task_config slot (DEFAULT_PRINT_TASK_CONFIG in
# print_task_config.py): vendor/type/sub-type read "NONE", colour reads opaque white.
EMPTY_FIELD = "NONE"
EMPTY_COLOR_RGBA = "FFFFFFFF"
TRUTHY = ("true", "1", "on", "yes")

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


# A cleared lane resets the firmware slot to its empty defaults so the screen and AFC panel stop
# showing the spool that was just removed. All four fields are sent (FILAMENT_TYPE again needs its
# VENDOR + FILAMENT_SUBTYPE companions) so config_already_matches can short-circuit a no-op clear.
def filament_config_clear_args(physical_extruder: int) -> dict[str, str]:
    return {
        "CONFIG_EXTRUDER": str(physical_extruder),
        "VENDOR": EMPTY_FIELD,
        "FILAMENT_TYPE": EMPTY_FIELD,
        "FILAMENT_SUBTYPE": EMPTY_FIELD,
        "FILAMENT_COLOR_RGBA": EMPTY_COLOR_RGBA,
    }


def _strip_unsafe(value: str) -> str:
    return "".join(char for char in value if char not in GCODE_UNSAFE_CHARS)


def _format_arg(key: str, value: str) -> str:
    if key in QUOTED_ARGS:
        return f'{key}="{_strip_unsafe(value)}"'
    return f"{key}={value}"


def set_print_filament_config_gcode(args: dict[str, str]) -> str:
    pairs = " ".join(_format_arg(key, value) for key, value in args.items())
    return f"SET_PRINT_FILAMENT_CONFIG {pairs}"


# A vendor+name display label ("ZIRO Silk Gold") the AFC panel cannot compose itself: it shows the
# Spoolman filament.name alone, and only when the frontend can resolve the spool. Pushed to the lane
# so an untagged channel still gets the richer label; absent vendor or name just drops that part.
def composed_filament_name(spool: dict) -> str:
    filament = spool.get("filament") or {}
    vendor = (filament.get("vendor") or {}).get("name") or ""
    name = filament.get("name") or ""
    return " ".join(part for part in (vendor, name) if part)


# Base64 so a name with spaces survives Klipper's whitespace-splitting gcode parser (SET_SPOOL_ID
# only ever carries an int). Addressed by physical extruder, the lane_index the bridge already uses.
def set_lane_filament_name_gcode(physical_extruder: int, name: str) -> str:
    encoded = base64.b64encode(name.encode("utf-8")).decode("ascii")
    return f"SET_LANE_FILAMENT_NAME EXTRUDER={physical_extruder} NAME_B64={encoded}"


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
        self.configured_location = config.get("location", "").strip()
        self.location_name = self.configured_location
        self.track_location = str(config.get("track_location", "false")).strip().lower() in TRUTHY

        self.print_task: dict = {}
        self.print_state = ""
        self.last_seen_spool_by_tool: dict[int, int | None] = {}
        self.pending_by_tool: dict[int, int | None] = {}
        self.filament_present: list = []

        self.server.register_event_handler("server:klippy_ready", self._on_klippy_ready)
        logging.info(f"PrintTaskBridge loaded, server: {self.spoolman_url}")

    # Re-subscribe on EVERY klippy_ready: Moonraker clears all subscription callbacks on klippy
    # disconnect (klippy_apis._on_klippy_disconnect), so a once-only subscribe goes deaf after the
    # first Klipper restart. Re-prime from empty so the restored tool spool_ids reconcile against the
    # freshly-reset print_task_config (a Klipper restart zeroes print_task_config while the spool_ids
    # are restored). _apply_spool is idempotent (config_already_matches skips a no-op write).
    async def _on_klippy_ready(self) -> None:
        await self._resolve_location_name()
        await self._subscribe(SUBSCRIPTION_OBJECTS, self._on_status_update)
        primed = await self._query_objects(SUBSCRIPTION_OBJECTS)
        self.print_state = _state_of(primed)
        self.last_seen_spool_by_tool = {}
        self.filament_present = []
        await self._on_status_update(primed, 0.0)

    # The location name defaults to this printer's own display name -- the instance name the user set
    # in their frontend, which Moonraker stores in its database -- so the feature works with no config.
    # An explicit `location` config (e.g. set by the app) always wins.
    async def _resolve_location_name(self) -> None:
        if self.configured_location or not self.track_location:
            self.location_name = self.configured_location
            return
        self.location_name = await self._detected_instance_name()

    async def _detected_instance_name(self) -> str:
        database = self.server.lookup_component("database", None)
        if database is None:
            return ""
        for namespace in ("fluidd", "mainsail"):
            try:
                name = await database.get_item(namespace, "uiSettings.general.instanceName", "")
            except Exception:
                name = ""
            if isinstance(name, str) and name.strip():
                return name.strip()
        return ""

    async def _on_status_update(self, status: dict, eventtime: float) -> None:
        self._absorb_print_task(status)
        await self._handle_state_transition(status)
        await self._release_removed_filament(status)
        changed = changed_tool_spools(self.last_seen_spool_by_tool, status)
        await self._track_locations(changed)
        self.last_seen_spool_by_tool.update(changed)
        for tool_index, spool_id in changed.items():
            await self._reconcile_tool(tool_index, spool_id)
        self._handle_all_spools_removed(changed)

    # The active spool follows the SELECTED spool, not the carrier-mount: only a lane with a real
    # spool_id is in the active-spool path. Pulling the last spool off the machine (every tool
    # spool_id back to None) leaves the active spool stale, so it is cleared. Gated on a spool
    # actually having just been removed so a startup with nothing loaded does not race the helper
    # still populating the tools.
    def _handle_all_spools_removed(self, changed: dict[int, int | None]) -> None:
        a_spool_was_removed = any(spool_id is None for spool_id in changed.values())
        no_spools_left = all(
            spool_id is None for spool_id in self.last_seen_spool_by_tool.values()
        )
        if a_spool_was_removed and no_spools_left and self.print_state not in ACTIVE_PRINT_STATES:
            self._set_active_spool(None)

    def _set_active_spool(self, spool_id: int | None) -> None:
        spoolman = self.server.lookup_component("spoolman", None)
        if spoolman is not None:
            spoolman.set_active_spool(spool_id)

    # Stamp this printer's name into a loaded spool's Spoolman location so the inventory shows where
    # each spool physically is, and clear it on unload. Runs for every lane (tagged and manual), since
    # the bridge sees all tool spool_id changes. Opt-in via track_location + a configured name.
    async def _track_locations(self, changed: dict[int, int | None]) -> None:
        if not (self.track_location and self.location_name):
            return
        for tool_index, new_spool_id in changed.items():
            previous = coerce_spool_id(self.last_seen_spool_by_tool.get(tool_index))
            current = coerce_spool_id(new_spool_id)
            if previous is not None and previous != current:
                await self._set_spool_location(previous, "")
            if current is not None:
                await self._set_spool_location(current, self.location_name)

    async def _set_spool_location(self, spool_id: int, location: str) -> None:
        url = f"{self.spoolman_url}/api/v1/spool/{spool_id}"
        response = await self.http_client.request(
            method="PATCH", url=url, body={"location": location},
            headers={"Content-Type": "application/json"})
        if response.has_error():
            logging.warning(
                f"PrintTaskBridge location update failed for spool {spool_id}: {response.error}")

    # A manually picked spool on an untagged lane fires no RFID event when pulled, so its tool spool_id
    # (and therefore its Spoolman location, slot, and lane name) would stay stale. The firmware's
    # filament_exist flag does drop, so when a lane's filament leaves we RELEASE that lane's assignment
    # (clear its spool_id), which cascades through the normal None-transition cleanup. Skipped mid-print
    # (a runout is the firmware's to handle) and baselined on (re)start so a fresh read is not a removal.
    async def _release_removed_filament(self, status: dict) -> None:
        task = status.get("print_task_config")
        exist = task.get("filament_exist") if isinstance(task, dict) else None
        if not isinstance(exist, list):
            return
        if self.print_state not in ACTIVE_PRINT_STATES:
            for physical_extruder, present in enumerate(exist):
                if self._was_present(physical_extruder) and not present:
                    await self._release_extruder(physical_extruder)
        self.filament_present = list(exist)

    def _was_present(self, physical_extruder: int) -> bool:
        return (
            physical_extruder < len(self.filament_present)
            and bool(self.filament_present[physical_extruder])
        )

    async def _release_extruder(self, physical_extruder: int) -> None:
        map_table = self.print_task.get("extruder_map_table", [])
        for tool_index in range(PHYSICAL_EXTRUDER_COUNT):
            mapped = _value_at(map_table, tool_index)
            has_spool = coerce_spool_id(self.last_seen_spool_by_tool.get(tool_index)) is not None
            if mapped == physical_extruder and has_spool:
                await self._run_gcode(
                    f"SET_GCODE_VARIABLE MACRO=T{tool_index} VARIABLE=spool_id VALUE=None")

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
        physical_extruder = self._writable_extruder(tool_index)
        if physical_extruder is None:
            return
        if self.print_state in ACTIVE_PRINT_STATES:
            self.pending_by_tool[tool_index] = spool_id
            return
        resolved_spool_id = coerce_spool_id(spool_id)
        if resolved_spool_id is None:
            await self._clear_extruder(physical_extruder)
            return
        # Picking a spool for a lane makes it the tracked spool: a lane with a real spool_id is the
        # thing Spoolman should account against, whether or not its tool is mounted on the carrier.
        self._set_active_spool(resolved_spool_id)
        await self._apply_spool(physical_extruder, spool_id)

    def _writable_extruder(self, tool_index: int) -> int | None:
        physical = physical_extruder_for_tool(
            self.print_task.get("extruder_map_table", []), tool_index
        )
        if physical is None:
            return None
        if channel_is_official(self.print_task.get("filament_official", []), physical):
            return None
        return physical

    async def _clear_extruder(self, physical_extruder: int) -> None:
        desired = filament_config_clear_args(physical_extruder)
        if config_already_matches(self.print_task, physical_extruder, desired):
            return
        await self._run_gcode(set_print_filament_config_gcode(desired))
        await self._run_gcode(set_lane_filament_name_gcode(physical_extruder, ""))

    async def _apply_spool(self, physical_extruder: int, spool_id: int | None) -> None:
        spool = await self._fetch_spool(spool_id)
        if spool is None:
            return
        desired = filament_config_args_from_spool(spool, physical_extruder)
        if not has_filament_fields(desired):
            return
        if not config_already_matches(self.print_task, physical_extruder, desired):
            await self._run_gcode(set_print_filament_config_gcode(desired))
        # Push the lane name even when the persisted config already matched, so a re-pick after a
        # restart (which clears the AFC lane's name) re-labels the lane.
        await self._apply_filament_name(physical_extruder, spool)

    async def _apply_filament_name(self, physical_extruder: int, spool: dict) -> None:
        name = composed_filament_name(spool)
        if not name:
            return
        await self._run_gcode(set_lane_filament_name_gcode(physical_extruder, name))

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
