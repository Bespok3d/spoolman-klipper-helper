# ruff: noqa: PLR2004  Tests assert against literal spool ids and channel indexes.
"""Resolving holders to Spoolman ids, the tool->spool queries, and the UID-bind entry."""
import types

from spoolman.spool_holders import SpoolHolders
from spoolman.spool_resolution import SpoolResolution

TAGGED = {
    "VENDOR": "ELEGOO", "MAIN_TYPE": "PLA", "SUB_TYPE": "Matte",
    "ARGB_COLOR": "1D6C6AFF", "SPOOL_ID": 104, "SKU": "abc",
}
SPOOLMAN_SPOOL = {
    "id": 104,
    "filament": {"name": "PLA Matte", "material": "PLA", "color_hex": "1D6C6A",
                 "vendor": {"name": "ELEGOO"}},
}


class RecordingLogs:
    def __init__(self):
        self.lines = []

    def _record(self, message):
        self.lines.append(message)

    log = warn = error = verbose = debug = _record


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
    def __init__(self, resolve_result=104):
        self.resolve_result = resolve_result
        self.resolved_infos = []
        self.bound_uids = []

    def resolve_spool(self, info, callback):
        self.resolved_infos.append(info)
        callback(self.resolve_result)

    def fetch_spool(self, spool_id, on_spool):
        on_spool(SPOOLMAN_SPOOL)

    def bind_uid(self, spool_id, uid):
        self.bound_uids.append((spool_id, uid))


class RecordingWriter:
    def __init__(self):
        self.labelled_lanes = []

    def label_lane(self, extruder, spool):
        self.labelled_lanes.append((extruder, spool))


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


def build_resolution(resolve_result=104, mode="auto", extruder_by_tool=None):
    logs = RecordingLogs()
    macros = RecordingMacros()
    afc_pushes = []
    helper = types.SimpleNamespace(
        logs=logs,
        macros=macros,
        spoolman=RecordingSpoolman(resolve_result),
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
    assert helper.writer.labelled_lanes == [(2, SPOOLMAN_SPOOL)]


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


def test_unresolvable_holder_warns_and_mirrors_nothing():
    resolution, helper, afc_pushes = build_resolution(resolve_result=None)
    helper.holders.spool_holders[0] = {**TAGGED, "SPOOL_ID": None}
    resolution.apply_spool_for_extruder(0)
    assert helper.macros.tool_spool_sets == []
    assert afc_pushes == []
    assert any("Unable to resolve spool id" in line for line in helper.logs.lines)


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


def test_bind_channel_card_uid_rejects_an_out_of_range_channel():
    resolution, helper, _afc_pushes = build_resolution()
    resolution.bind_channel_card_uid(9, 42)
    assert helper.spoolman.bound_uids == []
    assert any("Channel must be" in line for line in helper.logs.lines)
