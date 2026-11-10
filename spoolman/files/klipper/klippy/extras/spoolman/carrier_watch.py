"""In-process watcher for the state the old Moonraker bridge used to subscribe to.

A reactor timer samples, four times a second: each tool macro's spool_id (a widget pick lands
there via SET_GCODE_VARIABLE and fires no event), AFC's current_lane (carrier mount/park), the
firmware's filament_exist flags (a manually picked spool fires no RFID event when pulled), the
tool-to-extruder map (a print sets it at start and auto replenish changes it mid-print), and
the print state. Transitions are reported to the helper's tracking coordinator. Polling keeps
this passive: no gcode is intercepted, no Moonraker round-trip exists, and an in-process read is
always live (no stale-subscription races).
"""

from .active_spool import coerce_spool_id
from .afc import lane_spool_id
from .u1_tools import (
    ACTIVE_PRINT_STATES,
    EXTRUDERS_COUNT,
    MAX_TOOLS_COUNT,
    live_extruder_map,
    print_state,
)

# 4 Hz: a sample is a handful of in-process attribute reads, and the mount signal (the park
# detector flipping at physical pickup) should reach Spoolman before the prime-tower purge
# starts extruding the new filament, not a second later.
POLL_INTERVAL_SECONDS = 0.25


def tool_macro(printer, tool_index):
    return printer.lookup_object(f"gcode_macro T{tool_index}", None)


def macro_spool_id(macro):
    return getattr(macro, "variables", {}).get("spool_id")


# A print can feed any of the 32 logical tools, so every T macro the config defines is watched;
# a tool with no macro has no pick to watch.
def tool_spool_ids(printer):
    tool_macros = (
        (tool_index, tool_macro(printer, tool_index)) for tool_index in range(MAX_TOOLS_COUNT)
    )
    return {
        tool_index: macro_spool_id(macro)
        for tool_index, macro in tool_macros
        if macro is not None
    }


def tool_spool_id(printer, tool_index):
    return macro_spool_id(tool_macro(printer, tool_index))


def changed_tools(previous, current):
    return {
        tool_index: spool_id
        for tool_index, spool_id in current.items()
        if spool_id != previous.get(tool_index)
    }


# current_lane is NOT an attribute: afc-lite computes it in get_status() from the lanes' park
# detectors (the extruder reporting ACTIVATE is mounted; none ACTIVATE means carrier empty).
def current_lane(printer):
    afc = printer.lookup_object("AFC", None)
    if afc is None:
        return None, False
    status = afc.get_status(None) if hasattr(afc, "get_status") else {}
    return status.get("current_lane"), True


# The AFC panel's spool selection runs SET_SPOOL_ID, which lands as a plain attribute on the
# AFC_lane object and nowhere else; watching it makes a panel pick a first-class pick.
def afc_lane_spool_ids(printer):
    spool_by_lane = {}
    for lane_index in range(EXTRUDERS_COUNT):
        lane = printer.lookup_object(f"AFC_lane E{lane_index}", None)
        if lane is not None:
            spool_by_lane[lane_index] = getattr(lane, "spool_id", None)
    return spool_by_lane


def filament_present(printer):
    task = printer.lookup_object("print_task_config", None)
    config = getattr(task, "print_task_config", None)
    exist = config.get("filament_exist") if isinstance(config, dict) else None
    return list(exist) if isinstance(exist, list) else []


def removed_extruders(previous, current):
    return [
        extruder for extruder, was_present in enumerate(previous)
        if was_present and extruder < len(current) and not current[extruder]
    ]


class CarrierWatch:
    def __init__(self, printer, tracking):
        self.printer = printer
        self.tracking = tracking
        self.reactor = printer.get_reactor()
        self.spool_by_tool = {}
        self.spool_by_lane = {}
        self.lane = None
        self.present = []
        self.state = ""
        self.extruder_map = ()
        self.primed = False

    def start(self):
        self.reactor.register_timer(
            self._poll, self.reactor.monotonic() + POLL_INTERVAL_SECONDS
        )

    def _poll(self, eventtime):
        try:
            self._sample()
        except Exception as sample_error:
            self.tracking.logs.error(f"carrier watch: {sample_error}")
        return eventtime + POLL_INTERVAL_SECONDS

    def _snapshot(self):
        lane, afc_present = current_lane(self.printer)
        return {
            "spool_by_tool": tool_spool_ids(self.printer),
            "spool_by_lane": afc_lane_spool_ids(self.printer),
            "lane": lane,
            "afc_present": afc_present,
            "present": filament_present(self.printer),
            "state": print_state(self.printer),
            "extruder_map": tuple(live_extruder_map(self.printer)),
        }

    # First sample is a BASELINE, never a batch of transitions: a restart must not read the
    # restored spool_ids as fresh picks or an empty first filament_exist read as removals.
    def _sample(self):
        snapshot = self._snapshot()
        if self.primed:
            self._dispatch_transitions(snapshot)
        else:
            self._prime(snapshot)
        self._absorb_spools()

    def _prime(self, snapshot):
        self.primed = True
        self._absorb(snapshot)
        self._tell_tracking(
            self.tracking.on_primed, snapshot["spool_by_tool"], snapshot["lane"],
            snapshot["afc_present"], snapshot["state"],
        )

    # The helper's own writes come back here as picks, and the tracking knows them for echoes only
    # while the tools and lanes still hold what it told them: the picks go first, before a map
    # change or a pulled filament of this same sample tells them anything new.
    def _dispatch_transitions(self, snapshot):
        state = snapshot["state"]
        leaving_active = state not in ACTIVE_PRINT_STATES and self.state in ACTIVE_PRINT_STATES
        picks = changed_tools(self.spool_by_tool, snapshot["spool_by_tool"])
        lane_picks = changed_tools(self.spool_by_lane, snapshot["spool_by_lane"])
        mount_changed = snapshot["lane"] != self.lane
        removed = removed_extruders(self.present, snapshot["present"])
        map_changed = snapshot["extruder_map"] != self.extruder_map
        self._absorb(snapshot)

        self._dispatch_picks(picks, lane_picks, state)
        if map_changed:
            self._tell_tracking(self.tracking.on_map_changed)
        for extruder in removed:
            self._tell_tracking(self.tracking.on_filament_removed, extruder, state)
        if mount_changed:
            self._tell_tracking(
                self.tracking.on_mount_changed, snapshot["lane"], snapshot["afc_present"], state
            )
        if leaving_active:
            self._tell_tracking(
                self.tracking.on_print_left_active, snapshot["lane"], snapshot["afc_present"]
            )
        self._tell_tracking(self.tracking.on_settle_tick)

    # The picks of one sample carry no order in time, so the first one dispatched on a lane holds
    # (tools by number, then the AFC panel's lanes): a later pick of that sample on a tool or lane
    # the first one already rewrote is stale.
    def _dispatch_picks(self, picks, lane_picks, state):
        for tool_index, spool_id in picks.items():
            live_spool_id = tool_spool_id(self.printer, tool_index)
            if self._still_holds(f"T{tool_index}", live_spool_id, spool_id):
                self._tell_tracking(self.tracking.on_pick, tool_index, spool_id, state)
        for lane_index, spool_id in lane_picks.items():
            live_spool_id = lane_spool_id(self.printer, lane_index)
            if self._still_holds(f"E{lane_index}", live_spool_id, spool_id):
                self._tell_tracking(self.tracking.on_afc_lane_pick, lane_index, spool_id, state)

    def _still_holds(self, holder_name, live_spool_id, picked_spool_id):
        if coerce_spool_id(live_spool_id) == coerce_spool_id(picked_spool_id):
            return True
        self.tracking.logs.warn(
            f"{holder_name}: pick of spool {picked_spool_id} dropped, "
            "another pick on the same lane at the same moment won"
        )
        return False

    # The sample is absorbed before the tracking hears it, so a transition that fails here is not
    # seen again: it is reported, and the rest of the sample still reaches the tracking.
    def _tell_tracking(self, transition, *transition_details):
        try:
            transition(*transition_details)
        except Exception as transition_error:
            self.tracking.logs.error(f"carrier watch: {transition.__name__}: {transition_error}")

    def _absorb(self, snapshot):
        self.lane = snapshot["lane"]
        self.present = snapshot["present"]
        self.state = snapshot["state"]
        self.extruder_map = snapshot["extruder_map"]

    # The tracking answers a sample in the same reactor turn and records every value it writes to
    # a tool or a lane. Reading them back after it answered keeps those writes from coming back as
    # picks, and keeps a user's pick of the value a tool held before them visible.
    def _absorb_spools(self):
        self.spool_by_tool = tool_spool_ids(self.printer)
        self.spool_by_lane = afc_lane_spool_ids(self.printer)
