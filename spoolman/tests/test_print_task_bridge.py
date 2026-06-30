# ruff: noqa: PLR2004  Tests assert against literal spool ids, extruders, and colors.
"""Regression tests for the Spoolman -> print_task color/material bridge."""
import asyncio
import base64

from print_task_bridge import (
    PrintTaskBridge,
    changed_tool_spools,
    channel_is_official,
    composed_filament_name,
    config_already_matches,
    filament_config_args_from_spool,
    filament_config_clear_args,
    has_filament_fields,
    normalize_color_rgba,
    physical_extruder_for_tool,
    set_lane_filament_name_gcode,
    set_print_filament_config_gcode,
)

SPOOL = {
    "id": 42,
    "filament": {
        "name": "PLA Spicy Mint",
        "material": "PLA",
        "color_hex": "5fd4a0",
        "vendor": {"name": "FlashForge"},
    },
}
EXPECTED_GCODE = (
    'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=3 VENDOR="FlashForge" '
    'FILAMENT_TYPE="PLA" FILAMENT_SUBTYPE="" FILAMENT_COLOR_RGBA=5FD4A0FF'
)
# A config write also pushes the vendor+name label to the lane (extruder 3).
EXPECTED_NAME_GCODE = set_lane_filament_name_gcode(3, "FlashForge PLA Spicy Mint")
# Clearing a lane resets the slot to the firmware's empty defaults and blanks the lane name.
EXPECTED_CLEAR_GCODE = (
    'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=3 VENDOR="NONE" '
    'FILAMENT_TYPE="NONE" FILAMENT_SUBTYPE="NONE" FILAMENT_COLOR_RGBA=FFFFFFFF'
)
EXPECTED_CLEAR_NAME_GCODE = set_lane_filament_name_gcode(3, "")


def idle_print_task():
    return {
        "extruder_map_table": [0, 1, 2, 3],
        "filament_official": [False, False, False, False],
        "filament_vendor": ["", "", "", ""],
        "filament_type": ["", "", "", ""],
        "filament_sub_type": ["Basic", "Basic", "SnapSpeed", ""],
        "filament_color_rgba": ["FFFFFFFF", "FFFFFFFF", "FFFFFFFF", "FFFFFFFF"],
    }


def primed_status(print_task, state="standby"):
    return {
        "print_task_config": print_task,
        "print_stats": {"state": state},
        "gcode_macro T0": {"spool_id": ""},
        "gcode_macro T1": {"spool_id": ""},
        "gcode_macro T2": {"spool_id": ""},
        "gcode_macro T3": {"spool_id": ""},
    }


def test_normalize_color_pads_six_char():
    assert normalize_color_rgba("5fd4a0") == "5FD4A0FF"


def test_normalize_color_passes_eight_char_through_uppercased():
    assert normalize_color_rgba("5fd4a0ff") == "5FD4A0FF"


def test_normalize_color_rejects_wrong_length():
    assert normalize_color_rgba("12345") == ""
    assert normalize_color_rgba("") == ""


def test_filament_config_args_maps_vendor_material_color():
    assert filament_config_args_from_spool(SPOOL, 3) == {
        "CONFIG_EXTRUDER": "3",
        "VENDOR": "FlashForge",
        "FILAMENT_TYPE": "PLA",
        "FILAMENT_SUBTYPE": "",
        "FILAMENT_COLOR_RGBA": "5FD4A0FF",
    }


def test_material_always_carries_vendor_and_subtype():
    # The firmware rejects a FILAMENT_TYPE that arrives without both VENDOR and FILAMENT_SUBTYPE
    # ("[print_task_config] filament_config, incomplete parameters"). A spool with no vendor still
    # has to send an (empty) VENDOR and FILAMENT_SUBTYPE alongside the type.
    spool = {"filament": {"material": "PETG", "color_hex": "ffffff"}}
    args = filament_config_args_from_spool(spool, 1)
    assert args["FILAMENT_TYPE"] == "PETG"
    assert args["VENDOR"] == ""
    assert args["FILAMENT_SUBTYPE"] == ""


def test_filament_config_args_omits_missing_fields():
    bare = {"filament": {}}
    assert filament_config_args_from_spool(bare, 1) == {"CONFIG_EXTRUDER": "1"}
    assert has_filament_fields(filament_config_args_from_spool(bare, 1)) is False


def test_set_print_filament_config_gcode_quotes_text_and_strips_injection():
    args = filament_config_args_from_spool(SPOOL, 3)
    assert set_print_filament_config_gcode(args) == EXPECTED_GCODE


def test_gcode_strips_unsafe_chars_from_vendor():
    spool = {"filament": {"material": "PLA", "color_hex": "ffffff",
                          "vendor": {"name": 'Evil"\nM112'}}}
    line = set_print_filament_config_gcode(filament_config_args_from_spool(spool, 0))
    assert "\n" not in line
    assert '"EvilM112"' in line


def test_physical_extruder_for_tool_resolves_mapped():
    assert physical_extruder_for_tool([0, 1, 2, 3], 2) == 2


def test_physical_extruder_for_tool_out_of_range_or_unmapped():
    assert physical_extruder_for_tool([0, 1, 2, 3], 9) is None
    assert physical_extruder_for_tool([None, 1], 0) is None
    assert physical_extruder_for_tool([0, 1, 2, 9], 3) is None


def test_channel_is_official():
    assert channel_is_official([False, False, True, False], 2) is True
    assert channel_is_official([False, False, True, False], 0) is False
    assert channel_is_official([], 0) is False


def test_changed_tool_spools_detects_single_change():
    status = {"gcode_macro T1": {"spool_id": 7}}
    assert changed_tool_spools({}, status) == {1: 7}


def test_changed_tool_spools_detects_clear():
    status = {"gcode_macro T1": {"spool_id": ""}}
    assert changed_tool_spools({1: 7}, status) == {1: None}


def test_changed_tool_spools_ignores_unrelated_and_unchanged():
    status = {"toolhead": {"position": [0, 0, 0]}, "gcode_macro T0": {"spool_id": 7}}
    assert changed_tool_spools({0: 7}, status) == {}


def test_config_already_matches_true_when_equal():
    task = idle_print_task()
    task["filament_vendor"][3] = "FlashForge"
    task["filament_type"][3] = "PLA"
    task["filament_color_rgba"][3] = "5FD4A0FF"
    desired = filament_config_args_from_spool(SPOOL, 3)
    assert config_already_matches(task, 3, desired) is True


def test_config_already_matches_false_on_any_difference():
    desired = filament_config_args_from_spool(SPOOL, 3)
    assert config_already_matches(idle_print_task(), 3, desired) is False


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.error = None

    def has_error(self):
        return self._payload is None

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, payload):
        self._payload = payload
        self.requests: list = []

    async def request(self, method, url, body=None, headers=None, **kwargs):
        self.requests.append({"method": method, "url": url, "body": body})
        return FakeResponse(self._payload)

    def location_patches(self):
        return [
            (req["url"], req["body"]["location"])
            for req in self.requests
            if req["method"] == "PATCH" and isinstance(req["body"], dict) and "location" in req["body"]
        ]


class FakeKlippyApis:
    def __init__(self, primed):
        self._primed = primed
        self.gcodes: list[str] = []
        self.callback = None

    async def query_objects(self, objects, default=None):
        return self._primed

    async def subscribe_objects(self, objects, callback=None, default=None):
        self.callback = callback

    async def run_gcode(self, script, default=None):
        self.gcodes.append(script)


class FakeSpoolman:
    def __init__(self):
        self.active_spool_calls: list = []

    def set_active_spool(self, spool_id):
        self.active_spool_calls.append(spool_id)


class FakeDatabase:
    def __init__(self, instance_name=""):
        self.instance_name = instance_name

    async def get_item(self, namespace, key, default=""):
        if namespace == "fluidd" and self.instance_name:
            return self.instance_name
        return default


class FakeServer:
    def __init__(self, http_client, klippy_apis, instance_name=""):
        self.spoolman = FakeSpoolman()
        self._components = {
            "http_client": http_client, "klippy_apis": klippy_apis, "spoolman": self.spoolman,
            "database": FakeDatabase(instance_name),
        }
        self.handlers: dict = {}

    def lookup_component(self, name, default=None):
        return self._components.get(name, default)

    def register_event_handler(self, event, handler):
        self.handlers[event] = handler


class FakeConfig:
    def __init__(self, server, url, location="", track_location="false"):
        self._server = server
        self._values = {"server": url, "location": location, "track_location": track_location}

    def get_server(self):
        return self._server

    def get(self, key, default=None):
        return self._values.get(key, default)

    def error(self, message):
        return ValueError(message)


def build_bridge(print_task, spool_payload=SPOOL, state="standby", location="",
                 track_location="false", instance_name=""):
    klippy = FakeKlippyApis(primed_status(print_task, state))
    server = FakeServer(FakeHttpClient(spool_payload), klippy, instance_name=instance_name)
    config = FakeConfig(server, "10.0.0.5:7912", location=location, track_location=track_location)
    bridge = PrintTaskBridge(config)
    asyncio.run(bridge._on_klippy_ready())
    return bridge, klippy


def test_untagged_pick_issues_set_print_filament_config():
    bridge, klippy = build_bridge(idle_print_task())
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    assert klippy.gcodes == [EXPECTED_GCODE, EXPECTED_NAME_GCODE]


def test_composed_filament_name_joins_vendor_and_name():
    assert composed_filament_name(SPOOL) == "FlashForge PLA Spicy Mint"


def test_composed_filament_name_drops_missing_parts():
    assert composed_filament_name({"filament": {"name": "Silk Gold"}}) == "Silk Gold"
    assert composed_filament_name({"filament": {"vendor": {"name": "ZIRO"}}}) == "ZIRO"
    assert composed_filament_name({"filament": {}}) == ""
    assert composed_filament_name({}) == ""


def test_set_lane_filament_name_gcode_base64_roundtrips_a_spaced_name():
    line = set_lane_filament_name_gcode(2, "ZIRO Silk Gold")
    assert line.startswith("SET_LANE_FILAMENT_NAME EXTRUDER=2 NAME_B64=")
    encoded = line.rsplit("NAME_B64=", 1)[1]
    assert " " not in encoded
    assert base64.b64decode(encoded).decode("utf-8") == "ZIRO Silk Gold"


def test_official_channel_is_left_untouched():
    task = idle_print_task()
    task["filament_official"][3] = True
    bridge, klippy = build_bridge(task)
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    assert klippy.gcodes == []


def test_unchanged_config_skips_the_write_but_still_relabels_the_lane():
    # An already-matching slot skips the SET_PRINT_FILAMENT_CONFIG write, but the lane name is still
    # pushed so a re-pick after a restart (which clears the AFC lane name) re-labels it.
    task = idle_print_task()
    task["filament_vendor"][3] = "FlashForge"
    task["filament_type"][3] = "PLA"
    task["filament_color_rgba"][3] = "5FD4A0FF"
    bridge, klippy = build_bridge(task)
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    assert klippy.gcodes == [EXPECTED_NAME_GCODE]


def test_clear_args_match_an_empty_slot_and_miss_a_filled_one():
    empty = idle_print_task()
    empty["filament_vendor"][3] = "NONE"
    empty["filament_type"][3] = "NONE"
    empty["filament_sub_type"][3] = "NONE"
    assert config_already_matches(empty, 3, filament_config_clear_args(3)) is True
    assert config_already_matches(idle_print_task(), 3, filament_config_clear_args(3)) is False


def test_cleared_spool_resets_the_slot_and_blanks_the_lane_name():
    # Picking then clearing T3 (untagged lane) now mirrors the clear, not just the pick.
    bridge, klippy = build_bridge(idle_print_task())
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": ""}}, 0.0))
    assert klippy.gcodes == [
        EXPECTED_GCODE, EXPECTED_NAME_GCODE, EXPECTED_CLEAR_GCODE, EXPECTED_CLEAR_NAME_GCODE,
    ]


def test_clear_on_official_channel_is_left_untouched():
    task = idle_print_task()
    task["filament_official"][3] = True
    bridge, klippy = build_bridge(task)
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": ""}}, 0.0))
    assert klippy.gcodes == []


def test_clear_already_empty_slot_is_a_no_op():
    task = idle_print_task()
    task["filament_vendor"][3] = "NONE"
    task["filament_type"][3] = "NONE"
    task["filament_sub_type"][3] = "NONE"
    bridge, klippy = build_bridge(task)
    # last_seen starts empty; a value->None transition is needed to drive a clear.
    bridge.last_seen_spool_by_tool[3] = 42
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": ""}}, 0.0))
    assert klippy.gcodes == []


def test_mid_print_pick_is_deferred_then_applied_at_print_end():
    bridge, klippy = build_bridge(idle_print_task(), state="printing")
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    assert klippy.gcodes == []
    asyncio.run(bridge._on_status_update({"print_stats": {"state": "standby"}}, 1.0))
    assert klippy.gcodes == [EXPECTED_GCODE, EXPECTED_NAME_GCODE]


def test_mid_print_clear_is_deferred_then_applied_at_print_end():
    bridge, klippy = build_bridge(idle_print_task(), state="printing")
    bridge.last_seen_spool_by_tool[3] = 42
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": ""}}, 0.0))
    assert klippy.gcodes == []
    asyncio.run(bridge._on_status_update({"print_stats": {"state": "standby"}}, 1.0))
    assert klippy.gcodes == [EXPECTED_CLEAR_GCODE, EXPECTED_CLEAR_NAME_GCODE]


def test_prime_reconciles_a_spool_already_present_at_klippy_ready():
    # A pick that predates the (re)start is present at prime; the bridge must still write it,
    # not silently record it as already-seen (the bug that left Junior stale after restarts).
    primed = primed_status(idle_print_task())
    primed["gcode_macro T3"] = {"spool_id": 42}
    klippy = FakeKlippyApis(primed)
    server = FakeServer(FakeHttpClient(SPOOL), klippy)
    bridge = PrintTaskBridge(FakeConfig(server, "10.0.0.5:7912"))
    asyncio.run(bridge._on_klippy_ready())
    assert klippy.gcodes == [EXPECTED_GCODE, EXPECTED_NAME_GCODE]


def test_resubscribes_on_every_klippy_ready():
    # Moonraker drops every subscription callback on klippy disconnect, so the bridge must
    # re-subscribe on each klippy_ready (a once-only subscribe goes deaf after the first restart).
    bridge, klippy = build_bridge(idle_print_task())
    assert klippy.callback is not None
    klippy.callback = None
    asyncio.run(bridge._on_klippy_ready())
    assert klippy.callback is not None


def test_picking_a_spool_makes_it_the_active_spool():
    # A loaded-and-selected lane is what Spoolman tracks, mounted on the carrier or not.
    bridge, _klippy = build_bridge(idle_print_task())
    spoolman = bridge.server.lookup_component("spoolman")
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    assert spoolman.active_spool_calls == [104]


def test_picking_a_spool_on_an_official_lane_is_left_to_the_tag():
    task = idle_print_task()
    task["filament_official"][3] = True
    bridge, _klippy = build_bridge(task)
    spoolman = bridge.server.lookup_component("spoolman")
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 104}}, 0.0))
    assert spoolman.active_spool_calls == []


def test_picking_a_spool_mid_print_is_deferred_then_active_at_print_end():
    bridge, _klippy = build_bridge(idle_print_task(), state="printing")
    spoolman = bridge.server.lookup_component("spoolman")
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    assert spoolman.active_spool_calls == []
    asyncio.run(bridge._on_status_update({"print_stats": {"state": "standby"}}, 1.0))
    assert spoolman.active_spool_calls == [104]


def test_removing_the_last_spool_clears_the_active_spool():
    bridge, _klippy = build_bridge(idle_print_task())
    spoolman = bridge.server.lookup_component("spoolman")
    asyncio.run(bridge._on_status_update({"gcode_macro T0": {"spool_id": 80}}, 0.0))
    asyncio.run(bridge._on_status_update({"gcode_macro T0": {"spool_id": ""}}, 0.0))
    assert spoolman.active_spool_calls == [80, None]


def test_removing_one_of_several_spools_keeps_the_active_spool():
    bridge, _klippy = build_bridge(idle_print_task())
    spoolman = bridge.server.lookup_component("spoolman")
    asyncio.run(bridge._on_status_update({"gcode_macro T0": {"spool_id": 80}}, 0.0))
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": ""}}, 0.0))
    assert spoolman.active_spool_calls == [80, 104]   # T0 still loaded, so no clear


def test_removing_the_last_spool_mid_print_keeps_the_active_spool():
    bridge, _klippy = build_bridge(idle_print_task(), state="printing")
    spoolman = bridge.server.lookup_component("spoolman")
    asyncio.run(bridge._on_status_update({"gcode_macro T0": {"spool_id": 80}}, 0.0))
    asyncio.run(bridge._on_status_update({"gcode_macro T0": {"spool_id": ""}}, 0.0))
    assert spoolman.active_spool_calls == []


def test_a_machine_that_never_had_spools_does_not_clear():
    # No spool was ever loaded, so nothing was "removed" -- avoids racing the helper at startup.
    bridge, _klippy = build_bridge(idle_print_task())
    spoolman = bridge.server.lookup_component("spoolman")
    asyncio.run(bridge._on_status_update({"gcode_macro T0": {"spool_id": ""}}, 0.0))
    assert spoolman.active_spool_calls == []


SPOOL_URL = "http://10.0.0.5:7912/api/v1/spool"


def test_location_tracking_is_off_by_default():
    bridge, _klippy = build_bridge(idle_print_task())
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    assert bridge.http_client.location_patches() == []


def test_enabled_without_a_name_does_nothing():
    bridge, _klippy = build_bridge(idle_print_task(), track_location="true", location="")
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    assert bridge.http_client.location_patches() == []


def test_picking_a_spool_stamps_this_printer_as_its_location():
    bridge, _klippy = build_bridge(idle_print_task(), track_location="true", location="unU1jr")
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    assert bridge.http_client.location_patches() == [(f"{SPOOL_URL}/104", "unU1jr")]


def test_unloading_a_spool_clears_its_location():
    bridge, _klippy = build_bridge(idle_print_task(), track_location="true", location="unU1jr")
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": ""}}, 0.0))
    assert bridge.http_client.location_patches() == [
        (f"{SPOOL_URL}/104", "unU1jr"),
        (f"{SPOOL_URL}/104", ""),
    ]


def test_repicking_moves_the_location_off_the_old_spool():
    bridge, _klippy = build_bridge(idle_print_task(), track_location="true", location="unU1jr")
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 80}}, 0.0))
    assert bridge.http_client.location_patches() == [
        (f"{SPOOL_URL}/104", "unU1jr"),
        (f"{SPOOL_URL}/104", ""),
        (f"{SPOOL_URL}/80", "unU1jr"),
    ]


def test_location_auto_detects_the_frontend_instance_name():
    bridge, _klippy = build_bridge(
        idle_print_task(), track_location="true", location="", instance_name="unU1jr")
    assert bridge.location_name == "unU1jr"
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    assert bridge.http_client.location_patches() == [(f"{SPOOL_URL}/104", "unU1jr")]


def test_configured_location_overrides_the_auto_detected_name():
    bridge, _klippy = build_bridge(
        idle_print_task(), track_location="true", location="my-bench", instance_name="unU1jr")
    assert bridge.location_name == "my-bench"


def test_no_auto_detect_when_tracking_is_off():
    bridge, _klippy = build_bridge(
        idle_print_task(), track_location="false", location="", instance_name="unU1jr")
    assert bridge.location_name == ""


RELEASE_T2 = "SET_GCODE_VARIABLE MACRO=T2 VARIABLE=spool_id VALUE=None"


def _filament_exist(values):
    return {"print_task_config": {"filament_exist": list(values)}}


def test_pulling_an_untagged_manual_spool_releases_its_lane():
    # No RFID event fires, but filament_exist drops; the bridge releases the lane (clears spool_id),
    # which cascades to clear its location, slot, and name.
    bridge, klippy = build_bridge(idle_print_task())
    asyncio.run(bridge._on_status_update(_filament_exist([True, True, True, True]), 0.0))   # baseline
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))        # pick
    klippy.gcodes.clear()
    asyncio.run(bridge._on_status_update(_filament_exist([True, True, False, True]), 0.0))  # T2 pulled
    assert RELEASE_T2 in klippy.gcodes


def test_no_release_during_a_print():
    bridge, klippy = build_bridge(idle_print_task(), state="printing")
    asyncio.run(bridge._on_status_update(_filament_exist([True, True, True, True]), 0.0))
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    klippy.gcodes.clear()
    asyncio.run(bridge._on_status_update(_filament_exist([True, True, False, True]), 0.0))
    assert RELEASE_T2 not in klippy.gcodes


def test_first_filament_exist_read_is_a_baseline_not_a_removal():
    bridge, klippy = build_bridge(idle_print_task())
    asyncio.run(bridge._on_status_update({"gcode_macro T2": {"spool_id": 104}}, 0.0))
    klippy.gcodes.clear()
    asyncio.run(bridge._on_status_update(_filament_exist([True, True, False, True]), 0.0))
    assert RELEASE_T2 not in klippy.gcodes


def test_no_release_for_a_lane_that_has_no_spool():
    bridge, klippy = build_bridge(idle_print_task())
    asyncio.run(bridge._on_status_update(_filament_exist([True, True, True, True]), 0.0))
    klippy.gcodes.clear()
    asyncio.run(bridge._on_status_update(_filament_exist([True, True, False, True]), 0.0))
    assert klippy.gcodes == []
