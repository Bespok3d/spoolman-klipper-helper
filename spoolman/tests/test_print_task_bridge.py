# ruff: noqa: PLR2004  Tests assert against literal spool ids, extruders, and colors.
"""Regression tests for the Spoolman -> print_task color/material bridge."""
import asyncio

from print_task_bridge import (
    PrintTaskBridge,
    changed_tool_spools,
    channel_is_official,
    config_already_matches,
    filament_config_args_from_spool,
    has_filament_fields,
    normalize_color_rgba,
    physical_extruder_for_tool,
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

    async def request(self, method, url, **kwargs):
        return FakeResponse(self._payload)


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


class FakeServer:
    def __init__(self, http_client, klippy_apis):
        self._components = {"http_client": http_client, "klippy_apis": klippy_apis}
        self.handlers: dict = {}

    def lookup_component(self, name):
        return self._components[name]

    def register_event_handler(self, event, handler):
        self.handlers[event] = handler


class FakeConfig:
    def __init__(self, server, url):
        self._server = server
        self._url = url

    def get_server(self):
        return self._server

    def get(self, key):
        return self._url

    def error(self, message):
        return ValueError(message)


def build_bridge(print_task, spool_payload=SPOOL, state="standby"):
    klippy = FakeKlippyApis(primed_status(print_task, state))
    server = FakeServer(FakeHttpClient(spool_payload), klippy)
    bridge = PrintTaskBridge(FakeConfig(server, "10.0.0.5:7912"))
    asyncio.run(bridge._on_klippy_ready())
    return bridge, klippy


def test_untagged_pick_issues_set_print_filament_config():
    bridge, klippy = build_bridge(idle_print_task())
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    assert klippy.gcodes == [EXPECTED_GCODE]


def test_official_channel_is_left_untouched():
    task = idle_print_task()
    task["filament_official"][3] = True
    bridge, klippy = build_bridge(task)
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    assert klippy.gcodes == []


def test_unchanged_config_is_a_no_op():
    task = idle_print_task()
    task["filament_vendor"][3] = "FlashForge"
    task["filament_type"][3] = "PLA"
    task["filament_color_rgba"][3] = "5FD4A0FF"
    bridge, klippy = build_bridge(task)
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    assert klippy.gcodes == []


def test_cleared_spool_is_a_no_op():
    bridge, klippy = build_bridge(idle_print_task())
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": ""}}, 0.0))
    assert klippy.gcodes == [EXPECTED_GCODE]


def test_mid_print_pick_is_deferred_then_applied_at_print_end():
    bridge, klippy = build_bridge(idle_print_task(), state="printing")
    asyncio.run(bridge._on_status_update({"gcode_macro T3": {"spool_id": 42}}, 0.0))
    assert klippy.gcodes == []
    asyncio.run(bridge._on_status_update({"print_stats": {"state": "standby"}}, 1.0))
    assert klippy.gcodes == [EXPECTED_GCODE]
