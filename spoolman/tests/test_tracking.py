# ruff: noqa: PLR2004  Tests assert against literal spool ids and extruder indexes.
"""The tracking coordinator: the three active-spool rules end to end, deferral, release,
location, and the console voice going through the helper's own Logs (never fabricated)."""
from spoolman.print_task_writer import PrintTaskWriter
from spoolman.tracking import SpoolTracking

SPOOL = {
    "id": 104,
    "filament": {"name": "PLA Basic", "material": "PLA", "color_hex": "2850E0",
                 "vendor": {"name": "Flashforge"}},
}


class RecordingLogs:
    def __init__(self):
        self.lines = []

    def _record(self, message):
        self.lines.append(message)

    log = warn = error = verbose = debug = _record


class RecordingMacros:
    def __init__(self):
        self.commands = []
        self.tool_spool_sets = []

    def run(self, command, error_msg):
        self.commands.append(command)

    def set_spool_id_for_tool(self, tool, spool_id):
        self.tool_spool_sets.append((tool, spool_id))


class RecordingSpoolman:
    def __init__(self, spool_payload=SPOOL):
        self.spool_payload = spool_payload
        self.active_spool_calls = []
        self.location_calls = []

    def set_active_spool(self, spool_id):
        self.active_spool_calls.append(spool_id)

    def fetch_spool(self, spool_id, on_spool):
        on_spool(self.spool_payload)

    def patch_location(self, spool_id, location):
        self.location_calls.append((spool_id, location))


class FakeHelper:
    def __init__(self, printer, logs, macros, writer, spoolman):
        self.printer = printer
        self.logs = logs
        self.macros = macros
        self.writer = writer
        self.spoolman = spoolman
        self.spools_by_id = {}
        self.logging = "info"
        self.afc_pushes = []
        self.tagged_lanes = set()
        self.remembered = []

    def push_spool_to_afc(self, channel, spool_id):
        self.afc_pushes.append((channel, spool_id))

    def lane_is_tagged(self, channel):
        return channel in self.tagged_lanes

    def remember_manual_spool(self, tool_index, spool_id):
        self.remembered.append((tool_index, spool_id))


class FakePrintTaskConfig:
    def __init__(self, config):
        self.print_task_config = config


class FakeReactor:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class FakePrintStats:
    def __init__(self, state):
        self.state = state


class FakePrinter:
    def __init__(self, task_config, print_state=""):
        self.task = FakePrintTaskConfig(task_config)
        self.print_stats = FakePrintStats(print_state)
        self.reactor = FakeReactor()

    def lookup_object(self, name, default=None):
        objects = {"print_task_config": self.task, "print_stats": self.print_stats}
        return objects.get(name, default)

    def get_reactor(self):
        return self.reactor


def task_config(extruder_map=None):
    return {
        "extruder_map_table": extruder_map or [0, 1, 2, 3],
        "filament_vendor": ["NONE"] * 4,
        "filament_type": ["NONE"] * 4,
        "filament_sub_type": ["NONE"] * 4,
        "filament_color_rgba": ["FFFFFFFF"] * 4,
        "filament_official": [False] * 4,
    }


def build_tracking(extruder_map=None, track_location=False, location="", print_state=""):
    printer = FakePrinter(task_config(extruder_map), print_state)
    logs = RecordingLogs()
    macros = RecordingMacros()
    spoolman = RecordingSpoolman()
    writer = PrintTaskWriter(printer, logs, macros)
    helper = FakeHelper(printer, logs, macros, writer, spoolman)
    tracking = SpoolTracking(helper, track_location=track_location, location=location)
    return tracking, spoolman, macros, logs


def test_priming_with_afc_establishes_ground_truth_even_when_none():
    tracking, spoolman, _macros, _logs = build_tracking()
    tracking.on_primed({0: None, 1: None, 2: None, 3: None}, None, afc_present=True)
    assert spoolman.active_spool_calls == [None]


def test_rule_1_mounting_a_tool_activates_its_resolved_spool():
    tracking, spoolman, _macros, logs = build_tracking()
    tracking.on_primed({2: 104}, None, afc_present=True)
    tracking.on_mount_changed("E2", True, "standby")
    assert spoolman.active_spool_calls == [None, 104]
    assert any("Tracking:" in line for line in logs.lines)


def test_rule_2_park_gap_never_flaps_and_a_real_eject_settles_to_none():
    # THE regression report: an idle toolchange printed "no active spool" between park and pick
    # (57 -> none -> 57). The park gap is transient REGARDLESS of print state; only an empty
    # carrier that PERSISTS (a real eject) is "no active spool".
    tracking, spoolman, _macros, _logs = build_tracking()
    reactor = tracking.printer.reactor
    tracking.on_primed({2: 104, 3: 55}, "E2", afc_present=True)
    assert spoolman.active_spool_calls == [104]
    tracking.on_mount_changed(None, True, "standby")   # idle toolchange: park...
    tracking.on_settle_tick()
    tracking.on_mount_changed("E3", True, "standby")   # ...pick, 2s later
    assert spoolman.active_spool_calls == [104, 55]    # no none in between
    tracking.on_mount_changed(None, True, "standby")   # eject: carrier stays empty
    reactor.now += 6.0
    tracking.on_settle_tick()
    assert spoolman.active_spool_calls == [104, 55, None]  # settled clear, exactly once


def test_mid_print_park_is_suppressed_without_any_settle():
    tracking, spoolman, _macros, _logs = build_tracking()
    reactor = tracking.printer.reactor
    tracking.on_primed({2: 104}, "E2", afc_present=True)
    tracking.on_mount_changed(None, True, "printing")
    reactor.now += 60.0
    tracking.on_settle_tick()
    assert spoolman.active_spool_calls == [104]  # a long mid-print park never clears


def test_mid_print_tool_switch_tracks_live_with_one_push_per_mount():
    tracking, spoolman, _macros, _logs = build_tracking()
    tracking.on_primed({1: 24, 3: 55}, "E1", afc_present=True)
    assert spoolman.active_spool_calls == [24]
    tracking.on_mount_changed(None, True, "printing")   # park T1
    tracking.on_mount_changed("E3", True, "printing")   # mount T3
    assert spoolman.active_spool_calls == [24, 55]


def test_print_end_drains_writes_and_clear_active_resets_ground_truth():
    # THE stale-state regression: a print-end clear that bypasses tracking leaves active_spool
    # stale, and the next print starting with the SAME tool gets equal-skipped -- no consumption
    # tracked until its first toolchange. clear_active is the only legal clear.
    tracking, spoolman, macros, _logs = build_tracking()
    tracking.on_primed({}, "E2", afc_present=True)
    tracking.on_pick(2, 104, "printing")
    assert all(not cmd.startswith("SET_PRINT_FILAMENT_CONFIG") for cmd in macros.commands)
    tracking.on_print_left_active("E2", True)  # deferred firmware writes drain...
    assert any(cmd.startswith("SET_PRINT_FILAMENT_CONFIG") for cmd in macros.commands)
    tracking.clear_active()                    # ...and print_lifecycle clears through tracking
    assert spoolman.active_spool_calls[-1] is None
    tracking.track_tool_spool(104)             # next print starts with the same tool
    assert spoolman.active_spool_calls[-1] == 104  # NOT equal-skipped


def test_print_end_sends_the_filament_config_the_writer_held_during_the_print():
    # A tag read or a manual pick resolved mid print never reaches tracking's own deferral: the
    # writer is what holds that firmware write back, and print end is what lets it go. The
    # firmware resets the live extruder's pressure advance on every write it takes.
    tracking, _spoolman, macros, _logs = build_tracking(print_state="printing")
    tracking.writer.apply_spool(2, SPOOL)
    assert all(not cmd.startswith("SET_PRINT_FILAMENT_CONFIG") for cmd in macros.commands)
    tracking.printer.print_stats.state = "complete"
    tracking.on_print_left_active("E2", True)
    assert any(cmd.startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2")
               for cmd in macros.commands)


def test_a_mounted_tool_is_not_reapplied_when_the_print_ends():
    # A finished print consumes nothing: draining writes must not re-activate the spool of a
    # tool that happens to still sit on the carrier.
    tracking, spoolman, _macros, _logs = build_tracking()
    tracking.on_primed({2: 104}, "E2", afc_present=True)
    assert spoolman.active_spool_calls == [104]
    tracking.clear_active()
    tracking.on_print_left_active("E2", True)
    assert spoolman.active_spool_calls == [104, None]  # no 104 re-push


def test_idle_pick_writes_screen_name_and_afc():
    tracking, spoolman, macros, _logs = build_tracking()
    tracking.on_primed({}, None, afc_present=True)
    tracking.on_pick(2, 104, "standby")
    assert any(cmd.startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2")
               for cmd in macros.commands)
    assert any(cmd.startswith("SET_LANE_FILAMENT_NAME EXTRUDER=2") for cmd in macros.commands)
    assert tracking.helper.afc_pushes == [(2, 104)]
    assert tracking.helper.remembered == [(2, 104)]  # persisted for the next restart


def test_a_str_keyed_label_cache_entry_still_labels_the_spool():
    # A tag's SPOOL_ID arrives as str or int depending on its source; a str-keyed cache entry
    # must not degrade the log line to "Spoolman spool 24".
    tracking, _spoolman, _macros, logs = build_tracking()
    tracking.helper.spools_by_id["24"] = {
        "VENDOR": "Flashforge", "MAIN_TYPE": "PLA", "SUB_TYPE": "Basic",
        "ARGB_COLOR": "2850E0FF", "SPOOL_ID": 24, "SKU": "0",
    }
    tracking.on_primed({1: 24}, "E1", afc_present=True)
    assert any("Flashforge PLA Basic" in line for line in logs.lines)


def test_a_picked_spool_logs_like_a_tagged_one():
    # The fetched Spoolman spool doubles as label data: no more "Tracking: Spoolman spool 104".
    tracking, spoolman, _macros, logs = build_tracking()
    tracking.on_primed({}, None, afc_present=True)
    tracking.on_pick(2, 104, "standby")       # fetch caches the label shape
    tracking.on_mount_changed("E2", True, "standby")
    tracking_lines = [line for line in logs.lines if "Tracking:" in line]
    assert "Flashforge PLA PLA Basic" in tracking_lines[-1]
    assert "Spoolman id: 104" in tracking_lines[-1]


def test_clearing_a_pick_resets_the_slot():
    tracking, _spoolman, macros, _logs = build_tracking()
    slot = tracking.printer.lookup_object("print_task_config").print_task_config
    slot["filament_vendor"][2] = "Flashforge"
    slot["filament_type"][2] = "PLA"
    tracking.on_primed({2: 104}, None, afc_present=True)
    tracking.on_pick(2, None, "standby")
    assert any('VENDOR="NONE"' in cmd for cmd in macros.commands)


def test_without_afc_a_pick_is_the_active_spool():
    tracking, spoolman, _macros, _logs = build_tracking()
    tracking.on_primed({}, None, afc_present=False)
    tracking.on_pick(0, 80, "standby")
    assert spoolman.active_spool_calls == [80]


def test_filament_removed_releases_the_lane_only_when_idle():
    tracking, _spoolman, macros, _logs = build_tracking()
    tracking.on_primed({2: 104}, None, afc_present=True)
    tracking.on_filament_removed(2, "printing")
    assert macros.tool_spool_sets == []  # mid-print runout is the firmware's to handle
    tracking.on_filament_removed(2, "standby")
    assert macros.tool_spool_sets == [("T2", None)]


def test_location_is_stamped_on_pick_and_cleared_on_swap():
    tracking, spoolman, _macros, _logs = build_tracking(track_location=True, location="unU1")
    tracking.on_primed({}, None, afc_present=True)
    tracking.on_pick(2, 104, "standby")
    tracking.on_pick(2, 55, "standby")
    assert spoolman.location_calls == [(104, "unU1"), (104, ""), (55, "unU1")]


def test_macro_trigger_and_recompute_converge_without_double_set():
    tracking, spoolman, _macros, _logs = build_tracking()
    tracking.on_primed({2: 104}, None, afc_present=True)
    tracking.on_mount_changed("E2", True, "printing")
    tracking.track_tool_spool(104)  # the T-macro fires for the same mount
    assert spoolman.active_spool_calls == [None, 104]  # one push, no fight


def test_macro_trigger_applies_immediately_before_pickup():
    # The T-macro's SET_ACTIVE_SPOOL is the FIRST line of a toolchange: the active spool must
    # land at macro time (before physical pickup), so the prime-tower purge is attributed to the
    # incoming filament, never seconds later when the detector poll catches the mount.
    tracking, spoolman, _macros, _logs = build_tracking()
    tracking.on_primed({1: 24, 3: 55}, "E1", afc_present=True)
    assert spoolman.active_spool_calls == [24]
    tracking.track_tool_spool(55)  # T3 macro fires; E1 still mounted
    assert spoolman.active_spool_calls == [24, 55]


def test_a_macro_claim_no_mount_confirms_is_corrected_by_the_detector():
    tracking, spoolman, _macros, _logs = build_tracking()
    reactor = tracking.printer.reactor
    tracking.on_primed({0: 94, 2: 57}, "E2", afc_present=True)
    assert spoolman.active_spool_calls == [57]
    tracking.track_tool_spool(94)                     # spurious claim, nothing mounts
    tracking.on_mount_changed(None, True, "standby")  # carrier empty and STAYS empty
    reactor.now += 6.0
    tracking.on_settle_tick()
    assert spoolman.active_spool_calls == [57, 94, None]  # corrected once settled


def test_without_afc_the_macro_trigger_applies_directly():
    tracking, spoolman, _macros, _logs = build_tracking()
    tracking.on_primed({}, None, afc_present=False)
    tracking.track_tool_spool(80)
    assert spoolman.active_spool_calls == [80]


def test_afc_panel_pick_lands_on_the_lanes_home_tool():
    tracking, _spoolman, macros, logs = build_tracking()
    tracking.on_primed({}, None, afc_present=True)
    tracking.on_afc_lane_pick(0, 94, "standby")
    assert macros.tool_spool_sets == [("T0", 94)]
    assert tracking.helper.remembered == [(0, 94)]
    assert tracking.helper.afc_pushes == [(0, 94)]
    assert any("AFC panel pick" in line for line in logs.lines)


def test_afc_panel_pick_on_a_tagged_lane_is_ignored():
    tracking, _spoolman, macros, logs = build_tracking()
    tracking.helper.tagged_lanes.add(2)
    tracking.on_primed({2: 57}, None, afc_present=True)
    tracking.on_afc_lane_pick(2, 94, "standby")
    assert macros.tool_spool_sets == []
    assert any("ignored" in line for line in logs.lines)


def test_our_own_afc_pushback_echo_is_not_a_pick():
    # push_spool_to_afc writes the lane attribute we watch; the echoed value already matches the
    # tool, so it must not loop back into another macro-var write.
    tracking, _spoolman, macros, _logs = build_tracking()
    tracking.on_primed({0: 94}, None, afc_present=True)
    tracking.on_afc_lane_pick(0, 94, "standby")
    assert macros.tool_spool_sets == []
