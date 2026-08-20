"""Tier 1, read only: every bound lane publishes exactly what the shipped composer promises.

The expectation side runs the plugin's own print_task_writer over the live Spoolman record;
the published side reads the firmware's print_task_config and the AFC lane card. One string
everywhere is the promise under test.
"""
import expected_lane
import live_lanes
import pytest

MAX_PHYSICAL_EXTRUDERS = 8


def bound_lane_or_skip(printer, physical_extruder):
    if physical_extruder not in printer.lane_extruders():
        pytest.skip(f"no AFC lane E{physical_extruder} on this printer")
    spool_id = live_lanes.lane_spool_id(printer, physical_extruder)
    if not spool_id:
        pytest.skip(f"lane E{physical_extruder} has no Spoolman spool bound")
    return spool_id


@pytest.mark.parametrize("physical_extruder", range(MAX_PHYSICAL_EXTRUDERS))
def test_card_name_is_the_spool_name(
    printer, spoolman_records, helper_options, physical_extruder
):
    spool_id = bound_lane_or_skip(printer, physical_extruder)
    spool_record = spoolman_records.spool(spool_id)
    card_name = printer.lane_status(physical_extruder).get("filament_name")
    assert card_name == expected_lane.expected_card_name(spool_record, helper_options)


@pytest.mark.parametrize("physical_extruder", range(MAX_PHYSICAL_EXTRUDERS))
def test_writable_lane_firmware_matches_the_spool(
    printer, spoolman_records, helper_options, physical_extruder
):
    spool_id = bound_lane_or_skip(printer, physical_extruder)
    firmware_fields = live_lanes.lane_fields(printer, physical_extruder)
    if firmware_fields["official"] and not helper_options.spoolman_overrides_tag:
        pytest.skip(
            f"lane E{physical_extruder} is tag-owned (official)"
            " and spoolman_overrides_tag is off"
        )
    spool_record = spoolman_records.spool(spool_id)
    expected_view = expected_lane.expected_write_view(
        spool_record, physical_extruder, helper_options
    )
    assert live_lanes.published_view(printer, physical_extruder) == expected_view
