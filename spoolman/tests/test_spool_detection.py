# ruff: noqa: PLR2004  Tests assert against literal spool ids and channel indexes.
"""Bulk detection (firmware fields + rfid_data.json restore + re-read) and print-start sync."""
import json
import types

from spoolman.spool_detection import SpoolDetection
from spoolman.spool_holders import SpoolHolders

TAGGED = {
    "VENDOR": "ELEGOO", "MAIN_TYPE": "PLA", "SUB_TYPE": "Matte",
    "ARGB_COLOR": "1D6C6AFF", "SPOOL_ID": 104, "SKU": "abc",
}


class RecordingLogs:
    def __init__(self):
        self.lines = []

    def _record(self, message):
        self.lines.append(message)

    log = warn = error = verbose = debug = _record


class RecordingMacros:
    def __init__(self):
        self.detected_channels = []
        self.spool_id_by_tool = {}

    def detect_spool(self, channel):
        self.detected_channels.append(channel)

    def get_spool_id_for_tool(self, tool_id):
        return self.spool_id_by_tool.get(tool_id)

    def clear_print_task_config(self, channel):
        pass


class RecordingSpoolman:
    def __init__(self):
        self.resolved_infos = []

    def resolve_spool(self, info, callback):
        self.resolved_infos.append(info)
        callback({"id": info["SPOOL_ID"]}, False)


class FakeU1Tools:
    def __init__(self, spools_config, extruder_by_tool=None):
        self.spools_config = spools_config
        self.extruder_by_tool = extruder_by_tool or {}

    def get_spools_config(self):
        return self.spools_config

    def extruder_for_tool(self, tool_id):
        return self.extruder_by_tool.get(tool_id)


class RecordingResolution:
    def __init__(self):
        self.applied_extruders = []
        self.manual_pick_by_lane = {}

    def apply_spool_for_extruder(self, extruder):
        self.applied_extruders.append(extruder)

    def current_manual_pick(self, lane):
        return self.manual_pick_by_lane.get(lane)


def build_detection(tmp_path, spools_config, rfid_data=None, mode="auto", extruder_by_tool=None):
    rfid_data_path = tmp_path / "rfid_data.json"
    if rfid_data is not None:
        rfid_data_path.write_text(json.dumps(rfid_data))
    logs = RecordingLogs()
    macros = RecordingMacros()
    afc_pushes = []
    helper = types.SimpleNamespace(
        logs=logs,
        macros=macros,
        spoolman=RecordingSpoolman(),
        u1_tools=FakeU1Tools(spools_config, extruder_by_tool),
        mode=mode,
        push_spool_to_afc=lambda channel, spool_id: afc_pushes.append((channel, spool_id)),
    )
    helper.holders = SpoolHolders(logs, macros, lambda channel: None, lambda channel: True)
    helper.resolution = RecordingResolution()
    helper.printer = types.SimpleNamespace(lookup_object=lambda *args, **kwargs: None)
    detection = SpoolDetection(helper, str(rfid_data_path))
    return detection, helper, afc_pushes


def test_detect_merges_firmware_fields_and_rereads_every_channel(tmp_path):
    detection, helper, _afc_pushes = build_detection(
        tmp_path, spools_config=[{"VENDOR": "Acme"}, {"VENDOR": "NONE"}]
    )
    helper.holders.spool_holders[0] = {"VENDOR": None, "SKU": "abc"}
    detection.detect_spools()
    assert helper.holders.spool_holders[0] == {"VENDOR": "Acme", "SKU": "abc"}
    assert helper.macros.detected_channels == [0, 1]
    assert helper.resolution.applied_extruders == []
    assert detection.pending_picks == {0: None, 1: None}


def test_detect_restores_tagged_lanes_from_rfid_data(tmp_path):
    detection, helper, _afc_pushes = build_detection(
        tmp_path,
        spools_config=[{}, {}],
        rfid_data={"1": TAGGED, "2": {"VENDOR": "NONE"}, "9": TAGGED},
    )
    detection.detect_spools()
    assert helper.holders.spool_holders[1] == TAGGED  # tagged entry restored
    assert helper.holders.spool_holders[2] is None    # untagged entry is not identity
    assert any("Restored rfid data for extruder 1" in line for line in helper.logs.lines)


def test_detect_without_a_rfid_file_still_runs(tmp_path):
    detection, helper, _afc_pushes = build_detection(tmp_path, spools_config=[{}])
    detection.detect_spools()
    assert helper.resolution.applied_extruders == []
    assert helper.macros.detected_channels == [0]


def test_detect_sends_every_lane_through_resolution_including_untagged_picks(tmp_path):
    # Tagged, untagged-with-pick, and untagged-without-pick all wait for the RFID callback.
    detection, helper, _afc_pushes = build_detection(
        tmp_path, spools_config=[{}, {}, {}, {}]
    )
    helper.holders.spool_holders[0] = dict(TAGGED)
    helper.holders.spool_holders[1] = {"VENDOR": "NONE", "MAIN_TYPE": "", "SPOOL_ID": None}
    helper.resolution.manual_pick_by_lane[1] = 55
    detection.detect_spools()
    assert helper.macros.detected_channels == [0, 1, 2, 3]
    assert helper.resolution.applied_extruders == []
    assert detection.pending_picks == {0: None, 1: 55, 2: None, 3: None}


def test_sync_in_auto_mode_is_a_noop_because_the_map_is_live(tmp_path):
    detection, helper, afc_pushes = build_detection(tmp_path, spools_config=[])
    detection.sync_spools_tools()
    assert helper.spoolman.resolved_infos == []
    assert afc_pushes == []


def test_sync_in_manual_mode_caches_every_pick_and_leaves_the_lanes_to_the_tracking(tmp_path):
    # T0 and T5 both feed E2 in this print. Mirroring each tool onto its lane put T5's spool on
    # E2 after T0's, whichever the lane really held; the lane's spool is the tracking's to keep.
    detection, helper, afc_pushes = build_detection(
        tmp_path, spools_config=[], mode="manual", extruder_by_tool={0: 2, 1: 0, 5: 2}
    )
    helper.macros.spool_id_by_tool[0] = 55
    helper.macros.spool_id_by_tool[5] = 80
    detection.sync_spools_tools()
    assert helper.holders.spools_by_id == {55: {"id": 55}, 80: {"id": 80}}
    assert afc_pushes == []
