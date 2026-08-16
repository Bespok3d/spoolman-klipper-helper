# ruff: noqa: PLR2004  Tests assert against literal spool ids and channel indexes.
"""Resolving holders to Spoolman ids, the tool->spool queries, and the UID-bind entry."""
import json
import types

from klipper_fakes import (
    FakePrinter,
    RecordingLogs,
    RecordingWebhooks,
    drain_timers,
    helper_options,
)
from spoolman import card_uids
from spoolman.print_task_writer import slicer_filament_description
from spoolman.spool_holders import SpoolHolders
from spoolman.spool_resolution import SpoolResolution
from spoolman.spoolman import Spoolman
from spoolman.unmatched_tag import REGISTER_REFUSED

TAGGED = {
    "VENDOR": "ELEGOO", "MAIN_TYPE": "PLA", "SUB_TYPE": "Matte",
    "ARGB_COLOR": "1D6C6AFF", "SPOOL_ID": 104, "SKU": "abc",
}
REGISTERS_A_NEW_SPOOL = (777, None)
REFUSES_TO_REGISTER = (None, REGISTER_REFUSED)
SPOOLMAN_SPOOL = {
    "id": 104,
    "filament": {"name": "PLA Matte", "material": "PLA", "color_hex": "1D6C6A",
                 "vendor": {"name": "ELEGOO"}},
}


class RecordingMacros:
    def __init__(self):
        self.tool_spool_sets = []
        self.spool_id_by_tool = {}

    def set_spool_id_for_tool(self, tool, spool_id):
        self.tool_spool_sets.append((tool, spool_id))

    def get_spool_id_for_tool(self, tool_id):
        return self.spool_id_by_tool.get(tool_id)

    def clear_print_task_config(self, channel):
        pass


class RecordingSpoolman:
    def __init__(self, *, resolve_result=104, spoolman_unanswered=False, candidates=(),
                 registration=REGISTERS_A_NEW_SPOOL, uid_bind_holds=True):
        self.resolve_result = resolve_result
        self.spoolman_unanswered = spoolman_unanswered
        self.candidates = candidates
        self.registers_as, self.registration_problem = registration
        self.uid_bind_holds = uid_bind_holds
        self.resolved_infos = []
        self.searched_infos = []
        self.bound_uids = []
        self.registered_infos = []
        self.asked_by_hand = []
        self.applied_to_spools = []
        self.fetched_spool_ids = []
        self.created_spool = None

    def resolve_spool(self, info, callback):
        self.resolved_infos.append(info)
        callback(self.resolve_result, self.spoolman_unanswered)

    def search_candidates(self, info, on_candidates):
        self.searched_infos.append(info)
        on_candidates(None if self.candidates is None else list(self.candidates))

    def fetch_spool(self, spool_id, on_spool):
        self.fetched_spool_ids.append(spool_id)
        on_spool(SPOOLMAN_SPOOL)

    def bind_uid(self, spool_id, uid, on_done=None):
        self.bound_uids.append((spool_id, uid))
        if on_done:
            on_done(self.uid_bind_holds)

    def register_tag_as_spool(self, info, on_registered, asked_by_hand=False):
        self.registered_infos.append(info)
        self.asked_by_hand.append(asked_by_hand)
        on_registered(self.registers_as, self.registration_problem, self.created_spool)

    def apply_tag_to_spool(self, info, spool_id, on_applied):
        self.applied_to_spools.append((info, spool_id))
        on_applied(self.registration_problem)


class RecordingWriter:
    def __init__(self):
        self.applied_spools = []
        self.labelled_lanes = []
        self.blanked_lanes = []

    def apply_spool(self, extruder, spool):
        self.applied_spools.append((extruder, spool))
        return self.label_lane(extruder, spool)

    def label_lane(self, extruder, spool):
        self.labelled_lanes.append((extruder, spool))
        return slicer_filament_description(spool)

    def clear_lane_label(self, extruder):
        self.blanked_lanes.append(extruder)


class RecordingTracking:
    def __init__(self):
        self.tracked_spools = []

    def track_tool_spool(self, spool_id):
        self.tracked_spools.append(spool_id)


class FakeU1Tools:
    def __init__(self, extruder_by_tool):
        self.extruder_by_tool = extruder_by_tool

    def extruder_for_tool(self, tool_id):
        return self.extruder_by_tool.get(tool_id)


def build_resolution(mode="auto", extruder_by_tool=None, **spoolman_traits):
    logs = RecordingLogs()
    macros = RecordingMacros()
    afc_pushes = []
    helper = types.SimpleNamespace(
        logs=logs,
        macros=macros,
        spoolman=RecordingSpoolman(**spoolman_traits),
        u1_tools=FakeU1Tools(extruder_by_tool or {0: 0, 1: 1, 2: 2, 3: 3}),
        writer=RecordingWriter(),
        tracking=RecordingTracking(),
        mode=mode,
        logging="info",
        push_spool_to_afc=lambda channel, spool_id: afc_pushes.append((channel, spool_id)),
    )
    helper.holders = SpoolHolders(
        logs, macros, helper.push_spool_to_afc, lambda channel: True
    )
    resolution = SpoolResolution(helper)
    return resolution, helper, afc_pushes


def test_apply_binds_and_mirrors_a_resolved_spool_everywhere():
    resolution, helper, afc_pushes = build_resolution()
    helper.holders.spool_holders[2] = dict(TAGGED)
    resolution.apply_spool_for_extruder(2)
    assert helper.holders.spools_by_id[104] == dict(TAGGED)
    assert helper.macros.tool_spool_sets == [("T2", 104)]
    assert afc_pushes == [(2, 104)]
    assert helper.writer.applied_spools == [(2, SPOOLMAN_SPOOL)]
    assert helper.writer.labelled_lanes == [(2, SPOOLMAN_SPOOL)]


def test_a_resolved_tag_writes_the_spoolman_record_the_slicer_reads():
    # Live on E3 / spool 16: Fluidd already showed "Polymaker PLA Silk" from the lane name,
    # but Snapmaker Orca still read filament_sub_type Basic because a tagged lane only named
    # the AFC card and never called apply_spool.
    silk = {
        "id": 16,
        "filament": {
            "name": "Gold",
            "material": "PLA",
            "color_hex": "B56600",
            "vendor": {"name": "Polymaker"},
            "extra": {"variant": '"Silk"'},
        },
    }
    resolution, helper, _afc_pushes = build_resolution()
    helper.spoolman.fetch_spool = lambda spool_id, on_spool: on_spool(silk)
    helper.holders.spool_holders[3] = {
        "VENDOR": "Polymaker", "MAIN_TYPE": "PLA", "SUB_TYPE": "Silk",
        "ARGB_COLOR": "B56600FF", "SPOOL_ID": 16, "SKU": "0",
    }
    resolution.apply_spool_for_extruder(3)
    assert helper.writer.applied_spools == [(3, silk)]
    assert slicer_filament_description(silk) == "Polymaker PLA Silk"


def test_apply_with_no_holder_warns_and_never_resolves():
    resolution, helper, _afc_pushes = build_resolution()
    resolution.apply_spool_for_extruder(1)
    assert helper.spoolman.resolved_infos == []
    assert any("No filament info" in line for line in helper.logs.lines)


def test_a_dict_resolution_fills_the_holders_missing_spool_id():
    resolution, helper, _afc_pushes = build_resolution(resolve_result=SPOOLMAN_SPOOL)
    holder = {**TAGGED, "SPOOL_ID": None}
    helper.holders.spool_holders[0] = holder
    resolution.apply_spool_for_extruder(0)
    assert holder["SPOOL_ID"] == 104
    assert helper.macros.tool_spool_sets == [("T0", 104)]


def test_unresolvable_holder_warns_and_retracts_the_spool_that_was_there_before():
    resolution, helper, afc_pushes = build_resolution(
        resolve_result=None, candidates=[SPOOLMAN_SPOOL])
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert helper.macros.tool_spool_sets == [("T0", None)]
    assert afc_pushes == [(0, None)]
    assert helper.holders.spool_holders[0]["SKU"] == "abc"
    assert any("Nothing in Spoolman matches the tag" in line for line in helper.logs.lines)


def test_a_tag_that_matches_nothing_names_what_was_read_and_how_to_bind_it():
    resolution, helper, _afc_pushes = build_resolution(resolve_result=None)
    helper.holders.spool_holders[0] = {
        **TAGGED, "SPOOL_ID": None, "CARD_UID": [0x04, 0xA1, 0xB2, 0xC3]}
    resolution.apply_spool_for_extruder(0)
    unmatched = [line for line in helper.logs.lines if "Nothing in Spoolman matches" in line]
    assert len(unmatched) == 1
    said = unmatched[0]
    assert "ELEGOO" in said
    assert "PLA Matte" in said
    assert "#1D6C6A" in said
    assert "04a1b2c3" in said
    assert "SH_BIND_CARD_UID CHANNEL=0 SPOOL=" in said
    assert helper.holders.spool_holders[0]["VENDOR"] == "ELEGOO"


def test_a_spoolman_that_never_answers_is_never_reported_as_no_match():
    resolution, helper, _afc_pushes = build_resolution(
        resolve_result=None, spoolman_unanswered=True)
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert any("Spoolman did not answer" in line for line in helper.logs.lines)
    assert not any("Nothing in Spoolman matches" in line for line in helper.logs.lines)


def test_a_tag_that_matches_nothing_lists_your_own_spools_with_their_bind_commands():
    resolution, helper, _afc_pushes = build_resolution(
        resolve_result=None, candidates=[SPOOLMAN_SPOOL])
    helper.holders.spool_holders[3] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(3)
    assert helper.spoolman.searched_infos == [helper.holders.spool_holders[3]]
    assert any("Spools of yours that look like this tag on channel 3" in line
               for line in helper.logs.lines)
    assert any("SH_BIND_CARD_UID CHANNEL=3 SPOOL=104" in line for line in helper.logs.lines)


def test_a_search_that_finds_nothing_close_says_so_instead_of_listing_nothing():
    resolution, helper, _afc_pushes = build_resolution(resolve_result=None, candidates=[])
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert any("None of your Spoolman spools look like this tag" in line
               for line in helper.logs.lines)
    assert not any("SH_BIND_CARD_UID CHANNEL=0 SPOOL=104" in line for line in helper.logs.lines)


def test_a_search_spoolman_never_answered_is_never_reported_as_nothing_close():
    resolution, helper, _afc_pushes = build_resolution(resolve_result=None, candidates=None)
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert any("nothing to offer for the tag on channel 0" in line for line in helper.logs.lines)
    assert not any("None of your Spoolman spools" in line for line in helper.logs.lines)


def test_a_spoolman_that_never_answers_is_never_asked_for_candidates():
    resolution, helper, _afc_pushes = build_resolution(
        resolve_result=None, spoolman_unanswered=True)
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert helper.spoolman.searched_infos == []


def test_a_tag_no_spool_of_theirs_came_close_to_becomes_a_spool_the_lane_reports():
    resolution, helper, afc_pushes = build_resolution(
        resolve_result=None, candidates=[], registration=REGISTERS_A_NEW_SPOOL)
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert helper.spoolman.registered_infos == [helper.holders.spool_holders[0]]
    assert helper.holders.spool_holders[0]["SPOOL_ID"] == 777
    assert helper.macros.tool_spool_sets == [("T0", None), ("T0", 777)]
    assert afc_pushes == [(0, None), (0, 777)]
    assert any("is now Spoolman spool 777" in line for line in helper.logs.lines)


def test_a_tag_one_of_their_spools_came_close_to_is_theirs_to_pick_and_creates_nothing():
    resolution, helper, _afc_pushes = build_resolution(
        resolve_result=None, candidates=[SPOOLMAN_SPOOL])
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert helper.spoolman.registered_infos == []


def test_nothing_is_created_for_a_tag_when_the_search_never_read_the_inventory():
    resolution, helper, _afc_pushes = build_resolution(resolve_result=None, candidates=None)
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert helper.spoolman.registered_infos == []


def test_a_tag_spoolman_would_not_take_says_so_and_leaves_the_lane_unbound():
    resolution, helper, afc_pushes = build_resolution(
        resolve_result=None, candidates=[], registration=REFUSES_TO_REGISTER)
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert helper.holders.spool_holders[0]["SPOOL_ID"] is None
    assert afc_pushes == [(0, None)]
    assert any("would not take a new spool for the tag on channel 0" in line
               for line in helper.logs.lines)


def test_a_tag_can_be_written_onto_a_spool_they_picked_and_the_lane_reports_it_at_once():
    resolution, helper, afc_pushes = build_resolution()
    helper.holders.spool_holders[2] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_tag_to_spool(2, 512)
    assert helper.spoolman.applied_to_spools == [(helper.holders.spool_holders[2], 512)]
    assert helper.holders.spool_holders[2]["SPOOL_ID"] == 512
    assert afc_pushes == [(2, 512)]
    assert any("written onto Spoolman spool 512" in line for line in helper.logs.lines)


def test_a_channel_with_no_tag_on_it_is_never_written_onto_a_spool():
    resolution, helper, _afc_pushes = build_resolution()
    helper.holders.spool_holders[2] = {"VENDOR": "NONE", "MAIN_TYPE": "", "SPOOL_ID": None}
    resolution.apply_tag_to_spool(2, 512)
    assert helper.spoolman.applied_to_spools == []
    assert any("no tag on channel 2 to write onto a spool" in line for line in helper.logs.lines)


def test_a_lane_with_no_tag_says_nothing_and_searches_for_nothing():
    resolution, helper, _afc_pushes = build_resolution(
        resolve_result=None, candidates=[SPOOLMAN_SPOOL])
    helper.holders.spool_holders[0] = {"VENDOR": "NONE", "MAIN_TYPE": "", "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert helper.spoolman.searched_infos == []
    assert not any("look like this tag" in line for line in helper.logs.lines)


def test_a_lane_that_matches_no_spool_gives_its_panel_name_back():
    resolution, helper, _afc_pushes = build_resolution(
        resolve_result=None, candidates=[SPOOLMAN_SPOOL])
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert helper.writer.blanked_lanes == [0]
    assert helper.writer.applied_spools == []
    assert helper.writer.labelled_lanes == []


def test_auto_mode_prefers_the_mapped_spool_with_an_id():
    resolution, helper, _afc_pushes = build_resolution()
    helper.holders.spool_holders[1] = dict(TAGGED)
    helper.macros.spool_id_by_tool[1] = 55
    assert resolution.find_spool_for_tool(1)["SPOOL_ID"] == 104


def test_manual_mode_prefers_the_macro_pick():
    resolution, helper, _afc_pushes = build_resolution(mode="manual")
    helper.holders.spool_holders[1] = dict(TAGGED)
    helper.macros.spool_id_by_tool[1] = 55
    assert resolution.find_spool_for_tool(1) == {"SPOOL_ID": 55}


def test_mapped_query_falls_back_to_the_manual_pick_on_an_untagged_lane():
    resolution, helper, _afc_pushes = build_resolution()
    helper.holders.spool_holders[2] = {"VENDOR": "NONE"}
    helper.macros.spool_id_by_tool[2] = 55
    assert resolution.get_mapped_spool_for_tool(2) == {"SPOOL_ID": 55}


def test_set_active_tool_routes_the_resolved_spool_into_tracking():
    resolution, helper, _afc_pushes = build_resolution()
    helper.holders.spool_holders[2] = dict(TAGGED)
    resolution.set_active_tool(2)
    assert helper.tracking.tracked_spools == [104]


def test_set_active_tool_without_a_resolvable_spool_warns():
    resolution, helper, _afc_pushes = build_resolution()
    resolution.set_active_tool(2)
    assert helper.tracking.tracked_spools == []
    assert any("Cannot set active spool" in line for line in helper.logs.lines)


def test_bind_channel_card_uid_binds_the_lanes_stable_uid():
    resolution, helper, _afc_pushes = build_resolution()
    helper.holders.spool_holders[1] = {**TAGGED, "CARD_UID": [0x04, 0xA1, 0xB2, 0xC3]}
    resolution.bind_channel_card_uid(1, 42)
    assert helper.spoolman.bound_uids == [(42, "04a1b2c3")]


def test_bind_channel_card_uid_refuses_without_a_stable_uid():
    resolution, helper, _afc_pushes = build_resolution()
    helper.holders.spool_holders[1] = dict(TAGGED)  # no CARD_UID at all
    resolution.bind_channel_card_uid(1, 42)
    assert helper.spoolman.bound_uids == []
    assert any("No stable tag UID" in line for line in helper.logs.lines)


def test_link_spool_puts_the_lane_on_the_spool_it_was_just_linked_to():
    resolution, helper, afc_pushes = build_resolution()
    helper.holders.spool_holders[1] = {**TAGGED, "SPOOL_ID": None,
                                       "CARD_UID": [0x04, 0xA1, 0xB2, 0xC3]}
    resolution.bind_channel_card_uid(1, 42)
    assert helper.holders.spool_holders[1]["SPOOL_ID"] == 42
    assert helper.macros.tool_spool_sets == [("T1", 42)]
    assert afc_pushes == [(1, 42)]
    assert helper.writer.applied_spools == [(1, SPOOLMAN_SPOOL)]
    assert helper.writer.labelled_lanes == [(1, SPOOLMAN_SPOOL)]


def test_link_spool_leaves_the_lane_alone_when_the_reel_belongs_to_another_spool():
    resolution, helper, afc_pushes = build_resolution(uid_bind_holds=False)
    helper.holders.spool_holders[1] = {**TAGGED, "SPOOL_ID": None,
                                       "CARD_UID": [0x04, 0xA1, 0xB2, 0xC3]}
    resolution.bind_channel_card_uid(1, 42)
    assert helper.holders.spool_holders[1]["SPOOL_ID"] is None
    assert helper.macros.tool_spool_sets == []
    assert afc_pushes == []
    assert helper.writer.applied_spools == []
    assert helper.writer.labelled_lanes == []


def test_add_spool_from_tag_creates_the_spool_and_puts_the_lane_on_it():
    resolution, helper, afc_pushes = build_resolution(registration=REGISTERS_A_NEW_SPOOL)
    helper.holders.spool_holders[1] = {**TAGGED, "SPOOL_ID": None,
                                       "CARD_UID": [0x04, 0xA1, 0xB2, 0xC3]}
    resolution.add_spool_from_tag(1)
    assert helper.spoolman.registered_infos == [helper.holders.spool_holders[1]]
    assert helper.holders.spool_holders[1]["SPOOL_ID"] == 777
    assert afc_pushes == [(1, 777)]


def test_the_add_names_the_lane_from_the_spool_it_just_made_and_asks_spoolman_nothing_more():
    """A lane sat showing "Matte", the one word Spoolman files a new filament under, until a
    second question to Spoolman came back with the whole name. The spool the add just made
    carries that name already, so the lane is named as the spool is added."""
    resolution, helper, _afc_pushes = build_resolution(registration=REGISTERS_A_NEW_SPOOL)
    helper.spoolman.created_spool = SPOOLMAN_SPOOL
    helper.holders.spool_holders[1] = {**TAGGED, "SPOOL_ID": None,
                                       "CARD_UID": [0x04, 0xA1, 0xB2, 0xC3]}
    resolution.add_spool_from_tag(1)
    assert helper.writer.applied_spools == [(1, SPOOLMAN_SPOOL)]
    assert helper.writer.labelled_lanes == [(1, SPOOLMAN_SPOOL)]
    assert helper.spoolman.fetched_spool_ids == []


def test_add_spool_from_tag_asks_by_hand_so_the_auto_register_setting_does_not_hold_it_back():
    resolution, helper, _afc_pushes = build_resolution()
    helper.holders.spool_holders[1] = {**TAGGED, "SPOOL_ID": None}
    resolution.add_spool_from_tag(1)
    assert helper.spoolman.asked_by_hand == [True]


def test_add_spool_from_tag_leaves_the_lane_alone_when_spoolman_refuses():
    resolution, helper, afc_pushes = build_resolution(
        registration=REFUSES_TO_REGISTER)
    helper.holders.spool_holders[1] = {**TAGGED, "SPOOL_ID": None,
                                       "CARD_UID": [0x04, 0xA1, 0xB2, 0xC3]}
    resolution.add_spool_from_tag(1)
    assert helper.holders.spool_holders[1]["SPOOL_ID"] is None
    assert afc_pushes == []
    assert any("would not take a new spool for the tag on channel 1" in line
               for line in helper.logs.lines)


def test_a_channel_with_no_tag_on_it_never_becomes_a_new_spool():
    resolution, helper, _afc_pushes = build_resolution()
    helper.holders.spool_holders[1] = {"VENDOR": "NONE", "MAIN_TYPE": "", "SPOOL_ID": None}
    resolution.add_spool_from_tag(1)
    assert helper.spoolman.registered_infos == []


def test_bind_channel_card_uid_rejects_an_out_of_range_channel():
    resolution, helper, _afc_pushes = build_resolution()
    resolution.bind_channel_card_uid(9, 42)
    assert helper.spoolman.bound_uids == []
    assert any("Channel must be" in line for line in helper.logs.lines)


# The strategy chain itself, driven end to end through the REAL Spoolman against a fake
# Spoolman server. Everything above stubs `resolve_spool`, so the order of the chain
# (`spool_id`, then `uid`, then `sku`) had never actually run. A stock Snapmaker tag is the case
# that depends on it: the firmware's filament struct has no SPOOL_ID field, so the tag arrives
# carrying only a card UID and a SKU, and the UID has to win.

SNAPMAKER_UID_BYTES = [0x04, 0x5B, 0x2C, 0x71, 0x9A, 0x30, 0x80]
SNAPMAKER_UID_HEX = "045b2c719a3080"
SNAPMAKER_SKU = "sm-pla-basic-black"

STOCK_SNAPMAKER_TAG = {
    "VENDOR": "Snapmaker", "MAIN_TYPE": "PLA", "SUB_TYPE": "Basic",
    "ARGB_COLOR": "1D1D1DFF", "SKU": SNAPMAKER_SKU,
    "CARD_UID": SNAPMAKER_UID_BYTES,
}
UID_BOUND_SPOOL = {
    "id": 77,
    "extra": {card_uids.CARD_UIDS_FIELD: json.dumps(json.dumps([SNAPMAKER_UID_HEX]))},
}
SKU_MATCHED_SPOOL = {"id": 31}
UNBOUND_UID_BYTES = [0x04, 0x11, 0x22, 0x33]
SPOOLMAN_SERVER = {
    ("/api/v1/spool", ""): [SKU_MATCHED_SPOOL, UID_BOUND_SPOOL],
    ("/api/v1/filament", f"article_number={SNAPMAKER_SKU}"):
        [{"id": 9, "article_number": SNAPMAKER_SKU}],
    ("/api/v1/spool", "filament.id=9"): [SKU_MATCHED_SPOOL],
}

# Spoolman filters `article_number` by SUBSTRING: a tag whose SKU is "2" gets back every filament
# whose article number merely contains a 2, and none of them is this spool's filament.
PARTIAL_SKU = "2"
SUBSTRING_MATCHED_FILAMENTS = [
    {"id": 44, "article_number": "34062"},
    {"id": 57, "article_number": "900002"},
]
SUBSTRING_SPOOLMAN_SERVER = {
    ("/api/v1/spool", ""): [SKU_MATCHED_SPOOL, UID_BOUND_SPOOL],
    ("/api/v1/filament", f"article_number={PARTIAL_SKU}"): SUBSTRING_MATCHED_FILAMENTS,
    ("/api/v1/spool", "filament.id=44"): [{"id": 44}],
    ("/api/v1/spool", "filament.id=57"): [{"id": 57}],
}


class FakeWebRequest:
    """What Moonraker hands back to the plugin's callback endpoint."""

    def __init__(self, endpoint, payload):
        self.method = endpoint
        self.params = {"payload": payload, "error": None}
        self.sent = []

    def send(self, response):
        self.sent.append(response)


class SpoolmanServerWebhooks(RecordingWebhooks):
    """A webhooks that answers a spoolman_proxy call the way the Moonraker proxy would."""

    def __init__(self, printer, spools_by_request):
        super().__init__(printer)
        self.spools_by_request = spools_by_request
        self.requested = []

    def call_remote_method(self, method, **params):
        super().call_remote_method(method, **params)
        if method != "spoolman_proxy":
            return
        request = (params["path"], params["query"])
        self.requested.append(request)
        payload = self.spools_by_request.get(request, [])
        self.endpoints[params["cb_endpoint"]](FakeWebRequest(params["cb_endpoint"], payload))


def resolve_through_real_chain(info, spools_by_request=None, logs=None, **written_options):
    printer = FakePrinter()
    webhooks = SpoolmanServerWebhooks(printer, spools_by_request or SPOOLMAN_SERVER)
    printer.objects["webhooks"] = webhooks
    spoolman = Spoolman(printer, logs or RecordingLogs(), helper_options(**written_options))
    resolved = []

    def collect(spool, spoolman_unanswered):
        resolved.append(spool)

    spoolman.resolve_spool(info, collect)
    return resolved, webhooks.requested


def test_a_spoolman_that_never_answers_still_reaches_a_verdict_and_says_so():
    printer = FakePrinter()
    printer.objects["webhooks"] = RecordingWebhooks(printer)
    logs = RecordingLogs()
    spoolman = Spoolman(printer, logs)
    verdicts = []

    def collect(spool, spoolman_unanswered):
        verdicts.append((spool, spoolman_unanswered))

    spoolman.resolve_spool({"VENDOR": "ELEGOO", "CARD_UID": UNBOUND_UID_BYTES}, collect)
    assert verdicts == []
    drain_timers(printer.reactor)
    assert verdicts == [(None, True)]
    assert any("Spoolman did not answer" in line for line in logs.lines)


def test_a_candidate_search_no_one_answered_hands_back_no_list_at_all():
    printer = FakePrinter()
    printer.objects["webhooks"] = RecordingWebhooks(printer)
    spoolman = Spoolman(printer, RecordingLogs())
    searches = []
    spoolman.search_candidates(dict(TAGGED), searches.append)
    drain_timers(printer.reactor)
    assert searches == [None]


def test_a_candidate_search_reads_the_whole_inventory_once():
    printer = FakePrinter()
    webhooks = SpoolmanServerWebhooks(printer, {("/api/v1/spool", ""): [SPOOLMAN_SPOOL]})
    printer.objects["webhooks"] = webhooks
    spoolman = Spoolman(printer, RecordingLogs())
    searches = []
    spoolman.search_candidates(dict(TAGGED), searches.append)
    assert webhooks.requested == [("/api/v1/spool", "")]
    assert [spool["id"] for spool in searches[0]] == [104]


def test_a_stock_snapmaker_tag_resolves_by_its_card_uid_before_its_sku():
    resolved, requested = resolve_through_real_chain(STOCK_SNAPMAKER_TAG)
    assert resolved == [UID_BOUND_SPOOL]
    assert ("/api/v1/filament", f"article_number={SNAPMAKER_SKU}") not in requested


def test_a_tag_whose_uid_is_bound_to_nothing_falls_through_to_its_sku():
    resolved, requested = resolve_through_real_chain(
        {**STOCK_SNAPMAKER_TAG, "CARD_UID": [0x04, 0x11, 0x22, 0x33]})
    assert resolved == [SKU_MATCHED_SPOOL]
    assert ("/api/v1/filament", f"article_number={SNAPMAKER_SKU}") in requested


def test_a_card_that_failed_the_firmware_signature_check_still_resolves_by_sku():
    # A card the firmware refuses to parse arrives on the blank template, UID all zeroes.
    resolved, _requested = resolve_through_real_chain(
        {**STOCK_SNAPMAKER_TAG, "CARD_UID": [0, 0, 0, 0]})
    assert resolved == [SKU_MATCHED_SPOOL]


def test_a_re_randomized_uid_is_never_used_as_the_key():
    # NXP AN10927: a UID starting 0x08 is re-randomized per tap, so it identifies nothing.
    resolved, _requested = resolve_through_real_chain(
        {**STOCK_SNAPMAKER_TAG, "CARD_UID": [0x08, 0x5B, 0x2C, 0x71]})
    assert resolved == [SKU_MATCHED_SPOOL]


def test_a_sku_that_only_partially_matches_an_article_number_binds_nothing():
    logs = RecordingLogs()
    resolved, requested = resolve_through_real_chain(
        {**STOCK_SNAPMAKER_TAG, "SKU": PARTIAL_SKU, "CARD_UID": UNBOUND_UID_BYTES},
        SUBSTRING_SPOOLMAN_SERVER, logs=logs, card_uids_auto_register=True)
    assert resolved == [None]
    assert ("/api/v1/spool", "filament.id=44") not in requested
    assert ("/api/v1/spool", "filament.id=57") not in requested
    assert not [request for request in requested if request[0].startswith("/api/v1/spool/")]
    assert any(f"SKU {PARTIAL_SKU} matches 0 filaments exactly (2 returned)" in line
               for line in logs.lines)


def test_two_filaments_sharing_one_article_number_bind_nothing():
    twins = [{"id": 44, "article_number": PARTIAL_SKU}, {"id": 57, "article_number": PARTIAL_SKU}]
    resolved, requested = resolve_through_real_chain(
        {**STOCK_SNAPMAKER_TAG, "SKU": PARTIAL_SKU, "CARD_UID": UNBOUND_UID_BYTES},
        {**SUBSTRING_SPOOLMAN_SERVER,
         ("/api/v1/filament", f"article_number={PARTIAL_SKU}"): twins},
        card_uids_auto_register=True)
    assert resolved == [None]
    assert not [request for request in requested if request[1].startswith("filament.id=")]


def test_one_exact_article_number_still_resolves_and_registers_the_card():
    resolved, requested = resolve_through_real_chain(
        {**STOCK_SNAPMAKER_TAG, "CARD_UID": UNBOUND_UID_BYTES}, card_uids_auto_register=True)
    assert resolved == [SKU_MATCHED_SPOOL]
    assert ("/api/v1/spool", "filament.id=9") in requested
    assert ("/api/v1/spool/31", "") in requested


def test_a_spool_id_on_the_tag_still_wins_over_both():
    resolved, requested = resolve_through_real_chain({**STOCK_SNAPMAKER_TAG, "SPOOL_ID": 104})
    assert resolved == [104]
    assert requested == []
