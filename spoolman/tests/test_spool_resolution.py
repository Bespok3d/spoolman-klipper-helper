# ruff: noqa: PLR2004  Tests assert against literal spool ids and channel indexes.
"""Resolving holders to Spoolman ids, the tool->spool queries, and the UID-bind entry."""
import json
import types

from klipper_fakes import FakePrinter, RecordingLogs, RecordingWebhooks
from spoolman import card_uids
from spoolman.spool_holders import SpoolHolders
from spoolman.spool_resolution import SpoolResolution
from spoolman.spoolman import Spoolman

TAGGED = {
    "VENDOR": "ELEGOO", "MAIN_TYPE": "PLA", "SUB_TYPE": "Matte",
    "ARGB_COLOR": "1D6C6AFF", "SPOOL_ID": 104, "SKU": "abc",
}
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
SPOOLMAN_SERVER = {
    ("/api/v1/spool", ""): [SKU_MATCHED_SPOOL, UID_BOUND_SPOOL],
    ("/api/v1/filament", f"article_number={SNAPMAKER_SKU}"): [{"id": 9}],
    ("/api/v1/spool", "filament.id=9"): [SKU_MATCHED_SPOOL],
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


def resolve_through_real_chain(info, spools_by_request=None):
    printer = FakePrinter()
    webhooks = SpoolmanServerWebhooks(printer, spools_by_request or SPOOLMAN_SERVER)
    printer.objects["webhooks"] = webhooks
    spoolman = Spoolman(printer, RecordingLogs())
    resolved = []
    spoolman.resolve_spool(info, resolved.append)
    return resolved, webhooks.requested


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


def test_a_spool_id_on_the_tag_still_wins_over_both():
    resolved, requested = resolve_through_real_chain({**STOCK_SNAPMAKER_TAG, "SPOOL_ID": 104})
    assert resolved == [104]
    assert requested == []
