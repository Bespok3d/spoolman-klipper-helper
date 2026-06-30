"""Regression tests for the tool/print-task gcode the Spoolman helper emits."""
from macros import Macros


class FakeGcode:
    def __init__(self):
        self.scripts: list[str] = []

    def run_script_from_command(self, command):
        self.scripts.append(command)


class FakeLogs:
    def __init__(self):
        self.errors: list[str] = []

    def verbose(self, _message):
        pass

    def error(self, message):
        self.errors.append(message)


class FakeMacroObject:
    def __init__(self, variables):
        self.variables = variables


class FakePrinter:
    def __init__(self, gcode, macros_by_name=None):
        self._gcode = gcode
        self._macros = macros_by_name or {}
        self.webhooks = object()

    def lookup_object(self, name, default=None):
        if name == "gcode":
            return self._gcode
        if name == "webhooks":
            return self.webhooks
        return self._macros.get(name, default)


def build_macros(macros_by_name=None):
    gcode = FakeGcode()
    macros = Macros(FakePrinter(gcode, macros_by_name), FakeLogs())
    return macros, gcode


def test_clear_print_task_config_force_clears_the_slot():
    macros, gcode = build_macros()
    macros.clear_print_task_config(2)
    assert gcode.scripts == [
        'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=2 FORCE=1 VENDOR="NONE" '
        'FILAMENT_TYPE="NONE" FILAMENT_SUBTYPE="NONE" FILAMENT_COLOR_RGBA=FFFFFFFF'
    ]


def test_set_spool_id_for_a_real_tool_emits_the_variable_write():
    macros, gcode = build_macros({"gcode_macro T0": FakeMacroObject({"spool_id": 7})})
    macros.set_spool_id_for_tool("T0", None)
    assert gcode.scripts == ["SET_GCODE_VARIABLE MACRO=T0 VARIABLE=spool_id VALUE=None"]


def test_set_spool_id_skips_a_tool_with_no_spool_id_variable():
    # CLEAR_ALL once spammed an error for every T4..T31 that has no spool_id var; now it skips them.
    macros, gcode = build_macros({"gcode_macro T5": FakeMacroObject({})})
    macros.set_spool_id_for_tool("T5", None)
    assert gcode.scripts == []
    assert macros.logs.errors == []


def test_set_spool_id_skips_a_missing_tool_macro():
    macros, gcode = build_macros()
    macros.set_spool_id_for_tool("T31", None)
    assert gcode.scripts == []
    assert macros.logs.errors == []
