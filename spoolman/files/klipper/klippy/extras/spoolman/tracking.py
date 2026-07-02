"""The single owner of Spoolman consumption tracking, in-process.

Reacts to the carrier watcher's transitions: a widget pick mirrors the spool onto the printer's
screen and AFC lane (deferred while printing: the firmware write is the risky part, the tracking
is not), a carrier mount/park recomputes the active spool (a mid-print park gap is transient and
never pushed; a settled carrier-empty clears), a pulled filament releases its lane, and print
end drains the deferred writes. Every user-visible change speaks through the helper's own Logs
library: this module never fabricates console output any other way.
"""
from .active_spool import (
    coerce_spool_id,
    physical_extruder_for_tool,
    resolve_active_spool,
)
from .carrier_watch import ACTIVE_PRINT_STATES, EXTRUDERS_COUNT
from .filament_info import filament_info_from_spoolman, filament_info_to_string

_ACTIVE_UNRESOLVED = object()
# A toolchange's park->pick gap is 1-3s of carrier-empty that means NOTHING is settled; an eject
# leaves the carrier empty for good. Only an empty that persists this long is a real "no active
# spool" (print end/cancel and all-spools-removed still clear immediately: those ARE settled).
CARRIER_EMPTY_SETTLE_SECONDS = 5.0


class SpoolTracking:
    def __init__(self, helper, track_location=False, location=""):
        self.helper = helper
        self.printer = helper.printer
        self.logs = helper.logs
        self.writer = helper.writer
        self.spoolman = helper.spoolman
        self.track_location = track_location and bool(location)
        self.location = location
        self.spool_by_tool = {}
        self.pending_by_tool = {}
        self.lane = None
        self.afc_present = False
        self.print_state = ""
        self.active_spool = _ACTIVE_UNRESOLVED
        self.carrier_empty_since = None

    def _extruder_map(self):
        task = self.printer.lookup_object("print_task_config", None)
        config = getattr(task, "print_task_config", None)
        table = config.get("extruder_map_table") if isinstance(config, dict) else None
        return table if isinstance(table, list) else list(range(EXTRUDERS_COUNT))

    def on_primed(self, spool_by_tool, lane, afc_present, state=""):
        self.spool_by_tool = dict(spool_by_tool)
        self.lane = lane
        self.afc_present = afc_present
        self.print_state = state
        if afc_present:
            self._reconcile_active()

    def on_pick(self, tool_index, spool_id, state):
        self.print_state = state
        previous = coerce_spool_id(self.spool_by_tool.get(tool_index))
        self.spool_by_tool[tool_index] = spool_id
        picked = coerce_spool_id(spool_id)
        self.helper.remember_manual_spool(tool_index, picked)
        self._track_location_change(previous, picked)
        self.helper.push_spool_to_afc(tool_index, picked)
        if state in ACTIVE_PRINT_STATES:
            self.pending_by_tool[tool_index] = picked
        else:
            self._write_screen(tool_index, picked)
        if self.afc_present:
            self._reconcile_active()
        elif picked is not None:
            # No carrier concept at all: a pick is the only signal, as on a single-extruder
            # printer where "selected" and "mounted" are the same thing.
            self._apply_active(picked)

    def on_mount_changed(self, lane, afc_present, state):
        self.lane = lane
        self.afc_present = afc_present or self.afc_present
        self.print_state = state
        self._reconcile_active()

    # A spool chosen in the AFC panel's own selection dialog (SET_SPOOL_ID -> a lane attribute
    # nothing else reads). Routed onto the lane's home tool macro so the ONE existing pick
    # cascade runs: screen write, Spoolman widget, location, active recompute. An RFID-identified
    # lane is left alone (the tag is the source of truth), and a value that already matches the
    # tool is the echo of our own push-back, not a user action.
    def on_afc_lane_pick(self, lane_index, spool_id, state):
        self.print_state = state
        picked = coerce_spool_id(spool_id)
        if self.helper.lane_is_tagged(lane_index):
            if picked is not None:
                self.logs.warn(
                    f"AFC pick ignored for T{lane_index}: lane has an RFID-identified spool"
                )
            return
        if picked == coerce_spool_id(self.spool_by_tool.get(lane_index)):
            return
        self.logs.log(f"AFC panel pick: spool {picked or 'none'} -> T{lane_index}")
        self.helper.macros.set_spool_id_for_tool(f"T{lane_index}", picked)

    # Mid-print a runout is the firmware's to handle; only an idle pull releases the lane's
    # manual assignment (an untagged spool fires no RFID event when removed).
    def on_filament_removed(self, extruder, state):
        if state in ACTIVE_PRINT_STATES:
            return
        extruder_map = self._extruder_map()
        for tool_index in range(EXTRUDERS_COUNT):
            mapped = physical_extruder_for_tool(extruder_map, tool_index)
            has_spool = coerce_spool_id(self.spool_by_tool.get(tool_index)) is not None
            if mapped == extruder and has_spool:
                self.logs.log(f"Filament pulled from extruder {extruder}, releasing T{tool_index}")
                self.helper.macros.set_spool_id_for_tool(f"T{tool_index}", None)

    # Only drains the deferred firmware writes: the print-end ACTIVE-SPOOL clear belongs to
    # print_lifecycle (via clear_active below), and a tool still mounted after a finished print
    # must NOT be re-applied here (a finished print consumes nothing).
    def on_print_left_active(self, lane, afc_present):
        self.lane = lane
        self.print_state = ""
        pending = self.pending_by_tool
        self.pending_by_tool = {}
        for tool_index, spool_id in pending.items():
            self._write_screen(tool_index, spool_id)

    # Every explicit clear (print end/cancel, CLEAR_ALL_SPOOLS) goes through here, never straight
    # to spoolman: a bypass leaves self.active_spool stale, and the next same-tool macro push
    # would be equal-skipped, losing tracking until the first toolchange.
    def clear_active(self):
        self.carrier_empty_since = None
        self._apply_active(None)

    # Called every watcher sample: drives the settle window while a carrier-empty is pending
    # (no transition fires while the carrier simply STAYS empty after an eject).
    def on_settle_tick(self):
        if self.carrier_empty_since is not None:
            self._reconcile_active()

    # The toolchange macro trigger (SET_ACTIVE_SPOOL, first line of every T-macro): applied
    # IMMEDIATELY, so the active spool lands slightly BEFORE physical pickup and the prime-tower
    # purge is attributed to the incoming filament. The park-detector recompute remains the
    # corrector: an eject settles to none, and a claim no mount ever confirms is corrected within
    # one watcher sample.
    def track_tool_spool(self, spool_id):
        picked = coerce_spool_id(spool_id)
        if picked is not None:
            self._apply_active(picked)

    def _reconcile_active(self):
        resolved = resolve_active_spool(self.lane, self.spool_by_tool, self._extruder_map())
        if resolved is not None:
            self.carrier_empty_since = None
            self._apply_active(resolved)
            return
        if self.print_state in ACTIVE_PRINT_STATES:
            return  # the toolchange park gap: transient, nothing extrudes while parked
        if self.active_spool in (None, _ACTIVE_UNRESOLVED):
            self.carrier_empty_since = None
            self._apply_active(None)
            return
        self._settle_carrier_empty()

    def _settle_carrier_empty(self):
        now = self.printer.get_reactor().monotonic()
        if self.carrier_empty_since is None:
            self.carrier_empty_since = now
            return
        if now - self.carrier_empty_since >= CARRIER_EMPTY_SETTLE_SECONDS:
            self.carrier_empty_since = None
            self._apply_active(None)

    def _apply_active(self, resolved):
        if resolved == self.active_spool and self.active_spool is not _ACTIVE_UNRESOLVED:
            return
        self.active_spool = resolved
        self.spoolman.set_active_spool(resolved)
        self.logs.log(f"Tracking: {self._spool_label(resolved)}")

    # The cache can be keyed str or int (a tag's SPOOL_ID field arrives as either), so both are
    # tried: a miss here degrades the log line to a bare id, which is exactly the bug it caused.
    def _spool_label(self, spool_id):
        if spool_id is None:
            return "no active spool"
        known = self.helper.spools_by_id.get(spool_id) or self.helper.spools_by_id.get(
            str(spool_id)
        )
        if known:
            return filament_info_to_string(known, self.helper.logging)
        return f"Spoolman spool {spool_id}"

    def _write_screen(self, tool_index, spool_id):
        extruder = physical_extruder_for_tool(self._extruder_map(), tool_index)
        if extruder is None:
            return
        if spool_id is None:
            self.writer.clear_extruder(extruder)
            return

        # The fetched spool doubles as label data: cached in the tag shape (never clobbering a
        # real tag's entry), a manual pick logs like a tagged one instead of "Spoolman spool N".
        def on_spool(spool, target_extruder=extruder, picked=spool_id):
            if spool:
                self.helper.spools_by_id.setdefault(picked, filament_info_from_spoolman(spool))
                self.writer.apply_spool(target_extruder, spool)

        self.spoolman.fetch_spool(spool_id, on_spool)

    def _track_location_change(self, previous, current):
        if not self.track_location:
            return
        if previous is not None and previous != current:
            self.spoolman.patch_location(previous, "")
        if current is not None:
            self.spoolman.patch_location(current, self.location)
