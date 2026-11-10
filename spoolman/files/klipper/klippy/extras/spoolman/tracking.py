"""The single owner of Spoolman consumption tracking, in-process.

Keeps one spool per lane (physical extruder) and reaches it through the live tool-to-extruder
map: a widget pick on a tool lands on the lane the map feeds that tool from, and every tool the
map feeds from that lane is told the same spool. The lane's spool is mirrored onto the printer's
screen and AFC lane (the screen write is deferred while printing: the firmware write is the risky
part, the tracking is not), a carrier mount/park recomputes the active spool (a mid-print park
gap is transient and never pushed; a settled carrier-empty clears), a map change tells every tool
the spool of the lane it is fed from now, a pulled filament releases its lane, and print end
drains the deferred writes. Every user-visible change speaks through the helper's own Logs
library: this module never fabricates console output any other way.
"""
from .active_spool import (
    coerce_spool_id,
    physical_extruder_for_tool,
    resolve_active_spool,
    tools_for_physical_extruder,
)
from .filament_info import filament_info_from_spoolman, filament_info_to_string
from .u1_tools import ACTIVE_PRINT_STATES, EXTRUDERS_COUNT, live_extruder_map, print_state

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
        self.spool_by_lane = {}
        self.pending_by_lane = {}
        self.primed = False
        self.lane = None
        self.afc_present = False
        self.print_state = ""
        self.active_spool = _ACTIVE_UNRESOLVED
        self.carrier_empty_since = None

    def _extruder_map(self):
        return live_extruder_map(self.printer)

    def lane_spool(self, lane):
        return self.spool_by_lane.get(lane)

    def on_primed(self, spool_by_tool, lane, afc_present, state=""):
        self.spool_by_tool = dict(spool_by_tool)
        self.lane = lane
        self.afc_present = afc_present
        self.print_state = state
        self._seed_lanes_from_tools()
        self._line_up_sampled_tools()
        self.primed = True
        if afc_present:
            self._reconcile_active()

    # A lane already set before the first sample (a restored pick, an early tag bind) keeps that
    # spool; every other lane starts from the first tool the map feeds from it that names one.
    def _seed_lanes_from_tools(self):
        extruder_map = self._extruder_map()
        for lane in range(EXTRUDERS_COUNT):
            self.spool_by_lane.setdefault(lane, self._first_tool_spool(extruder_map, lane))

    # A restart mid-print can find two tools on one lane naming different spools, and a tool left
    # from the last print can still name a spool while it is fed by no lane: every sampled tool is
    # told the spool of the lane it is fed from now.
    def _line_up_sampled_tools(self):
        extruder_map = self._extruder_map()
        for tool_index in list(self.spool_by_tool):
            lane = physical_extruder_for_tool(extruder_map, tool_index)
            self._tell_tool(tool_index, self.spool_by_lane.get(lane))

    def _first_tool_spool(self, extruder_map, lane):
        tool_spools = (
            coerce_spool_id(self.spool_by_tool.get(tool_index))
            for tool_index in tools_for_physical_extruder(extruder_map, lane)
        )
        return next((spool_id for spool_id in tool_spools if spool_id is not None), None)

    # The watcher reports every macro change, the helper's own writes included; a value the tool
    # was already told is that echo, not a pick.
    def on_pick(self, tool_index, spool_id, state):
        self.print_state = state
        picked = coerce_spool_id(spool_id)
        if picked == coerce_spool_id(self.spool_by_tool.get(tool_index)):
            return
        self.spool_by_tool[tool_index] = picked
        lane = physical_extruder_for_tool(self._extruder_map(), tool_index)
        if lane is None:
            self._undo_pick_on_a_tool_fed_by_no_lane(tool_index, picked)
            return
        self.pick_for_lane(lane, picked)

    # A tool the map feeds from no lane (one the print does not use, or T4 and up between prints)
    # has no spool to show, so a pick on it is undone, and said so.
    def _undo_pick_on_a_tool_fed_by_no_lane(self, tool_index, picked):
        if picked is not None:
            self.logs.warn(f"T{tool_index} is fed by no lane now: spool {picked} ignored")
        self._tell_tool(tool_index, None)

    # A pick by hand: from a tool, the AFC panel, a restored pick or a pulled filament. The whole
    # pick cascade runs.
    def pick_for_lane(self, lane, spool_id):
        picked = coerce_spool_id(spool_id)
        previous = self._store_lane_spool(lane, picked)
        self._after_pick(lane, previous, picked)

    # A bind, an unbind, a re-applied pick or a cleared lane: the pick cascade runs as it did when
    # the caller's own macro write came back through the watcher, so only for a real change once
    # the watcher is primed.
    def set_lane_spool(self, lane, spool_id):
        picked = coerce_spool_id(spool_id)
        previous = self._store_lane_spool(lane, picked)
        if self.primed and previous != picked:
            self._after_pick(lane, previous, picked)

    def _store_lane_spool(self, lane, picked):
        previous = self.spool_by_lane.get(lane)
        self.spool_by_lane[lane] = picked
        self._tell_tools_on_lane(self._extruder_map(), lane)
        self.helper.push_spool_to_afc(lane, picked)
        return previous

    def _tell_tools_on_lane(self, extruder_map, lane):
        lane_spool_id = self.spool_by_lane.get(lane)
        for tool_index in tools_for_physical_extruder(extruder_map, lane):
            self._tell_tool(tool_index, lane_spool_id)

    # Before the first sample a tool's value is unknown, not none: a cleared lane must still clear
    # its tools.
    def _tell_tool(self, tool_index, spool_id):
        known = tool_index in self.spool_by_tool
        if known and coerce_spool_id(self.spool_by_tool[tool_index]) == spool_id:
            return
        self.spool_by_tool[tool_index] = spool_id
        self.helper.macros.set_spool_id_for_tool(f"T{tool_index}", spool_id)

    def _after_pick(self, lane, previous, picked):
        self.helper.remember_manual_spool(lane, picked)
        self._track_location_change(previous, picked)
        self.print_state = print_state(self.printer)
        if self.print_state in ACTIVE_PRINT_STATES:
            self.pending_by_lane[lane] = picked
        else:
            self._write_screen(lane, picked)
        if self.afc_present:
            self._reconcile_active()
        elif picked is not None:
            # No carrier concept at all: a pick is the only signal, as on a single-extruder
            # printer where "selected" and "mounted" are the same thing.
            self._apply_active(picked)

    # A print sets the map at start and puts the home map back at its end, and auto replenish
    # moves a tool to another lane mid-print: each time, every tool is told the spool of the lane
    # it is fed from now, and a tool fed by no lane is told none. The lanes' spools do not move,
    # so the active spool stays as it was.
    def on_map_changed(self):
        extruder_map = self._extruder_map()
        for lane in self.spool_by_lane:
            self._tell_tools_on_lane(extruder_map, lane)
        for tool_index in self._tools_fed_by_no_lane(extruder_map):
            self._tell_tool(tool_index, None)

    def _tools_fed_by_no_lane(self, extruder_map):
        return [
            tool_index for tool_index in self.spool_by_tool
            if physical_extruder_for_tool(extruder_map, tool_index) is None
        ]

    def on_mount_changed(self, lane, afc_present, state):
        self.lane = lane
        self.afc_present = afc_present or self.afc_present
        self.print_state = state
        self._reconcile_active()

    # A spool chosen in the AFC panel's own selection dialog (SET_SPOOL_ID -> a lane attribute
    # nothing else reads), made a pick on that lane so the ONE existing pick cascade runs: tool
    # macros, screen write, Spoolman widget, location, active recompute. An RFID-identified lane
    # is left alone (the tag is the source of truth), and a value that already matches the lane
    # is the echo of our own push-back, not a user action.
    def on_afc_lane_pick(self, lane_index, spool_id, state):
        self.print_state = state
        picked = coerce_spool_id(spool_id)
        if self.helper.lane_is_tagged(lane_index):
            if picked is not None:
                self.logs.warn(
                    f"AFC pick ignored for E{lane_index}: lane has an RFID-identified spool"
                )
            return
        if picked == self.lane_spool(lane_index):
            return
        self.logs.log(f"AFC panel pick: spool {picked or 'none'} -> E{lane_index}")
        self.pick_for_lane(lane_index, picked)

    # Mid-print a runout is the firmware's to handle; only an idle pull releases the lane's
    # manual assignment (an untagged spool fires no RFID event when removed).
    def on_filament_removed(self, extruder, state):
        if state in ACTIVE_PRINT_STATES:
            return
        if self.lane_spool(extruder) is None:
            return
        self.logs.log(f"Filament pulled from extruder {extruder}, releasing lane E{extruder}")
        self.pick_for_lane(extruder, None)

    # Only drains what was held back while the print ran: the writer's filament config writes
    # first, then the deferred screen writes, so a spool picked by hand during the print has the
    # last word. The print-end ACTIVE-SPOOL clear belongs to print_lifecycle (via clear_active
    # below), and a tool still mounted after a finished print must NOT be re-applied here (a
    # finished print consumes nothing).
    def on_print_left_active(self, lane, afc_present):
        self.lane = lane
        self.print_state = ""
        self.writer.release_writes_held_during_print(self.helper.tag_on_lane, self.lane_spool)
        pending = self.pending_by_lane
        self.pending_by_lane = {}
        for pending_lane, spool_id in pending.items():
            self._write_screen(pending_lane, spool_id)

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
        resolved = resolve_active_spool(self.lane, self.spool_by_lane)
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

    def _write_screen(self, lane, spool_id):
        if spool_id is None:
            self.writer.clear_extruder(lane)
            return

        # The fetched spool doubles as label data: cached in the tag shape (never clobbering a
        # real tag's entry), a manual pick logs like a tagged one instead of "Spoolman spool N".
        # An answer that arrives after the lane changed spool belongs to a spool the lane no longer
        # holds and is dropped.
        def on_spool(spool, target_lane=lane, picked=spool_id):
            if spool and self.lane_spool(target_lane) == picked:
                self.helper.spools_by_id.setdefault(picked, filament_info_from_spoolman(spool))
                self.writer.apply_spool(target_lane, spool, self.helper.tag_on_lane(target_lane))

        self.spoolman.fetch_spool(spool_id, on_spool)

    def _track_location_change(self, previous, current):
        if not self.track_location:
            return
        if previous is not None and previous != current:
            self.spoolman.patch_location(previous, "")
        if current is not None:
            self.spoolman.patch_location(current, self.location)
