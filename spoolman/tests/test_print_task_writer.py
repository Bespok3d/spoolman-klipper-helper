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

DECLARED_SUBTYPE_SPOOL = {
    "id": 43,
    "filament": {
        "name": "PLA+ Fast",
        "material": "PLA",
        "color_hex": "101010",
        "vendor": {"name": "ELEGOO"},
        "extra": {"subtype": '"Rapid"'},
    },
}

NAME_CARRIES_SUBTYPE_SPOOL = {
    "id": 44,
    "filament": {
        "name": "Silk Gold",
        "material": "PLA",
        "color_hex": "D4AF37",
        "vendor": {"name": "ZIRO"},
    },
}

RAPID_PETG_SPOOL = {
    "id": 45,
    "filament": {
        "name": "RAPID PETG Blue",
        "material": "PETG",
        "color_hex": "1E90FF",
        "vendor": {"name": "ELEGOO"},
    },
}

FULL_SPECTRUM_SPOOL = {
    "id": 46,
    "filament": {
        "name": "PLA Full Spectrum Red",
        "material": "PLA",
        "vendor": {"name": "FlashForge"},
    },
}

# The extended firmware files the sub-type in a "variant" extra field on the filament, stored as a
# JSON string like every Spoolman text field.
VARIANT_SPOOL = {
    "id": 48,
    "filament": {
        "name": "White",
        "material": "PETG",
        "vendor": {"name": "SUNLU"},
        "extra": {"variant": '"Basic"'},
    },
}


def tpu_spool_named(filament_name):
    return {
        "id": 47,
        "filament": {"name": filament_name, "material": "TPU", "vendor": {"name": "SUNLU"}},
    }


EXPECTED_GCODE = (
    'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=3 VENDOR="FlashForge" '
    'FILAMENT_TYPE="PLA" FILAMENT_SUBTYPE="Basic" FILAMENT_COLOR_RGBA=3FD2C5FF'
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


def build_writer(task_config, spoolman_overrides_tag=False):
    macros = RecordingMacros()
    writer = PrintTaskWriter(
        FakePrinter(task_config), RecordingLogs(), macros,
        spoolman_overrides_tag=spoolman_overrides_tag,
    )
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
    assert args["FILAMENT_SUBTYPE"] == "Basic"  # nothing filed anywhere, so the base line name
    assert args["FILAMENT_COLOR_RGBA"] == "3FD2C5FF"


def test_a_subtype_property_added_in_spoolman_is_announced():
    args = filament_config_args_from_spool(DECLARED_SUBTYPE_SPOOL, 3)
    assert args["FILAMENT_SUBTYPE"] == "Rapid"


def test_a_subtype_added_on_the_spool_itself_wins():
    # A declared field beats the "Silk" the name scan would find.
    spool = dict(NAME_CARRIES_SUBTYPE_SPOOL, extra={"sub_type": "Matte"})
    assert filament_config_args_from_spool(spool, 1)["FILAMENT_SUBTYPE"] == "Matte"


def test_the_variant_field_the_extended_firmware_writes_is_read_as_the_subtype():
    # The extended firmware's own field, on a filament whose name says nothing ("White").
    args = filament_config_args_from_spool(VARIANT_SPOOL, 2)
    assert args["FILAMENT_SUBTYPE"] == "Basic"


def test_the_source_order_decides_which_field_wins():
    spool = dict(VARIANT_SPOOL, extra={"sub_type": "Rapid"})
    assert filament_config_args_from_spool(spool, 2)["FILAMENT_SUBTYPE"] == "Rapid"
    reordered = ("variant", "sub_type", "name_inferred")
    assert filament_config_args_from_spool(spool, 2, reordered)["FILAMENT_SUBTYPE"] == "Basic"


def test_dropping_name_inferred_stops_the_name_being_read():
    declared_only = ("sub_type", "variant")
    args = filament_config_args_from_spool(NAME_CARRIES_SUBTYPE_SPOOL, 1, declared_only)
    assert args["FILAMENT_SUBTYPE"] == "Basic"  # the "Silk" in the name was not read


def test_a_spool_with_no_subtype_anywhere_is_announced_as_the_base_line():
    # Nothing declared, nothing in the name: the slot reads "SUNLU TPU Basic", never "SUNLU TPU".
    args = filament_config_args_from_spool(tpu_spool_named("Ocean Blue"), 0)
    assert args["FILAMENT_SUBTYPE"] == "Basic"


def test_a_standard_subtype_word_is_read_from_anywhere_in_the_name():
    # The owner's spool: the word is first, not after the material, and uppercase on the label;
    # it still files with the canonical casing a Snorca preset name uses.
    args = filament_config_args_from_spool(RAPID_PETG_SPOOL, 0)
    assert args["FILAMENT_SUBTYPE"] == "Rapid"
    silk = filament_config_args_from_spool(NAME_CARRIES_SUBTYPE_SPOOL, 3)
    assert silk["FILAMENT_SUBTYPE"] == "Silk"


def test_any_shore_hardness_grade_in_the_name_files_as_the_subtype():
    # Shore A and Shore D grades run right across the scale, so no list of grades is kept.
    def subtype_of(filament_name):
        return filament_config_args_from_spool(tpu_spool_named(filament_name), 0)[
            "FILAMENT_SUBTYPE"]

    assert subtype_of("TPU 95A Black") == "95A"
    assert subtype_of("SUNLU TPU 82a") == "82A"
    assert subtype_of("Hard TPU 63D") == "63D"
    assert subtype_of("TPU 3D Blue") == "Basic"  # a marketing "3D" is not a grade


def test_a_two_word_subtype_is_matched_as_a_phrase():
    args = filament_config_args_from_spool(FULL_SPECTRUM_SPOOL, 0)
    assert args["FILAMENT_SUBTYPE"] == "Full Spectrum"


def test_set_print_filament_config_gcode_quotes_and_strips_unsafe():
    gcode = set_print_filament_config_gcode(filament_config_args_from_spool(SPOOL, 3))
    assert gcode == EXPECTED_GCODE
    spaced = set_print_filament_config_gcode(
        filament_config_args_from_spool(FULL_SPECTRUM_SPOOL, 1))
    assert 'FILAMENT_SUBTYPE="Full Spectrum"' in spaced
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


def test_a_tagged_lane_is_never_overwritten_with_spoolman_data():
    # The firmware marks a tag-filed channel official; the tag stays the source of truth.
    task = empty_task_config()
    task["filament_official"][3] = True
    writer, macros = build_writer(task)
    writer.apply_spool(3, SPOOL)
    assert all(not cmd.startswith("SET_PRINT_FILAMENT_CONFIG") for cmd in macros.commands)


def test_the_override_switch_lets_a_spoolman_pick_rewrite_a_tagged_lane():
    # The experiment switch: precedence flips, so the same tagged lane is written after all.
    task = empty_task_config()
    task["filament_official"][3] = True
    writer, macros = build_writer(task, spoolman_overrides_tag=True)
    writer.apply_spool(3, SPOOL)
    assert macros.commands[0] == EXPECTED_GCODE


def test_apply_spool_skips_matching_config_but_still_pushes_name():
    # A re-pick after a restart: config persisted, the AFC lane name did not.
    task = empty_task_config()
    task["filament_vendor"][3] = "FlashForge"
    task["filament_type"][3] = "PLA"
    task["filament_sub_type"][3] = "Basic"
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


def test_the_afc_lane_shows_the_composed_spoolman_name():
    writer, macros = build_writer(empty_task_config())
    writer.apply_spool(3, SPOOL)
    assert macros.commands[1] == set_lane_filament_name_gcode(3, "FlashForge PLA Spicy Mint")


def test_config_already_matches():
    task = empty_task_config()
    assert config_already_matches(task, 3, filament_config_clear_args(3)) is True
    assert config_already_matches(
        task, 3, filament_config_args_from_spool(SPOOL, 3)) is False
