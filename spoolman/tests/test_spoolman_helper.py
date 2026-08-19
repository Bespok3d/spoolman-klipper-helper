# ruff: noqa: PLR2004  Tests assert against literal spool ids and channel indexes.
"""Boot characterization: the composition root wires, registers, and routes end to end.

The coordinator holds no logic of its own anymore; what can break it is wiring (a bad import,
a collaborator built in the wrong order, a renamed facade method) -- exactly what would leave
klippy unable to boot on the printer. This constructs the REAL SpoolmanHelper against a fake
printer and drives klippy-ready plus one tag report through the whole stack.
"""
import importlib
import json
import sys
import types
from pathlib import Path

_gcode_stub = types.ModuleType("gcode")
_gcode_stub.CommandError = type("CommandError", (Exception,), {})
sys.modules.setdefault("gcode", _gcode_stub)

# spoolman_helper.py lives in klippy/extras and imports its package relatively (in production
# Klipper loads it as extras.spoolman_helper), so the test recreates that package context.
_EXTRAS_DIR = Path(__file__).resolve().parent.parent / "files" / "klipper" / "klippy" / "extras"
_extras_package = types.ModuleType("extras")
_extras_package.__path__ = [str(_EXTRAS_DIR)]
sys.modules.setdefault("extras", _extras_package)
SpoolmanHelper = importlib.import_module("extras.spoolman_helper").SpoolmanHelper

TAGGED = {
    "VENDOR": "ELEGOO", "MAIN_TYPE": "PLA", "SUB_TYPE": "Matte",
    "ARGB_COLOR": "1D6C6AFF", "SPOOL_ID": 104, "SKU": "abc",
}
SPOOLMAN_SPOOL = {
    "id": 55,
    "filament": {
        "name": "PLA Matte",
        "material": "PLA",
        "color_hex": "1D6C6A",
        "vendor": {"name": "ELEGOO"},
    },
}


def stub_spool_fetch(helper, spool=None):
    fetched = []
    record = spool or SPOOLMAN_SPOOL

    def fetch_spool(spool_id, on_spool):
        fetched.append(spool_id)
        on_spool(record)

    helper.spoolman.fetch_spool = fetch_spool
    return fetched


def scripts_after(printer, action):
    gcode = printer.objects["gcode"]
    already_emitted = len(gcode.scripts)
    action()
    return gcode.scripts[already_emitted:]


class RecordingGcode:
    def __init__(self):
        self.commands = {}
        self.responses = []
        self.scripts = []

    def register_command(self, name, handler, desc=""):
        self.commands[name] = handler

    def respond_info(self, message):
        self.responses.append(message)

    def run_script_from_command(self, command):
        self.scripts.append(command)


class FakeReactor:
    NEVER = 9e99

    def __init__(self):
        self.timers = []

    def monotonic(self):
        return 0.0

    def register_timer(self, callback, when):
        self.timers.append((callback, when))


class RecordingWebhooks:
    def __init__(self, printer):
        self.printer = printer
        self.endpoints = {}
        self.remote_calls = []

    def register_endpoint(self, path, handler):
        self.endpoints[path] = handler

    def call_remote_method(self, method, **params):
        self.remote_calls.append((method, params))


class FakeMacroObject:
    def __init__(self):
        self.variables = {"spool_id": ""}


class RecordingRfid:
    def __init__(self):
        self.notify_callbacks = []

    def register_spool_notify(self, callback):
        self.notify_callbacks.append(callback)


class FakeAfcLane:
    def __init__(self, spool_id=None):
        self.spool_id = spool_id


class FakePrintTask:
    def __init__(self):
        self.print_task_config = {"filament_exist": [True, True, True, True]}


class FakePrinter:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.reactor = FakeReactor()
        self.event_handlers = {}
        self.objects = {"gcode": RecordingGcode(), "bespok3d_rfid": RecordingRfid()}
        self.objects["webhooks"] = RecordingWebhooks(self)
        self.objects["print_task_config"] = FakePrintTask()
        self.objects["AFC"] = object()
        for tool_index in range(4):
            self.objects[f"gcode_macro T{tool_index}"] = FakeMacroObject()
            self.objects[f"AFC_lane E{tool_index}"] = FakeAfcLane()

    def lookup_object(self, name, default=KeyError):
        if name in self.objects:
            return self.objects[name]
        if default is KeyError:
            raise KeyError(name)
        return default

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, event_name, handler):
        self.event_handlers[event_name] = handler

    def get_snapmaker_config_dir(self):
        return self.config_dir


class FakeConfig:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def get(self, key, default=None):
        return default


def boot_helper(tmp_path):
    (tmp_path / "print_task.json").write_text(json.dumps({
        "extruder_map_table": [0, 1, 2, 3],
        "filament_vendor": ["NONE"] * 4,
    }))
    printer = FakePrinter(str(tmp_path))
    helper = SpoolmanHelper(FakeConfig(printer))
    helper.manual_restore.manual_spools_path = str(tmp_path / "manual_spools.json")
    printer.event_handlers["klippy:ready"]()
    finish_detect(helper, printer)
    return helper, printer


def finish_detect(helper, printer, reports=None):
    notify = printer.objects["bespok3d_rfid"].notify_callbacks[0]
    reports = reports or {}
    for channel in list(helper.detection.pending_picks):
        info, is_clear = reports.get(channel, (None, True))
        notify(channel, info, is_clear)


UNTAGGED = {"VENDOR": "NONE", "MAIN_TYPE": "NONE", "SPOOL_ID": None}


def test_boot_registers_commands_hooks_and_the_watcher(tmp_path):
    _helper, printer = boot_helper(tmp_path)
    gcode = printer.objects["gcode"]
    for command_name in ("SH_SET_ACTIVE_TOOL", "SH_CLEAR_ALL_SPOOLS", "SH_DETECT_SPOOLS",
                         "SH_DUMP_SPOOLS", "SH_BIND_CARD_UID"):
        assert command_name in gcode.commands
    assert len(printer.objects["bespok3d_rfid"].notify_callbacks) == 1
    assert any("Loaded!" in line for line in gcode.responses)
    assert any(command.startswith("FILAMENT_DT_UPDATE") for command in gcode.scripts)
    assert printer.reactor.timers  # the carrier watch armed itself


def test_a_tag_report_routes_through_resolution_to_the_tool_macro(tmp_path):
    _helper, printer = boot_helper(tmp_path)
    notify = printer.objects["bespok3d_rfid"].notify_callbacks[0]
    notify(0, dict(TAGGED), False)
    gcode = printer.objects["gcode"]
    assert "SET_GCODE_VARIABLE MACRO=T0 VARIABLE=spool_id VALUE=104" in gcode.scripts
    assert any("Tool T0 is using: ELEGOO PLA Matte" in line for line in gcode.responses)


def test_a_clear_report_releases_the_tool_macro(tmp_path):
    _helper, printer = boot_helper(tmp_path)
    printer.objects["print_task_config"].print_task_config["filament_exist"][0] = False
    notify = printer.objects["bespok3d_rfid"].notify_callbacks[0]
    notify(0, dict(TAGGED), False)
    notify(0, None, True)
    assert "SET_GCODE_VARIABLE MACRO=T0 VARIABLE=spool_id VALUE=None" in (
        printer.objects["gcode"].scripts
    )


def test_a_clear_report_keeps_the_pick_when_filament_is_still_there(tmp_path):
    helper, printer = boot_helper(tmp_path)
    notify = printer.objects["bespok3d_rfid"].notify_callbacks[0]
    notify(0, dict(TAGGED), False)
    scripts_after_the_tag = list(printer.objects["gcode"].scripts)
    notify(0, None, True)
    new_scripts = printer.objects["gcode"].scripts[len(scripts_after_the_tag):]
    assert helper.holders.spool_holders[0] is None
    assert not any("VALUE=None" in script for script in new_scripts)
    assert printer.objects["AFC_lane E0"].spool_id == 104


def test_a_clear_report_keeps_a_manual_pick_and_reapplies_it(tmp_path):
    helper, printer = boot_helper(tmp_path)
    fetched = stub_spool_fetch(helper)
    printer.objects["gcode_macro T0"].variables["spool_id"] = 55
    notify = printer.objects["bespok3d_rfid"].notify_callbacks[0]
    new_scripts = scripts_after(printer, lambda: notify(0, None, True))
    assert printer.objects["gcode_macro T0"].variables["spool_id"] == 55
    assert helper.holders.spool_holders[0] is None
    assert fetched == [55]
    assert not any("VALUE=None" in script for script in new_scripts)
    assert any(
        script.startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0")
        and "VENDOR=\"ELEGOO\"" in script
        for script in new_scripts
    )
    assert any(
        script.startswith("SET_LANE_FILAMENT_NAME EXTRUDER=0 NAME_B64=")
        and not script.endswith("NAME_B64=")
        for script in new_scripts
    )


def test_a_clear_report_releases_a_manual_pick_when_the_lane_is_empty(tmp_path):
    _helper, printer = boot_helper(tmp_path)
    printer.objects["print_task_config"].print_task_config["filament_exist"][0] = False
    printer.objects["gcode_macro T0"].variables["spool_id"] = 55
    notify = printer.objects["bespok3d_rfid"].notify_callbacks[0]
    new_scripts = scripts_after(printer, lambda: notify(0, None, True))
    assert "SET_GCODE_VARIABLE MACRO=T0 VARIABLE=spool_id VALUE=None" in new_scripts
    assert "SET_LANE_FILAMENT_NAME EXTRUDER=0 NAME_B64=" in new_scripts


def test_a_spool_taken_off_a_lane_gives_the_panel_name_back(tmp_path):
    _helper, printer = boot_helper(tmp_path)
    printer.objects["print_task_config"].print_task_config["filament_exist"][0] = False
    notify = printer.objects["bespok3d_rfid"].notify_callbacks[0]
    notify(0, dict(TAGGED), False)
    notify(0, None, True)
    assert "SET_LANE_FILAMENT_NAME EXTRUDER=0 NAME_B64=" in printer.objects["gcode"].scripts


def test_clearing_no_particular_lane_leaves_every_lane_name_alone(tmp_path):
    helper, printer = boot_helper(tmp_path)
    helper.clear_spool_for_channel(None)
    assert not [
        script for script in printer.objects["gcode"].scripts
        if script.startswith("SET_LANE_FILAMENT_NAME")
    ]


def test_detect_spools_reapplies_a_manual_pick_without_unbinding(tmp_path):
    helper, printer = boot_helper(tmp_path)
    fetched = stub_spool_fetch(helper)
    printer.objects["gcode_macro T1"].variables["spool_id"] = 55
    helper.holders.spool_holders[1] = {"VENDOR": "NONE", "MAIN_TYPE": "", "SPOOL_ID": None}
    helper.detect_spools()
    new_scripts = scripts_after(printer, lambda: finish_detect(helper, printer))
    assert printer.objects["gcode_macro T1"].variables["spool_id"] == 55
    assert fetched == [55]
    assert not any("VALUE=None" in script for script in new_scripts)
    assert any(
        script.startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=1")
        for script in new_scripts
    )


def test_detect_spools_resolves_a_tagged_lane(tmp_path):
    helper, printer = boot_helper(tmp_path)
    stub_spool_fetch(helper, {**SPOOLMAN_SPOOL, "id": 104})
    (tmp_path / "print_task.json").write_text(json.dumps({
        "extruder_map_table": [0, 1, 2, 3],
        "filament_vendor": ["ELEGOO", "NONE", "NONE", "NONE"],
        "filament_type": ["PLA", "NONE", "NONE", "NONE"],
        "filament_sub_type": ["Matte", "NONE", "NONE", "NONE"],
    }))
    helper.holders.spool_holders[0] = dict(TAGGED)
    helper.detect_spools()
    new_scripts = scripts_after(
        printer, lambda: finish_detect(helper, printer, {0: (dict(TAGGED), False)})
    )
    assert "SET_GCODE_VARIABLE MACRO=T0 VARIABLE=spool_id VALUE=104" in new_scripts


def test_detect_spools_leaves_an_untagged_lane_without_a_pick_alone(tmp_path):
    helper, printer = boot_helper(tmp_path)
    helper.holders.spool_holders[2] = {"VENDOR": "NONE", "MAIN_TYPE": "", "SPOOL_ID": None}
    helper.detect_spools()
    new_scripts = scripts_after(printer, lambda: finish_detect(helper, printer))
    assert not any("VALUE=None" in script for script in new_scripts)
    assert not any(
        script.startswith("SET_PRINT_FILAMENT_CONFIG") for script in new_scripts
    )
    assert not any(
        script.startswith("SET_LANE_FILAMENT_NAME") for script in new_scripts
    )


def test_detect_spools_keeps_an_afc_pick_when_the_sensor_says_empty(tmp_path):
    helper, printer = boot_helper(tmp_path)
    fetched = stub_spool_fetch(helper, {**SPOOLMAN_SPOOL, "id": 94})
    printer.objects["print_task_config"].print_task_config["filament_exist"] = [
        False, False, False, False
    ]
    printer.objects["AFC_lane E0"].spool_id = 94
    helper.detect_spools()
    new_scripts = scripts_after(printer, lambda: finish_detect(helper, printer))
    assert "SET_GCODE_VARIABLE MACRO=T0 VARIABLE=spool_id VALUE=94" in new_scripts
    assert printer.objects["AFC_lane E0"].spool_id == 94
    assert fetched == [94]
    assert not any("VALUE=None" in script for script in new_scripts)
    assert any(
        script.startswith("SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0")
        for script in new_scripts
    )


def test_detect_spools_keeps_an_afc_pick_on_an_untagged_report(tmp_path):
    helper, printer = boot_helper(tmp_path)
    fetched = stub_spool_fetch(helper, {**SPOOLMAN_SPOOL, "id": 94})
    printer.objects["AFC_lane E2"].spool_id = 94
    helper.detect_spools()
    new_scripts = scripts_after(
        printer, lambda: finish_detect(helper, printer, {2: (dict(UNTAGGED), False)})
    )
    assert "SET_GCODE_VARIABLE MACRO=T2 VARIABLE=spool_id VALUE=94" in new_scripts
    assert printer.objects["AFC_lane E2"].spool_id == 94
    assert fetched == [94]
    assert not any("VALUE=None" in script for script in new_scripts)


def test_detect_spools_lets_a_fresh_tag_replace_an_afc_pick(tmp_path):
    helper, printer = boot_helper(tmp_path)
    stub_spool_fetch(helper, {**SPOOLMAN_SPOOL, "id": 104})
    printer.objects["AFC_lane E0"].spool_id = 94
    printer.objects["gcode_macro T0"].variables["spool_id"] = 94
    helper.detect_spools()
    new_scripts = scripts_after(
        printer, lambda: finish_detect(helper, printer, {0: (dict(TAGGED), False)})
    )
    assert "SET_GCODE_VARIABLE MACRO=T0 VARIABLE=spool_id VALUE=104" in new_scripts
