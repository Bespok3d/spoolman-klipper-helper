# ruff: noqa: PLR2004  Tests assert against literal extruder indexes and gcode strings.
"""Screen/lane writes: composition + the live official-channel and already-matches guards."""
import base64

from spoolman.print_task_writer import (
    PrintTaskWriter,
    config_already_matches,
    filament_config_args_forcing_an_official_channel,
    filament_config_args_from_spool,
    filament_config_clear_args,
    normalize_color_rgba,
    set_lane_filament_name_gcode,
    set_print_filament_config_gcode,
    slicer_filament_description,
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

TRANSLUCENT_SPOOL = {
    "id": 47,
    "filament": {
        "name": "Translucent",
        "material": "PLA",
        "color_hex": "E9E9E7",
        "vendor": {"name": "ELEGOO"},
    },
}

MATTE_SPOOL = {
    "id": 48,
    "filament": {
        "name": "PLA Matte White",
        "material": "PLA",
        "color_hex": "FFFFFF",
        "vendor": {"name": "R3D"},
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


class FakePrintStats:
    def __init__(self, state):
        self.state = state


class FakePrinter:
    def __init__(self, task_config, print_state=""):
        self.task = FakePrintTaskConfig(task_config)
        self.print_stats = FakePrintStats(print_state)

    def lookup_object(self, name, default=None):
        objects = {"print_task_config": self.task, "print_stats": self.print_stats}
        return objects.get(name, default)


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


def build_writer(task_config, spoolman_overrides_tag=False, print_state=""):
    macros = RecordingMacros()
    writer = PrintTaskWriter(
        FakePrinter(task_config, print_state), RecordingLogs(), macros,
        spoolman_overrides_tag=spoolman_overrides_tag,
    )
    return writer, macros


def end_the_print(writer):
    writer.printer.print_stats.state = "complete"


def config_writes_sent(macros):
    return [
        command for command in macros.commands
        if command.startswith("SET_PRINT_FILAMENT_CONFIG")
    ]


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


def test_slicer_filament_description():
    assert slicer_filament_description(NAME_CARRIES_SUBTYPE_SPOOL) == "ZIRO PLA Silk"
    assert slicer_filament_description(DECLARED_SUBTYPE_SPOOL) == "ELEGOO PLA Rapid"
    assert slicer_filament_description(TRANSLUCENT_SPOOL) == "ELEGOO PLA Translucent"
    assert slicer_filament_description(MATTE_SPOOL) == "R3D PLA Matte"


# The whole point of the change: the lane card and the slicer read one string, so a preset named
# what the Device tab shows is also what the panel says. Any second composition drifts from it.
def test_lane_name_is_exactly_what_the_printer_publishes_to_the_slicer():
    published = filament_config_args_from_spool(SPOOL, 3)
    fields = (published["VENDOR"], published["FILAMENT_TYPE"], published["FILAMENT_SUBTYPE"])
    assert slicer_filament_description(SPOOL) == " ".join(fields) == "FlashForge PLA Basic"


# A record with no material is published to nobody: the firmware refuses a filament type it was
# not given, so the lane keeps the panel's own display rather than a half-written name.
def test_no_material_means_no_description():
    assert slicer_filament_description({"filament": {"name": "Silk Gold"}}) == ""
    assert slicer_filament_description({}) == ""


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
    # FORCE=1 is required; without it the firmware raises "official filament, not configurable".
    task = empty_task_config()
    task["filament_official"][3] = True
    writer, macros = build_writer(task, spoolman_overrides_tag=True)
    writer.apply_spool(3, SPOOL)
    assert macros.commands[0] == (
        'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=3 FORCE=1 VENDOR="FlashForge" '
        'FILAMENT_TYPE="PLA" FILAMENT_SUBTYPE="Basic" FILAMENT_COLOR_RGBA=3FD2C5FF'
    )


def test_an_untagged_write_does_not_send_force():
    writer, macros = build_writer(empty_task_config())
    writer.apply_spool(3, SPOOL)
    assert "FORCE=" not in macros.commands[0]


def test_forcing_an_official_channel_inserts_force_after_the_extruder():
    forced = filament_config_args_forcing_an_official_channel(
        filament_config_args_from_spool(SPOOL, 3))
    assert list(forced)[:2] == ["CONFIG_EXTRUDER", "FORCE"]
    assert forced["FORCE"] == "1"


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


def test_a_spool_picked_while_printing_leaves_the_running_print_alone():
    # The firmware resets the live extruder's pressure advance on every config write it takes,
    # so mid print the write waits. The lane card still gets its label: it changes nothing.
    writer, macros = build_writer(empty_task_config(), print_state="printing")
    writer.apply_spool(3, SPOOL)
    assert config_writes_sent(macros) == []
    assert macros.commands[0].startswith("SET_LANE_FILAMENT_NAME EXTRUDER=3")


def test_a_paused_print_holds_the_write_too():
    writer, macros = build_writer(empty_task_config(), print_state="paused")
    writer.apply_spool(3, SPOOL)
    assert config_writes_sent(macros) == []


def test_the_held_write_goes_out_once_the_print_is_over():
    writer, macros = build_writer(empty_task_config(), print_state="printing")
    writer.apply_spool(3, SPOOL)
    end_the_print(writer)
    writer.release_writes_held_during_print()
    assert config_writes_sent(macros) == [EXPECTED_GCODE]


def test_only_the_last_spool_picked_during_a_print_is_written():
    writer, macros = build_writer(empty_task_config(), print_state="printing")
    writer.apply_spool(3, NAME_CARRIES_SUBTYPE_SPOOL)
    writer.apply_spool(3, SPOOL)
    end_the_print(writer)
    writer.release_writes_held_during_print()
    assert config_writes_sent(macros) == [EXPECTED_GCODE]


def test_clearing_an_extruder_while_printing_blanks_the_lane_and_waits():
    task = empty_task_config()
    task["filament_vendor"][3] = "FlashForge"
    task["filament_type"][3] = "PLA"
    writer, macros = build_writer(task, print_state="printing")
    writer.clear_extruder(3)
    assert macros.commands == [set_lane_filament_name_gcode(3, "")]
    end_the_print(writer)
    writer.release_writes_held_during_print()
    assert config_writes_sent(macros) == [
        set_print_filament_config_gcode(filament_config_clear_args(3))
    ]


def test_a_held_write_the_printer_already_carries_is_dropped():
    # Something else put that spool in the slot while the print ran: nothing left to send.
    task = empty_task_config()
    writer, macros = build_writer(task, print_state="printing")
    writer.apply_spool(3, SPOOL)
    task["filament_vendor"][3] = "FlashForge"
    task["filament_type"][3] = "PLA"
    task["filament_sub_type"][3] = "Basic"
    task["filament_color_rgba"][3] = "3FD2C5FF"
    end_the_print(writer)
    writer.release_writes_held_during_print()
    assert config_writes_sent(macros) == []


def test_label_lane_pushes_the_slicer_description():
    writer, macros = build_writer(empty_task_config())
    writer.label_lane(1, SPOOL)
    assert len(macros.commands) == 1
    assert macros.commands[0] == set_lane_filament_name_gcode(1, "FlashForge PLA Basic")


def test_label_lane_with_no_name_pushes_nothing():
    writer, macros = build_writer(empty_task_config())
    writer.label_lane(1, {"filament": {}})
    assert macros.commands == []


def test_the_afc_lane_shows_what_the_slicer_gets():
    writer, macros = build_writer(empty_task_config())
    writer.apply_spool(3, SPOOL)
    assert macros.commands[1] == set_lane_filament_name_gcode(3, "FlashForge PLA Basic")


def test_config_already_matches():
    task = empty_task_config()
    assert config_already_matches(task, 3, filament_config_clear_args(3)) is True
    assert config_already_matches(
        task, 3, filament_config_args_from_spool(SPOOL, 3)) is False
