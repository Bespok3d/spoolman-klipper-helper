# ruff: noqa: PLR2004  Tests assert against literal spool ids and channel indexes.
"""The per-channel holder store: tag intake, the UNKNOWN-vs-empty decision, clears, merges."""
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


class RecordingMacros:
    def __init__(self):
        self.cleared_task_channels = []

    def clear_print_task_config(self, channel):
        self.cleared_task_channels.append(channel)


def build_holders(filament_present=(True, True, True, True)):
    logs = RecordingLogs()
    macros = RecordingMacros()
    released_lanes = []

    def lane_has_filament(channel):
        return filament_present[channel]

    holders = SpoolHolders(logs, macros, released_lanes.append, lane_has_filament)
    return holders, macros, released_lanes, logs


def test_tagged_report_is_stored_and_flagged_for_resolution():
    holders, _macros, _released_lanes, _logs = build_holders()
    assert holders.store_channel_report(2, TAGGED) is True
    assert holders.spool_holders[2] is TAGGED


def test_untagged_report_with_filament_present_is_unknown_loaded():
    holders, _macros, _released_lanes, logs = build_holders()
    assert holders.store_channel_report(1, UNTAGGED) is False
    assert holders.spool_holders[1] is UNTAGGED
    assert any("UNKNOWN filament" in line for line in logs.lines)


def test_untagged_report_on_a_bare_lane_is_empty_not_unknown():
    holders, _macros, _released_lanes, logs = build_holders(
        filament_present=(True, False, True, True)
    )
    assert holders.store_channel_report(1, UNTAGGED) is False
    assert holders.spool_holders[1] is None
    assert any("is empty" in line for line in logs.lines)


def test_out_of_range_channel_is_an_error_not_a_crash():
    holders, _macros, _released_lanes, logs = build_holders()
    assert holders.store_channel_report(7, TAGGED) is False
    assert any("Extruder must be" in line for line in logs.lines)


def test_forget_tag_drops_the_holder_and_leaves_the_pick():
    holders, macros, released_lanes, _logs = build_holders()
    holders.store_channel_report(2, dict(TAGGED))
    holders.forget_tag(2)
    assert holders.spool_holders[2] is None
    assert released_lanes == []
    assert macros.cleared_task_channels == []


def test_clear_channel_releases_the_lane_and_drops_its_cached_spool():
    holders, macros, released_lanes, _logs = build_holders()
    holders.store_channel_report(2, dict(TAGGED))
    holders.spools_by_id[104] = TAGGED
    holders.clear_channel(2)
    assert released_lanes == [2]
    assert holders.spool_holders[2] is None
    assert 104 not in holders.spools_by_id
    assert macros.cleared_task_channels == []  # firmware slot untouched without force


def test_clear_channel_force_also_wipes_the_firmware_slot():
    holders, macros, released_lanes, _logs = build_holders()
    holders.clear_channel(2, force=True)
    assert macros.cleared_task_channels == [2]
    assert released_lanes == [2]


def test_clear_channel_none_only_logs():
    holders, _macros, released_lanes, logs = build_holders()
    holders.clear_channel(None)
    assert released_lanes == []
    assert any("Clearing spool from extruder None" in line for line in logs.lines)


def test_lane_is_tagged():
    holders, _macros, _released_lanes, _logs = build_holders()
    holders.store_channel_report(0, dict(TAGGED))
    holders.store_channel_report(1, dict(UNTAGGED))
    assert holders.lane_is_tagged(0) is True
    assert holders.lane_is_tagged(1) is False  # loaded but unidentified
    assert holders.lane_is_tagged(3) is False  # empty
    assert holders.lane_is_tagged(9) is False  # out of range


def test_merge_detected_spools_enriches_only_existing_holders():
    holders, _macros, _released_lanes, _logs = build_holders()
    holders.spool_holders[0] = {"VENDOR": "ELEGOO", "SKU": None}
    detected = [
        {"VENDOR": None, "SKU": "abc"},   # None values never overwrite
        {"VENDOR": "Acme"},               # no holder on 1: detection alone creates nothing
    ]
    holders.merge_detected_spools(detected)
    assert holders.spool_holders[0] == {"VENDOR": "ELEGOO", "SKU": "abc"}
    assert holders.spool_holders[1] is None


def test_clear_ids_empties_the_shared_cache_in_place():
    holders, _macros, _released_lanes, _logs = build_holders()
    cache_alias = holders.spools_by_id
    holders.spools_by_id[104] = TAGGED
    holders.clear_ids()
    assert cache_alias == {}  # same dict object, emptied (live readers keep working)


def test_the_tag_on_a_lane_is_handed_back_only_for_a_tagged_lane():
    holders, _macros, _released_lanes, _logs = build_holders()
    holders.store_channel_report(2, TAGGED)
    holders.store_channel_report(1, UNTAGGED)
    assert holders.tag_on_lane(2) is TAGGED
    assert holders.tag_on_lane(1) is None
    assert holders.tag_on_lane(0) is None
