# ruff: noqa: PLR2004  Tests assert against literal spool ids and channel indexes.
"""SH_DUMP_SPOOLS: a lane names its real spool, then a manual pick, then UNKNOWN/empty."""
import types

from klipper_fakes import (
    REMAPPED_TABLE,
    FakePrintStats,
    FakePrintTaskConfig,
    FakeSdcard,
    write_remapped_gcode,
)
from spoolman.lane_report import LaneReport
from spoolman.spool_holders import SpoolHolders

TAGGED = {
    "VENDOR": "ELEGOO", "MAIN_TYPE": "PLA", "SUB_TYPE": "Matte",
    "ARGB_COLOR": "1D6C6AFF", "SPOOL_ID": 104, "SKU": "abc",
}
UNTAGGED = {"VENDOR": "NONE", "MAIN_TYPE": "NONE", "SPOOL_ID": None}


class RecordingLogs:
    def __init__(self):
        self.lines = []

    def _record(self, message):
        self.lines.append(message)

    log = warn = error = verbose = debug = _record


class FakeMacros:
    def clear_print_task_config(self, channel):
        pass


class FakeTracking:
    def __init__(self):
        self.spool_by_lane = {}

    def lane_spool(self, lane):
        return self.spool_by_lane.get(lane)


class FakeAfcLane:
    def __init__(self, filament_name):
        self.filament_name = filament_name


class FakePrinter:
    def __init__(self, objects=None):
        self.objects = objects or {}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


def build_report(afc_objects=None):
    logs = RecordingLogs()
    macros = FakeMacros()
    helper = types.SimpleNamespace(
        printer=FakePrinter(afc_objects),
        logs=logs,
        macros=macros,
        tracking=FakeTracking(),
        logging="info",
    )
    helper.holders = SpoolHolders(logs, macros, lambda channel: None, lambda channel: True)
    return LaneReport(helper), helper


def summary_line(helper, lane_label):
    return next(line for line in helper.logs.lines if line.startswith(f"{lane_label}:"))


def test_a_tagged_lane_reports_its_filament():
    report, helper = build_report()
    helper.holders.spool_holders[0] = TAGGED
    report.dump(raw=None)
    assert "ELEGOO PLA Matte" in summary_line(helper, "E0 (T0)")


def test_a_cached_manual_pick_reports_like_a_tagged_one():
    report, helper = build_report()
    helper.tracking.spool_by_lane[1] = 55
    helper.holders.spools_by_id[55] = TAGGED
    report.dump(raw=None)
    assert "ELEGOO PLA Matte" in summary_line(helper, "E1 (T1)")


def test_an_uncached_manual_pick_reads_the_afc_lane_label_back():
    report, helper = build_report({"AFC_lane E1": FakeAfcLane("ZIRO Silk Gold")})
    helper.tracking.spool_by_lane[1] = 55
    report.dump(raw=None)
    assert summary_line(helper, "E1 (T1)") == (
        "E1 (T1): Spoolman spool 55 (manually assigned): ZIRO Silk Gold"
    )


def test_an_uncached_manual_pick_without_afc_has_no_label_suffix():
    report, helper = build_report()
    helper.tracking.spool_by_lane[1] = 55
    report.dump(raw=None)
    assert summary_line(helper, "E1 (T1)") == "E1 (T1): Spoolman spool 55 (manually assigned)"


def test_loaded_but_unidentified_is_unknown_and_a_bare_lane_is_empty():
    report, helper = build_report()
    helper.holders.spool_holders[2] = UNTAGGED
    report.dump(raw=None)
    assert summary_line(helper, "E2 (T2)") == "E2 (T2): UNKNOWN (loaded, no tag)"
    assert summary_line(helper, "E3 (T3)") == "E3 (T3): empty"


# T0>E2, T1>E0, T5>E2: lane E2 carries the spool T0 and T5 both print with, and lane E1 feeds no
# tool in this print. Labelling each lane with its home tool named T2 a tool the print never uses.
def test_a_remapped_print_names_each_lane_by_the_tools_it_feeds(tmp_path):
    report, helper = build_report({
        "print_stats": FakePrintStats("printing"),
        "virtual_sdcard": FakeSdcard(write_remapped_gcode(tmp_path)),
        "print_task_config": FakePrintTaskConfig({"extruder_map_table": REMAPPED_TABLE}),
    })
    helper.tracking.spool_by_lane[2] = 55
    report.dump(raw=None)
    assert summary_line(helper, "E2 (T0, T5)") == (
        "E2 (T0, T5): Spoolman spool 55 (manually assigned)"
    )
    assert summary_line(helper, "E0 (T1)") == "E0 (T1): empty"
    assert summary_line(helper, "E1") == "E1: empty"
    assert summary_line(helper, "E3") == "E3: empty"


def test_raw_dump_prints_the_underlying_structures():
    report, helper = build_report()
    helper.holders.spool_holders[0] = TAGGED
    report.dump(raw="1")
    assert any("spool_holders:" in line and "spools_by_id:" in line for line in helper.logs.lines)
