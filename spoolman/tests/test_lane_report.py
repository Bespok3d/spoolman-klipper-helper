# ruff: noqa: PLR2004  Tests assert against literal spool ids and channel indexes.
"""SH_DUMP_SPOOLS: a lane names its real spool, then a manual pick, then UNKNOWN/empty."""
import types

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
    def __init__(self):
        self.spool_id_by_tool = {}

    def get_spool_id_for_tool(self, tool_id):
        return self.spool_id_by_tool.get(tool_id)

    def set_spool_id_for_tool(self, tool, spool_id):
        pass

    def clear_print_task_config(self, channel):
        pass


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
        logging="info",
    )
    helper.holders = SpoolHolders(
        logs, macros, lambda channel, spool_id: None, lambda channel: True
    )
    return LaneReport(helper), helper


def summary_line(helper, channel):
    return next(line for line in helper.logs.lines if line.startswith(f"T{channel}:"))


def test_a_tagged_lane_reports_its_filament():
    report, helper = build_report()
    helper.holders.spool_holders[0] = TAGGED
    report.dump(raw=None)
    assert "ELEGOO PLA Matte" in summary_line(helper, 0)


def test_a_cached_manual_pick_reports_like_a_tagged_one():
    report, helper = build_report()
    helper.macros.spool_id_by_tool[1] = 55
    helper.holders.spools_by_id[55] = TAGGED
    report.dump(raw=None)
    assert "ELEGOO PLA Matte" in summary_line(helper, 1)


def test_an_uncached_manual_pick_reads_the_afc_lane_label_back():
    report, helper = build_report({"AFC_lane E1": FakeAfcLane("ZIRO Silk Gold")})
    helper.macros.spool_id_by_tool[1] = 55
    report.dump(raw=None)
    assert summary_line(helper, 1) == "T1: Spoolman spool 55 (manually assigned): ZIRO Silk Gold"


def test_an_uncached_manual_pick_without_afc_has_no_label_suffix():
    report, helper = build_report()
    helper.macros.spool_id_by_tool[1] = 55
    report.dump(raw=None)
    assert summary_line(helper, 1) == "T1: Spoolman spool 55 (manually assigned)"


def test_loaded_but_unidentified_is_unknown_and_a_bare_lane_is_empty():
    report, helper = build_report()
    helper.holders.spool_holders[2] = UNTAGGED
    report.dump(raw=None)
    assert summary_line(helper, 2) == "T2: UNKNOWN (loaded, no tag)"
    assert summary_line(helper, 3) == "T3: empty"


def test_raw_dump_prints_the_underlying_structures():
    report, helper = build_report()
    helper.holders.spool_holders[0] = TAGGED
    report.dump(raw="1")
    assert any("spool_holders:" in line and "spools_by_id:" in line for line in helper.logs.lines)
