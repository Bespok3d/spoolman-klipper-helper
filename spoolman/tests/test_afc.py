# ruff: noqa: PLR2004  Tests assert against literal spool ids and colors.
"""Regression tests for mirroring resolved spool ids onto AFC lanes."""
from afc import lane_spool_id, push_spool_to_afc

EXTRUDERS = 4


class FakeLane:
    def __init__(self):
        self.spool_id = None


class FakePrinter:
    def __init__(self, objects):
        self._objects = objects

    def lookup_object(self, name, default=None):
        return self._objects.get(name, default)


def test_push_sets_lane_spool_id_when_afc_present():
    lane = FakeLane()
    printer = FakePrinter({"AFC": object(), "AFC_lane E0": lane})
    push_spool_to_afc(printer, 0, 42, EXTRUDERS)
    assert lane.spool_id == 42


def test_push_clears_lane_spool_id():
    lane = FakeLane()
    lane.spool_id = 42
    printer = FakePrinter({"AFC": object(), "AFC_lane E1": lane})
    push_spool_to_afc(printer, 1, None, EXTRUDERS)
    assert lane.spool_id is None


def test_push_no_op_when_afc_absent():
    lane = FakeLane()
    printer = FakePrinter({"AFC_lane E0": lane})
    push_spool_to_afc(printer, 0, 42, EXTRUDERS)
    assert lane.spool_id is None


def test_push_no_op_when_lane_missing():
    printer = FakePrinter({"AFC": object()})
    push_spool_to_afc(printer, 0, 42, EXTRUDERS)


def test_push_no_op_when_channel_out_of_range():
    lane = FakeLane()
    printer = FakePrinter({"AFC": object(), "AFC_lane E9": lane})
    push_spool_to_afc(printer, 9, 42, EXTRUDERS)
    assert lane.spool_id is None


def test_lane_spool_id_reads_the_panel_pick():
    lane = FakeLane()
    lane.spool_id = 94
    printer = FakePrinter({"AFC_lane E2": lane})
    assert lane_spool_id(printer, 2) == 94


def test_lane_spool_id_is_empty_when_the_lane_is_missing():
    printer = FakePrinter({})
    assert lane_spool_id(printer, 0) is None
