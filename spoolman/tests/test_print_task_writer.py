# ruff: noqa: PLR2004  Tests assert against literal extruder indexes and gcode strings.
"""Screen/lane writes: composition + the live official-channel and already-matches guards."""
import base64

from print_task_writer import (
    PrintTaskWriter,
    composed_filament_name,
    config_already_matches,
    filament_config_args_from_spool,
    filament_config_clear_args,
    normalize_color_rgba,
    set_lane_filament_name_gcode,
    set_print_filament_config_gcode,
)

SPOOL = {
    "id": 42,
    "filament": {
        "name": "PLA Spicy Mint",
        "material": "PLA",
        "color_hex": "3fD2c5",
        "vendor": {"name": "FlashForge"},
    },
}

EXPECTED_GCODE = (
    'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=3 VENDOR="FlashForge" '
    'FILAMENT_TYPE="PLA" FILAMENT_SUBTYPE="" FILAMENT_COLOR_RGBA=3FD2C5FF'
)


class RecordingMacros:
    def __init__(self):
        self.commands = []

    def run(self, command, error_msg):
        self.commands.append(command)


class FakePrintTaskConfig:
    def __init__(self, config):
        self.print_task_config = config


class FakePrinter:
    def __init__(self, task_config):
        self.task = FakePrintTaskConfig(task_config)

    def lookup_object(self, name, default=None):
        return self.task if name == "print_task_config" else default


class RecordingLogs:
    def __init__(self):
        self.lines = []

    def _record(self, message):
        self.lines.append(message)

    log = warn = error = verbose = debug = _record


def empty_task_config():
    return {
        "filament_vendor": ["NONE"] * 4,
        "filament_type": ["NONE"] * 4,
        "filament_sub_type": ["NONE"] * 4,
        "filament_color_rgba": ["FFFFFFFF"] * 4,
        "filament_official": [False] * 4,
    }


def build_writer(task_config):
    macros = RecordingMacros()
    writer = PrintTaskWriter(FakePrinter(task_config), RecordingLogs(), macros)
    return writer, macros


def test_normalize_color_rgba():
    assert normalize_color_rgba("3fD2c5") == "3FD2C5FF"
    assert normalize_color_rgba("3FD2C580") == "3FD2C580"
    assert normalize_color_rgba("") == ""
    assert normalize_color_rgba(None) == ""


def test_filament_config_args_carry_the_firmware_required_trio():
    args = filament_config_args_from_spool(SPOOL, 3)
    assert args["VENDOR"] == "FlashForge"
    assert args["FILAMENT_TYPE"] == "PLA"
    assert args["FILAMENT_SUBTYPE"] == ""  # required together with FILAMENT_TYPE
    assert args["FILAMENT_COLOR_RGBA"] == "3FD2C5FF"


def test_set_print_filament_config_gcode_quotes_and_strips_unsafe():
    gcode = set_print_filament_config_gcode(filament_config_args_from_spool(SPOOL, 3))
    assert gcode == EXPECTED_GCODE
    injected = {"CONFIG_EXTRUDER": "1", "VENDOR": 'Ven"; M112', "FILAMENT_TYPE": "PLA",
                "FILAMENT_SUBTYPE": ""}
    assert '"; M112' not in set_print_filament_config_gcode(injected)


def test_composed_filament_name():
    assert composed_filament_name(SPOOL) == "FlashForge PLA Spicy Mint"
    assert composed_filament_name({"filament": {"name": "Silk Gold"}}) == "Silk Gold"
    assert composed_filament_name({}) == ""


def test_lane_name_gcode_base64_roundtrips_a_spaced_name():
    line = set_lane_filament_name_gcode(2, "ZIRO Silk Gold")
    encoded = line.rsplit("NAME_B64=", 1)[1]
    assert " " not in encoded
    assert base64.b64decode(encoded).decode("utf-8") == "ZIRO Silk Gold"


def test_apply_spool_writes_config_and_name():
    writer, macros = build_writer(empty_task_config())
    writer.apply_spool(3, SPOOL)
    assert macros.commands[0] == EXPECTED_GCODE
    assert macros.commands[1].startswith("SET_LANE_FILAMENT_NAME EXTRUDER=3")


def test_apply_spool_skips_official_channel():
    task = empty_task_config()
    task["filament_official"][3] = True
    writer, macros = build_writer(task)
    writer.apply_spool(3, SPOOL)
    assert all(not cmd.startswith("SET_PRINT_FILAMENT_CONFIG") for cmd in macros.commands)


def test_apply_spool_skips_matching_config_but_still_pushes_name():
    # A re-pick after a restart: config persisted, the AFC lane name did not.
    task = empty_task_config()
    task["filament_vendor"][3] = "FlashForge"
    task["filament_type"][3] = "PLA"
    task["filament_sub_type"][3] = ""
    task["filament_color_rgba"][3] = "3FD2C5FF"
    writer, macros = build_writer(task)
    writer.apply_spool(3, SPOOL)
    assert len(macros.commands) == 1
    assert macros.commands[0].startswith("SET_LANE_FILAMENT_NAME")


def test_clear_extruder_resets_slot_and_blanks_name():
    task = empty_task_config()
    task["filament_vendor"][3] = "FlashForge"
    task["filament_type"][3] = "PLA"
    writer, macros = build_writer(task)
    writer.clear_extruder(3)
    assert macros.commands[0] == set_print_filament_config_gcode(filament_config_clear_args(3))
    assert macros.commands[1] == set_lane_filament_name_gcode(3, "")


def test_clear_already_empty_slot_is_a_no_op():
    writer, macros = build_writer(empty_task_config())
    writer.clear_extruder(3)
    assert macros.commands == []


def test_label_lane_pushes_the_composed_name():
    writer, macros = build_writer(empty_task_config())
    writer.label_lane(1, SPOOL)
    assert len(macros.commands) == 1
    assert macros.commands[0] == set_lane_filament_name_gcode(1, "FlashForge PLA Spicy Mint")


def test_label_lane_with_no_name_pushes_nothing():
    writer, macros = build_writer(empty_task_config())
    writer.label_lane(1, {"filament": {}})
    assert macros.commands == []


def test_config_already_matches():
    task = empty_task_config()
    assert config_already_matches(task, 3, filament_config_clear_args(3)) is True
    assert config_already_matches(task, 3, filament_config_args_from_spool(SPOOL, 3)) is False
