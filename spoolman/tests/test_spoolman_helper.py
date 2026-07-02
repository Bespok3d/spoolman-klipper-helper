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


class FakePrinter:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.reactor = FakeReactor()
        self.event_handlers = {}
        self.objects = {"gcode": RecordingGcode(), "bespok3d_rfid": RecordingRfid()}
        self.objects["webhooks"] = RecordingWebhooks(self)
        for tool_index in range(4):
            self.objects[f"gcode_macro T{tool_index}"] = FakeMacroObject()

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
    printer.event_handlers["klippy:ready"]()
    return helper, printer


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
    notify = printer.objects["bespok3d_rfid"].notify_callbacks[0]
    notify(0, dict(TAGGED), False)
    notify(0, None, True)
    assert "SET_GCODE_VARIABLE MACRO=T0 VARIABLE=spool_id VALUE=None" in (
        printer.objects["gcode"].scripts
    )
