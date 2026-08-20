"""Tier 2, mutating: SH_DETECT_SPOOLS re-reads every lane without wiping a manual pick.

The released 0.1.35 wipes a panel-picked spool on detect; the pending_picks fix (0.1.36)
preserves it. This test is red against an installed 0.1.35 and goes green once the fixed
plugin is on the printer.
"""
import live_lanes
import printer_wire
import pytest

pytestmark = pytest.mark.mutating

DETECT_SETTLE_TIMEOUT_SECONDS = 90.0


def _bound_now(printer, watched_lanes):
    return {
        extruder: live_lanes.lane_spool_id(printer, extruder) for extruder in watched_lanes
    }


def _restore_missing_picks(printer, bound_before):
    wiped_picks = {
        extruder: spool_id
        for extruder, spool_id in bound_before.items()
        if live_lanes.lane_spool_id(printer, extruder) != spool_id
    }
    for extruder, spool_id in wiped_picks.items():
        printer.run_gcode(
            f"SET_GCODE_VARIABLE MACRO=T{extruder} VARIABLE=spool_id VALUE={spool_id}"
        )
    if wiped_picks:
        printer_wire.wait_until(
            lambda: _bound_now(printer, wiped_picks) == wiped_picks,
            "the wiped spool picks to be restored",
            DETECT_SETTLE_TIMEOUT_SECONDS,
        )


@pytest.mark.timeout(300)
def test_detect_spools_keeps_every_bound_lane(idle_printer):
    printer = idle_printer
    bound_before = _bound_now(printer, live_lanes.bound_lane_extruders(printer))
    if not bound_before:
        pytest.skip("no lane has a spool bound; nothing to detect")
    try:
        printer.run_gcode("SH_DETECT_SPOOLS")
        printer_wire.wait_until(
            lambda: _bound_now(printer, bound_before) == bound_before,
            "every lane to re-report its spool after SH_DETECT_SPOOLS",
            DETECT_SETTLE_TIMEOUT_SECONDS,
        )
    finally:
        _restore_missing_picks(printer, bound_before)
