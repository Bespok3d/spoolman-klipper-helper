# ruff: noqa: PLR2004  Tests assert against literal tool indexes.
"""The in-process watcher: transition detection and the baseline-not-transition first sample."""
from carrier_watch import (
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


class FakePrintStats:
    def __init__(self, state):
        self.state = state


class FakePrintTaskConfig:
    def __init__(self, exist):
        self.print_task_config = {"filament_exist": exist}


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

    def on_settle_tick(self):
        pass  # every-sample heartbeat for the settle window, not a transition


def build_watch(lane="E2", state="standby", exist=None, spool_ids=(None, None, None, None)):
    printer = FakePrinter()
    printer.objects["AFC"] = FakeAfc(lane)
    printer.objects["print_stats"] = FakePrintStats(state)
    printer.objects["print_task_config"] = FakePrintTaskConfig(exist or [True] * 4)
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
    assert kinds == ["primed", "removed", "pick", "mount"]


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
