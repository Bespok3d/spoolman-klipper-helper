# ruff: noqa: PLR2004  Tests assert against literal tool indexes.
"""The in-process watcher: transition detection and the baseline-not-transition first sample."""
from klipper_fakes import (
    REMAPPED_TABLE,
    FakePrintStats,
    FakePrintTaskConfig,
    FakeSdcard,
    write_remapped_gcode,
)
from spoolman.carrier_watch import (
    POLL_INTERVAL_SECONDS,
    CarrierWatch,
    changed_tools,
    removed_extruders,
)


class FakeReactor:
    def monotonic(self):
        return 0.0

    def register_timer(self, callback, when):
        pass


class FakeMacro:
    def __init__(self, spool_id):
        self.variables = {"spool_id": spool_id}


class FakeAfc:
    def __init__(self, lane):
        self.lane = lane

    def get_status(self, eventtime=None):
        return {"current_lane": self.lane}


class FakePrinter:
    def __init__(self):
        self.objects = {}
        self.reactor = FakeReactor()

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return self.reactor


class RecordingTracking:
    def __init__(self):
        self.calls = []
        self.logs = self

    def error(self, message):
        self.calls.append(("error", message))

    def warn(self, message):
        self.calls.append(("warn", message))

    def on_primed(self, spool_by_tool, lane, afc_present, state=""):
        self.calls.append(("primed", dict(spool_by_tool), lane, afc_present))

    def on_pick(self, tool_index, spool_id, state):
        self.calls.append(("pick", tool_index, spool_id, state))

    def on_mount_changed(self, lane, afc_present, state):
        self.calls.append(("mount", lane, afc_present, state))

    def on_afc_lane_pick(self, lane_index, spool_id, state):
        self.calls.append(("lane_pick", lane_index, spool_id, state))

    def on_filament_removed(self, extruder, state):
        self.calls.append(("removed", extruder, state))

    def on_print_left_active(self, lane, afc_present):
        self.calls.append(("left_active", lane, afc_present))

    def on_map_changed(self):
        self.calls.append(("map_changed",))

    def on_settle_tick(self):
        pass  # every-sample heartbeat for the settle window, not a transition


def build_watch(lane="E2", state="standby", exist=None, spool_ids=(None, None, None, None)):
    printer = FakePrinter()
    printer.objects["AFC"] = FakeAfc(lane)
    printer.objects["print_stats"] = FakePrintStats(state)
    printer.objects["print_task_config"] = FakePrintTaskConfig(
        {"filament_exist": exist or [True] * 4}
    )
    for tool_index, spool_id in enumerate(spool_ids):
        printer.objects[f"gcode_macro T{tool_index}"] = FakeMacro(spool_id)
    tracking = RecordingTracking()
    watch = CarrierWatch(printer, tracking)
    return watch, printer, tracking


def test_changed_tools():
    assert changed_tools({0: 80, 1: None}, {0: 80, 1: 24}) == {1: 24}
    assert changed_tools({}, {0: 80}) == {0: 80}
    assert changed_tools({0: 80}, {0: 80}) == {}


def test_removed_extruders():
    assert removed_extruders([True, True], [True, False]) == [1]
    assert removed_extruders([False, True], [True, True]) == []
    assert removed_extruders([True], []) == []


def test_first_sample_is_a_baseline_not_a_batch_of_transitions():
    watch, _printer, tracking = build_watch(spool_ids=(80, 24, None, None))
    watch._sample()
    kinds = [call[0] for call in tracking.calls]
    assert kinds == ["primed"]  # restored spool_ids are NOT fresh picks


def test_pick_mount_and_removal_transitions_fire_after_the_baseline():
    watch, printer, tracking = build_watch()
    watch._sample()
    printer.objects["gcode_macro T2"].variables["spool_id"] = 104
    printer.objects["AFC"].lane = "E3"
    printer.objects["print_task_config"].print_task_config["filament_exist"] = [True, True, True, False]  # noqa: E501
    watch._sample()
    kinds = [call[0] for call in tracking.calls]
    assert kinds == ["primed", "pick", "removed", "mount"]


class FakeAfcLane:
    def __init__(self, spool_id=None):
        self.spool_id = spool_id


def test_afc_panel_pick_fires_after_the_baseline():
    watch, printer, tracking = build_watch()
    printer.objects["AFC_lane E0"] = FakeAfcLane()
    watch._sample()
    printer.objects["AFC_lane E0"].spool_id = 94
    watch._sample()
    assert ("lane_pick", 0, 94, "standby") in tracking.calls


def test_afc_lane_spool_present_at_baseline_is_not_a_pick():
    watch, printer, tracking = build_watch()
    printer.objects["AFC_lane E0"] = FakeAfcLane(spool_id=94)
    watch._sample()
    watch._sample()
    assert all(call[0] != "lane_pick" for call in tracking.calls)
    assert watch.spool_by_lane[0] == 94


# Stands for T0 and T1 sharing lane E2: a pick on T0 retells T1 and the lane.
class LaneRetellingTracking(RecordingTracking):
    def __init__(self, printer):
        super().__init__()
        self.printer = printer

    def on_pick(self, tool_index, spool_id, state):
        super().on_pick(tool_index, spool_id, state)
        self.printer.objects["gcode_macro T1"].variables["spool_id"] = spool_id
        self.printer.objects["AFC_lane E2"].spool_id = spool_id


# T1 and E2 still show the helper's own write from before the T0 pick; reporting them would undo
# that pick.
def test_a_value_an_earlier_pick_of_the_same_sample_rewrote_is_not_reported_as_a_pick():
    watch, printer, _recording = build_watch(state="printing")
    printer.objects["AFC_lane E2"] = FakeAfcLane()
    tracking = LaneRetellingTracking(printer)
    watch.tracking = tracking
    watch._sample()
    printer.objects["gcode_macro T0"].variables["spool_id"] = 104
    printer.objects["gcode_macro T1"].variables["spool_id"] = 80
    printer.objects["AFC_lane E2"].spool_id = 80
    watch._sample()
    reported_picks = [call for call in tracking.calls if call[0] in ("pick", "lane_pick")]
    assert reported_picks == [("pick", 0, 104, "printing")]
    assert [call[0] for call in tracking.calls].count("warn") == 2


# The T0 pick made the helper tell T1 the same spool; putting T1 back to the spool it held before
# that is a new pick, not a value the watcher already saw.
def test_a_tool_put_back_to_its_value_from_before_a_helper_write_is_a_pick():
    watch, printer, _recording = build_watch(state="printing", spool_ids=(None, 80, None, None))
    printer.objects["AFC_lane E2"] = FakeAfcLane()
    tracking = LaneRetellingTracking(printer)
    watch.tracking = tracking
    watch._sample()
    printer.objects["gcode_macro T0"].variables["spool_id"] = 104
    watch._sample()
    printer.objects["gcode_macro T1"].variables["spool_id"] = 80
    watch._sample()
    reported_picks = [call for call in tracking.calls if call[0] == "pick"]
    assert reported_picks == [("pick", 0, 104, "printing"), ("pick", 1, 80, "printing")]


# Clearing T0 retells T1 as None; T1 cleared in the same sample with 0 names that same empty
# spool, so it is no clash to warn about.
def test_a_pick_naming_the_same_spool_another_way_is_not_dropped():
    watch, printer, _recording = build_watch(state="printing", spool_ids=(80, 80, None, None))
    printer.objects["AFC_lane E2"] = FakeAfcLane(spool_id=80)
    tracking = LaneRetellingTracking(printer)
    watch.tracking = tracking
    watch._sample()
    printer.objects["gcode_macro T0"].variables["spool_id"] = None
    printer.objects["gcode_macro T1"].variables["spool_id"] = 0
    watch._sample()
    reported_picks = [call for call in tracking.calls if call[0] == "pick"]
    assert reported_picks == [("pick", 0, None, "printing"), ("pick", 1, 0, "printing")]
    assert "warn" not in [call[0] for call in tracking.calls]


class FailingPickTracking(RecordingTracking):
    def on_pick(self, tool_index, spool_id, state):
        raise OSError("disk full")


# The sample is already taken as the new baseline, so a transition skipped after a failing pick
# would never be reported: here the print-end drain.
def test_a_failing_pick_is_reported_and_the_rest_of_its_sample_still_runs():
    watch, printer, _recording = build_watch(state="printing")
    tracking = FailingPickTracking()
    watch.tracking = tracking
    watch._sample()
    printer.objects["gcode_macro T0"].variables["spool_id"] = 104
    printer.objects["print_stats"].state = "standby"
    watch._sample()
    assert ("error", "carrier watch: on_pick: disk full") in tracking.calls
    assert ("left_active", "E2", True) in tracking.calls


def test_a_remapping_print_reports_a_map_change_and_a_pick_on_t5(tmp_path):
    watch, printer, tracking = build_watch(spool_ids=(None,) * 6)
    printer.objects["print_task_config"].print_task_config["extruder_map_table"] = REMAPPED_TABLE
    printer.objects["virtual_sdcard"] = FakeSdcard(write_remapped_gcode(tmp_path))
    watch._sample()
    printer.objects["print_stats"].state = "printing"
    printer.objects["gcode_macro T5"].variables["spool_id"] = 104
    watch._sample()
    picks_and_map_changes = [call for call in tracking.calls if call[0] in ("map_changed", "pick")]
    assert picks_and_map_changes == [("pick", 5, 104, "printing"), ("map_changed",)]


# A pick in a sample may be the echo of the helper's own write; the tracking can tell it apart only
# while the tool still holds that write, before a map change or a pulled filament retells it.
def test_picks_are_dispatched_before_the_map_change_and_the_pulled_filament_of_their_sample():
    watch, printer, tracking = build_watch(state="printing")
    task_config = printer.objects["print_task_config"].print_task_config
    watch._sample()
    printer.objects["gcode_macro T0"].variables["spool_id"] = 104
    printer.objects["AFC_lane E0"] = FakeAfcLane(spool_id=94)
    task_config["extruder_map_table"] = [2, 0, 0, 0]
    task_config["filament_exist"] = [True, True, False, True]
    watch._sample()
    kinds = [call[0] for call in tracking.calls]
    assert kinds == ["primed", "pick", "lane_pick", "map_changed", "removed"]


def test_auto_replenish_moving_a_tool_mid_print_reports_a_map_change(tmp_path):
    watch, printer, tracking = build_watch(state="printing", spool_ids=(None,) * 6)
    task_config = printer.objects["print_task_config"].print_task_config
    task_config["extruder_map_table"] = list(REMAPPED_TABLE)
    printer.objects["virtual_sdcard"] = FakeSdcard(write_remapped_gcode(tmp_path))
    watch._sample()
    task_config["extruder_map_table"][0] = 3
    watch._sample()
    assert tracking.calls[1:] == [("map_changed",)]  # the first sample is the baseline


def test_leaving_an_active_print_fires_drain():
    watch, printer, tracking = build_watch(state="printing")
    watch._sample()
    printer.objects["print_stats"].state = "standby"
    watch._sample()
    assert ("left_active", "E2", True) in tracking.calls


def test_a_sampling_error_is_reported_never_raised():
    watch, printer, tracking = build_watch()
    watch._sample()
    printer.objects["print_stats"] = object()  # state attr missing -> getattr default covers it

    class Exploding:
        @property
        def variables(self):
            raise RuntimeError("boom")

    printer.objects["gcode_macro T0"] = Exploding()
    assert watch._poll(10.0) == 10.0 + POLL_INTERVAL_SECONDS  # timer stays armed
    assert any(call[0] == "error" for call in tracking.calls)
