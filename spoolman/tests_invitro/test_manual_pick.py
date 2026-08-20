"""Tier 2, mutating: a manual pick lands on the firmware, the AFC card, and the persisted file.

The pick is driven exactly as the printer panel drives it, then restored to the spool that
was picked before the test ran.
"""
import json

import expected_lane
import live_lanes
import printer_wire
import pytest

pytestmark = pytest.mark.mutating

PICK_SETTLE_TIMEOUT_SECONDS = 90.0


def _pick_spool(printer, tool_id, spool_id):
    # The panel sets the tool macro's spool_id variable; the bench maps tool n to extruder n.
    printer.run_gcode(f"SET_GCODE_VARIABLE MACRO=T{tool_id} VARIABLE=spool_id VALUE={spool_id}")


def _persisted_manual_pick(printer, tool_id):
    manual_spools_text = printer.config_file_text(printer_wire.MANUAL_SPOOLS_MOONRAKER_PATH)
    return json.loads(manual_spools_text).get(str(tool_id), 0)


def _donor_spool_id(printer, manual_extruder):
    donor_spool_ids = (
        live_lanes.lane_spool_id(printer, extruder)
        for extruder in live_lanes.bound_lane_extruders(printer)
        if extruder != manual_extruder
    )
    return next(donor_spool_ids, 0)


def _pick_and_settle(printer, spoolman_records, helper_options, physical_extruder, spool_id):
    spool_record = spoolman_records.spool(spool_id)
    expected_view = expected_lane.expected_write_view(
        spool_record, physical_extruder, helper_options
    )
    _pick_spool(printer, physical_extruder, spool_id)
    printer_wire.wait_until(
        lambda: live_lanes.published_view(printer, physical_extruder) == expected_view,
        f"lane E{physical_extruder} to publish spool {spool_id}",
        PICK_SETTLE_TIMEOUT_SECONDS,
    )
    return spool_record


@pytest.mark.timeout(420)
def test_manual_pick_reaches_firmware_card_and_disk(
    idle_printer, spoolman_records, helper_options
):
    printer = idle_printer
    manual_extruders = live_lanes.manual_writable_extruders(printer)
    if not manual_extruders:
        pytest.skip("no manual-writable lane with a spool bound on this printer")
    picked_extruder = manual_extruders[0]
    original_spool_id = live_lanes.lane_spool_id(printer, picked_extruder)
    donor_spool_id = _donor_spool_id(printer, picked_extruder)
    if not donor_spool_id:
        pytest.skip("no second bound lane to borrow a donor spool from")
    try:
        donor_record = _pick_and_settle(
            printer, spoolman_records, helper_options, picked_extruder, donor_spool_id
        )
        card_name = printer.lane_status(picked_extruder).get("filament_name")
        assert card_name == expected_lane.expected_card_name(donor_record, helper_options)
        assert _persisted_manual_pick(printer, picked_extruder) == donor_spool_id
    finally:
        _pick_and_settle(
            printer, spoolman_records, helper_options, picked_extruder, original_spool_id
        )
    assert _persisted_manual_pick(printer, picked_extruder) == original_spool_id
