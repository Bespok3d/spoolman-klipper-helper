# ruff: noqa: PLR2004  Tests assert against literal spool ids and extruder indexes.
"""The tracking coordinator: the three active-spool rules end to end, deferral, release,
location, and the console voice going through the helper's own Logs (never fabricated)."""
from klipper_fakes import (
    REMAPPED_TABLE,
    FakePrintStats,
    FakePrintTaskConfig,
    FakeSdcard,
    write_remapped_gcode,
)
from spoolman.print_task_writer import PrintTaskWriter
from spoolman.tracking import SpoolTracking

SPOOL = {
    "id": 104,
    "filament": {"name": "PLA Basic", "material": "PLA", "color_hex": "2850E0",
                 "vendor": {"name": "Flashforge"}},
}
OTHER_SPOOL = {
    "id": 80,
    "filament": {"name": "PETG Tough", "material": "PETG", "color_hex": "E02828",
                 "vendor": {"name": "Polymaker"}},
}
SPOOLMAN_RECORDS = {104: SPOOL, 80: OTHER_SPOOL}


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
    def __init__(self):
        self.active_spool_calls = []
        self.location_calls = []

    def set_active_spool(self, spool_id):
        self.active_spool_calls.append(spool_id)

    def fetch_spool(self, spool_id, on_spool):
        on_spool(SPOOLMAN_RECORDS.get(spool_id))

    def patch_location(self, spool_id, location):
        self.location_calls.append((spool_id, location))


class LateAnsweringSpoolman(RecordingSpoolman):
    def __init__(self):
        super().__init__()
        self.unanswered_fetches = []

    def fetch_spool(self, spool_id, on_spool):
        self.unanswered_fetches.append((spool_id, on_spool))

    def answer_every_fetch(self):
        for spool_id, on_spool in self.unanswered_fetches:
            on_spool(SPOOLMAN_RECORDS.get(spool_id))


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
        self.tags_on_lanes = {}
        self.remembered = []

    def push_spool_to_afc(self, channel, spool_id):
        self.afc_pushes.append((channel, spool_id))

    def tag_on_lane(self, channel):
        return self.tags_on_lanes.get(channel)

    def lane_is_tagged(self, channel):
        return channel in self.tags_on_lanes

    def remember_manual_spool(self, lane, spool_id):
        self.remembered.append((lane, spool_id))


class FakeReactor:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class FakePrinter:
    def __init__(self, task_config, print_state="", gcode_path=None):
        self.task = FakePrintTaskConfig(task_config)
        self.print_stats = FakePrintStats(print_state)
        self.sdcard = FakeSdcard(gcode_path)
        self.reactor = FakeReactor()

    def lookup_object(self, name, default=None):
        objects = {
            "print_task_config": self.task,
            "print_stats": self.print_stats,
            "virtual_sdcard": self.sdcard,
        }
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


def build_tracking(
    extruder_map=None, track_location=False, location="", print_state="", gcode_path=None,
):
    printer = FakePrinter(task_config(extruder_map), print_state, gcode_path)
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
    tracking, spoolman, macros, _logs = build_tracking(print_state="printing")
    tracking.on_primed({}, "E2", afc_present=True)
    tracking.on_pick(2, 104, "printing")
    assert all(not cmd.startswith("SET_PRINT_FILAMENT_CONFIG") for cmd in macros.commands)
    tracking.printer.print_stats.state = "complete"
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
    tracking.set_lane_spool(2, 104)  # a tag bind names the lane's spool, then writes its config
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


def test_a_pick_on_a_tagged_lane_keeps_the_tags_richer_colours():
    # The tag on E2 names three colours for spool 104, Spoolman only one: the screen write keeps
    # all three instead of flattening the spool to the Spoolman colour.
    tracking, _spoolman, macros, _logs = build_tracking()
    tracking.helper.tags_on_lanes[2] = {
        "VENDOR": "Flashforge", "MAIN_TYPE": "PLA", "SUB_TYPE": "Basic", "SPOOL_ID": 104,
        "ARGB_COLOR": 0xFF112233, "ALPHA": 0xFF, "COLOR_NUMS": 3,
        "RGB_1": 0x112233, "RGB_2": 0x445566, "RGB_3": 0x778899,
    }
    tracking.on_primed({}, None, afc_present=True)
    tracking.on_pick(2, 104, "standby")
    assert any(cmd.startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2")
               and "COLORS=112233,445566,778899" in cmd for cmd in macros.commands)


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


def test_afc_panel_pick_lands_on_the_lanes_tool_outside_a_print():
    tracking, _spoolman, macros, logs = build_tracking()
    tracking.on_primed({}, None, afc_present=True)
    tracking.on_afc_lane_pick(0, 94, "standby")
    assert macros.tool_spool_sets == [("T0", 94)]
    assert tracking.helper.remembered == [(0, 94)]
    assert tracking.helper.afc_pushes == [(0, 94)]
    assert any("AFC panel pick" in line for line in logs.lines)


def test_afc_panel_pick_on_a_tagged_lane_is_ignored():
    tracking, _spoolman, macros, logs = build_tracking()
    tracking.helper.tags_on_lanes[2] = {"SPOOL_ID": 57, "MAIN_TYPE": "PLA"}
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


def build_remapped_print(tmp_path, print_state="printing"):
    return build_tracking(
        REMAPPED_TABLE, print_state=print_state, gcode_path=write_remapped_gcode(tmp_path)
    )


def test_a_pick_on_a_remapped_tool_lands_on_the_lane_it_is_fed_from(tmp_path):
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path)
    tracking.on_primed({}, None, afc_present=True, state="printing")
    tracking.on_pick(0, 104, "printing")
    assert tracking.lane_spool(2) == 104
    assert tracking.helper.afc_pushes == [(2, 104)]
    assert tracking.helper.remembered == [(2, 104)]
    assert macros.tool_spool_sets == [("T5", 104)]  # T5 shares E2
    assert tracking.pending_by_lane == {2: 104}
    tracking.printer.print_stats.state = "complete"
    tracking.on_print_left_active(None, True)
    filament_writes = [cmd for cmd in macros.commands if cmd.startswith("SET_PRINT_FILAMENT")]
    assert len(filament_writes) == 1
    assert filament_writes[0].startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2")
    assert "Flashforge" in filament_writes[0]


def test_the_watchers_echo_of_a_shared_lane_write_is_not_a_new_pick(tmp_path):
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path)
    tracking.on_primed({}, None, afc_present=True, state="printing")
    tracking.on_pick(0, 104, "printing")
    tracking.on_pick(5, 104, "printing")  # the watcher sees the T5 write the pick made
    assert tracking.helper.remembered == [(2, 104)]
    assert macros.tool_spool_sets == [("T5", 104)]


def test_a_pick_on_a_tool_the_print_does_not_use_touches_no_lane_and_is_undone(tmp_path):
    tracking, _spoolman, macros, logs = build_remapped_print(tmp_path)
    tracking.on_primed({}, None, afc_present=True, state="printing")
    tracking.on_pick(3, 55, "printing")
    assert tracking.lane_spool(0) is None
    assert tracking.helper.afc_pushes == []
    assert macros.tool_spool_sets == [("T3", None)]
    assert logs.lines == ["T3 is fed by no lane now: spool 55 ignored"]


def test_a_pick_on_a_tool_already_told_none_is_still_undone(tmp_path):
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path)
    tracking.on_primed({3: None}, None, afc_present=True, state="printing")
    tracking.on_pick(3, 55, "printing")
    assert macros.tool_spool_sets == [("T3", None)]


def test_the_watchers_echo_of_an_undone_pick_is_not_a_new_pick(tmp_path):
    tracking, _spoolman, macros, logs = build_remapped_print(tmp_path)
    tracking.on_primed({}, None, afc_present=True, state="printing")
    tracking.on_pick(3, 55, "printing")
    tracking.on_pick(3, None, "printing")
    assert macros.tool_spool_sets == [("T3", None)]
    assert len(logs.lines) == 1


def test_the_mounted_lane_tracks_the_spool_of_the_tool_fed_from_it(tmp_path):
    # T2 is not in the print: its old spool 31 says nothing about E2, which T0 feeds from.
    tracking, spoolman, macros, _logs = build_remapped_print(tmp_path)
    tracking.on_primed({0: 104, 2: 31}, "E2", afc_present=True, state="printing")
    assert spoolman.active_spool_calls == [104]
    assert macros.tool_spool_sets == [("T2", None)]


def test_a_map_change_tells_every_tool_the_spool_of_its_new_lane(tmp_path):
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path, print_state="standby")
    tracking.on_primed({0: 94, 1: 24, 2: 104, 3: 55}, None, afc_present=True)
    tracking.printer.print_stats.state = "printing"
    tracking.on_map_changed()
    assert sorted(macros.tool_spool_sets) == [
        ("T0", 104), ("T1", 94), ("T2", None), ("T3", None), ("T5", 104),
    ]
    assert tracking.lane_spool(2) == 104  # the lanes keep their spools


def test_a_tool_left_out_of_the_map_at_print_end_shows_no_spool(tmp_path):
    # During the print T5 showed E2's spool; once the home map is back no lane feeds T5.
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path)
    tracking.on_primed({0: 104, 1: 94, 5: 104}, None, afc_present=True, state="printing")
    tracking.printer.print_stats.state = "complete"
    tracking.on_map_changed()
    assert sorted(macros.tool_spool_sets) == [
        ("T0", 94), ("T1", None), ("T2", 104), ("T3", None), ("T5", None),
    ]


def test_a_restart_mid_print_lines_up_the_tools_sharing_a_lane(tmp_path):
    # T0 and T5 both feed from E2 but name different spools: E2 takes T0's, and T5 follows it.
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path)
    tracking.on_primed({0: 104, 5: 80}, None, afc_present=True, state="printing")
    assert tracking.lane_spool(2) == 104
    assert macros.tool_spool_sets == [("T5", 104)]


def test_a_lane_cleared_after_the_print_started_keeps_the_active_spool_through_the_print(tmp_path):
    # The print started after the last watcher event, so only the live state says it is running:
    # the tools the print feeds from E2 are cleared now, the firmware clear waits for the print end.
    tracking, spoolman, macros, _logs = build_remapped_print(tmp_path, print_state="standby")
    slot = tracking.printer.lookup_object("print_task_config").print_task_config
    slot["filament_vendor"][2] = "Flashforge"
    slot["filament_type"][2] = "PLA"
    tracking.on_primed({2: 104}, "E2", afc_present=True)
    tracking.printer.print_stats.state = "printing"
    tracking.set_lane_spool(2, None)
    assert macros.tool_spool_sets == [("T0", None), ("T5", None)]
    assert all(not cmd.startswith("SET_PRINT_FILAMENT_CONFIG") for cmd in macros.commands)
    tracking.printer.reactor.now += 60.0
    tracking.on_settle_tick()
    assert spoolman.active_spool_calls == [104]
    tracking.printer.print_stats.state = "complete"
    tracking.on_print_left_active(None, True)
    assert any(cmd.startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2")
               and 'VENDOR="NONE"' in cmd for cmd in macros.commands)


def test_a_tool_fed_by_no_lane_between_prints_is_cleared_at_priming():
    tracking, _spoolman, macros, _logs = build_tracking()
    tracking.on_primed({0: 94, 5: 80}, None, afc_present=True)
    assert tracking.lane_spool(0) == 94
    assert macros.tool_spool_sets == [("T5", None)]


def test_a_lane_set_before_the_first_sample_keeps_its_spool(tmp_path):
    # A tag bound mid print names E2's spool before the watcher's first sample; T0's value at that
    # sample is older and must not replace it.
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path)
    tracking.set_lane_spool(2, 104)
    tracking.on_primed({0: 31}, None, afc_present=True, state="printing")
    assert tracking.lane_spool(2) == 104
    assert macros.tool_spool_sets == [("T0", 104), ("T5", 104), ("T0", 104)]


def test_a_lane_cleared_before_the_first_sample_clears_its_tool():
    tracking, _spoolman, macros, _logs = build_tracking()
    tracking.set_lane_spool(1, None)
    assert macros.tool_spool_sets == [("T1", None)]


def test_afc_panel_pick_tells_every_tool_fed_from_the_lane(tmp_path):
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path)
    tracking.on_primed({}, None, afc_present=True, state="printing")
    tracking.on_afc_lane_pick(2, 80, "printing")
    assert sorted(macros.tool_spool_sets) == [("T0", 80), ("T5", 80)]
    assert tracking.helper.remembered == [(2, 80)]


def test_a_write_held_for_a_spool_the_lane_no_longer_holds_is_dropped(tmp_path):
    # A tag bound spool 104 to E2 mid print and its config write was held. Then T0, fed from E2,
    # was picked as spool 80: at print end only spool 80's config may land on E2.
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path)
    tracking.on_primed({}, None, afc_present=True, state="printing")
    tracking.set_lane_spool(2, 104)
    tracking.writer.apply_spool(2, SPOOL)
    tracking.on_pick(0, 80, "printing")
    tracking.printer.print_stats.state = "complete"
    tracking.on_print_left_active(None, True)
    filament_writes = [cmd for cmd in macros.commands if cmd.startswith("SET_PRINT_FILAMENT")]
    assert len(filament_writes) == 1
    assert filament_writes[0].startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2")
    assert "Polymaker" in filament_writes[0]


def test_a_spoolman_answer_for_a_spool_the_lane_no_longer_holds_is_dropped(tmp_path):
    # The print-end write for spool 104 waits on Spoolman; meanwhile T2 is picked as spool 80 on
    # the same lane. Only spool 80's config may land on E2.
    tracking, _spoolman, macros, _logs = build_remapped_print(tmp_path)
    spoolman = LateAnsweringSpoolman()
    tracking.spoolman = spoolman
    tracking.on_primed({}, None, afc_present=True, state="printing")
    tracking.on_pick(0, 104, "printing")
    tracking.printer.print_stats.state = "complete"
    tracking.on_print_left_active(None, True)
    tracking.on_pick(2, 80, "standby")
    spoolman.answer_every_fetch()
    filament_writes = [cmd for cmd in macros.commands if cmd.startswith("SET_PRINT_FILAMENT")]
    assert len(filament_writes) == 1
    assert filament_writes[0].startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2")
    assert "Polymaker" in filament_writes[0]
