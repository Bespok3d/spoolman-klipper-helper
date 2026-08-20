"""Tier 2, mutating: switching priority to Spoolman takes over a tag-owned lane, then tag
ownership is fully restored.

With spoolman_overrides_tag on, the helper force-writes the firmware on official (RFID-tagged)
lanes, so the card and the slicer carry the Spoolman name; the forced write drops the tag's
official claim. The takeover fires on a tag resolution event, never on a bare restart (the
helper's apply path is event-driven), so the test triggers the tag re-read itself. The restore
flips the option back and re-reads every tag, then every lane field must match its pre-test
snapshot exactly.
"""
from collections import namedtuple

import expected_lane
import lane_state
import live_lanes
import printer_wire
import pytest

pytestmark = pytest.mark.mutating

OVERRIDES_OPTION = "spoolman_overrides_tag"
KLIPPY_READY_TIMEOUT_SECONDS = 240.0
LANE_REBIND_TIMEOUT_SECONDS = 120.0
HELPER_WRITE_TIMEOUT_SECONDS = 180.0
TAG_REREAD_TIMEOUT_SECONDS = 120.0

PriorityBench = namedtuple(
    "PriorityBench", "printer spoolman_records helper_options device_config_path"
)


def _device_helper_config_path(printer):
    # The cfg under the config root is a symlink into the plugin's own tree; editing the
    # resolved target keeps the plugin's symlink integration intact, where a Moonraker
    # upload would replace the link with a plain file.
    config_root = printer.config_root_path()
    resolved = printer.ssh(
        f"readlink -f {config_root}/{printer_wire.HELPER_CONFIG_MOONRAKER_PATH}"
    )
    return resolved.strip()


def _set_override(printer, device_config_path, enabled):
    override_value = "true" if enabled else "false"
    printer.ssh(
        f"sed -i 's/^{OVERRIDES_OPTION}:.*/{OVERRIDES_OPTION}: {override_value}/'"
        f" {device_config_path}"
    )


def _restart_klippy(printer):
    printer.restart_klippy()
    printer_wire.wait_until(
        lambda: printer.klippy_state() == "ready",
        "klippy to come back ready after RESTART",
        KLIPPY_READY_TIMEOUT_SECONDS,
    )


def _wait_for_rebind(printer, physical_extruder):
    printer_wire.wait_until(
        lambda: bool(live_lanes.lane_spool_id(printer, physical_extruder)),
        f"lane E{physical_extruder} to re-bind its spool after the restart",
        LANE_REBIND_TIMEOUT_SECONDS,
    )


def _lane_snapshots(printer, extruders):
    print_task = printer.print_task_config()
    return {
        extruder: lane_state.firmware_lane_fields(print_task, extruder)
        for extruder in extruders
    }


def _switch_to_spoolman(bench, tested_extruder):
    _set_override(bench.printer, bench.device_config_path, enabled=True)
    _restart_klippy(bench.printer)
    _wait_for_rebind(bench.printer, tested_extruder)
    # A restart alone never applies the override: the helper only writes on a tag resolution
    # event, so the takeover needs the tag re-read the restore path already uses.
    bench.printer.run_gcode(f"FILAMENT_DT_UPDATE CHANNEL={tested_extruder}")
    spool_record = bench.spoolman_records.spool(
        live_lanes.lane_spool_id(bench.printer, tested_extruder)
    )
    expected_view = expected_lane.expected_write_view(
        spool_record, tested_extruder, bench.helper_options
    )
    printer_wire.wait_until(
        lambda: live_lanes.published_view(bench.printer, tested_extruder) == expected_view,
        f"lane E{tested_extruder} firmware to follow Spoolman after the priority switch",
        HELPER_WRITE_TIMEOUT_SECONDS,
    )
    card_name = bench.printer.lane_status(tested_extruder).get("filament_name")
    assert card_name == expected_lane.expected_card_name(spool_record, bench.helper_options)
    takeover_fields = live_lanes.lane_fields(bench.printer, tested_extruder)
    assert not takeover_fields["official"], "a forced write must drop the tag's official claim"


def _all_official_again(printer, official_extruders):
    print_task = printer.print_task_config()
    return all(
        lane_state.firmware_lane_fields(print_task, extruder)["official"]
        for extruder in official_extruders
    )


def _restore_tag_ownership(bench, official_extruders):
    _set_override(bench.printer, bench.device_config_path, enabled=False)
    _restart_klippy(bench.printer)
    for extruder in official_extruders:
        _wait_for_rebind(bench.printer, extruder)
        bench.printer.run_gcode(f"FILAMENT_DT_UPDATE CHANNEL={extruder}")
    printer_wire.wait_until(
        lambda: _all_official_again(bench.printer, official_extruders),
        "every tag-owned lane to reclaim its official flag after the tag re-read",
        TAG_REREAD_TIMEOUT_SECONDS,
    )


@pytest.mark.timeout(900)
def test_spoolman_priority_takes_over_a_tagged_lane_and_restores(
    idle_printer, spoolman_records, helper_options
):
    printer = idle_printer
    if helper_options.spoolman_overrides_tag:
        pytest.skip("spoolman_overrides_tag is already on; nothing to switch")
    official_extruders = live_lanes.official_lane_extruders(printer)
    if not official_extruders:
        pytest.skip("no tag-owned (official) lane with a spool bound on this printer")
    before_views = _lane_snapshots(printer, official_extruders)
    bench = PriorityBench(
        printer, spoolman_records, helper_options, _device_helper_config_path(printer)
    )
    try:
        _switch_to_spoolman(bench, official_extruders[0])
    finally:
        _restore_tag_ownership(bench, official_extruders)
    assert _lane_snapshots(printer, official_extruders) == before_views
