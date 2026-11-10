"""In-process watcher for the state the old Moonraker bridge used to subscribe to.

A reactor timer samples, once a second: each tool macro's spool_id (a widget pick lands there
via SET_GCODE_VARIABLE and fires no event), AFC's current_lane (carrier mount/park), the
firmware's filament_exist flags (a manually picked spool fires no RFID event when pulled), and
the print state. Transitions are reported to the helper's tracking coordinator. Polling keeps
this passive: no gcode is intercepted, no Moonraker round-trip exists, and an in-process read is
always live (no stale-subscription races).
"""

from .u1_tools import EXTRUDERS_COUNT

# 4 Hz: a sample is a handful of in-process attribute reads, and the mount signal (the park
# detector flipping at physical pickup) should reach Spoolman before the prime-tower purge
# starts extruding the new filament, not a second later.
POLL_INTERVAL_SECONDS = 0.25
ACTIVE_PRINT_STATES = ("printing", "paused")


def tool_spool_ids(printer):
    spool_by_tool = {}
    for tool_index in range(EXTRUDERS_COUNT):
        macro = printer.lookup_object(f"gcode_macro T{tool_index}", None)
        variables = getattr(macro, "variables", {}) if macro is not None else {}
        spool_by_tool[tool_index] = variables.get("spool_id")
    return spool_by_tool


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


def print_state(printer):
    stats = printer.lookup_object("print_stats", None)
    return getattr(stats, "state", "") or ""


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
        }

    # First sample is a BASELINE, never a batch of transitions: a restart must not read the
    # restored spool_ids as fresh picks or an empty first filament_exist read as removals.
    def _sample(self):
        snapshot = self._snapshot()
        if not self.primed:
            self.primed = True
            self._absorb(snapshot)
            self.tracking.on_primed(
                snapshot["spool_by_tool"], snapshot["lane"],
                snapshot["afc_present"], snapshot["state"],
            )
            return
        self._dispatch_transitions(snapshot)

    def _dispatch_transitions(self, snapshot):
        state = snapshot["state"]
        leaving_active = state not in ACTIVE_PRINT_STATES and self.state in ACTIVE_PRINT_STATES
        picks = changed_tools(self.spool_by_tool, snapshot["spool_by_tool"])
        lane_picks = changed_tools(self.spool_by_lane, snapshot["spool_by_lane"])
        mount_changed = snapshot["lane"] != self.lane
        removed = removed_extruders(self.present, snapshot["present"])
        self._absorb(snapshot)

        for extruder in removed:
            self.tracking.on_filament_removed(extruder, state)
        for tool_index, spool_id in picks.items():
            self.tracking.on_pick(tool_index, spool_id, state)
        for lane_index, spool_id in lane_picks.items():
            self.tracking.on_afc_lane_pick(lane_index, spool_id, state)
        if mount_changed:
            self.tracking.on_mount_changed(snapshot["lane"], snapshot["afc_present"], state)
        if leaving_active:
            self.tracking.on_print_left_active(snapshot["lane"], snapshot["afc_present"])
        self.tracking.on_settle_tick()

    def _absorb(self, snapshot):
        self.spool_by_tool = snapshot["spool_by_tool"]
        self.spool_by_lane = snapshot["spool_by_lane"]
        self.lane = snapshot["lane"]
        self.present = snapshot["present"]
        self.state = snapshot["state"]
