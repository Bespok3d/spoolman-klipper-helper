"""Regression tests for how a tracked spool is labelled (UNKNOWN vs identified) and its colour."""
from spoolman.filament_info import (
    filament_descriptor,
    filament_info_to_string,
    friendly_colour,
    is_untagged_filament,
)

TAGGED = {
    "VENDOR": "ELEGOO", "MAIN_TYPE": "PLA", "SUB_TYPE": "Matte",
    "ARGB_COLOR": "1D6C6AFF", "SPOOL_ID": 104, "SKU": "abc",
}
UNTAGGED_LOADED = {
    "VENDOR": "NONE", "MAIN_TYPE": "NONE", "SUB_TYPE": "NONE",
    "ARGB_COLOR": 4294967295, "SPOOL_ID": None, "SKU": None,
}


def test_identified_spool_keeps_its_vendor_material_label_and_readable_colour():
    assert filament_descriptor(TAGGED) == "ELEGOO PLA Matte"
    assert filament_info_to_string(TAGGED) == (
        "ELEGOO PLA Matte (colour: #1D6C6A (teal), Spoolman id: 104, sku: abc)"
    )


def test_untagged_loaded_spool_is_just_unknown_with_no_colour_or_ids():
    assert filament_descriptor(UNTAGGED_LOADED) == "UNKNOWN"
    # No firmware-default colour / null spool id / sku noise -- present but unidentified.
    assert filament_info_to_string(UNTAGGED_LOADED) == "UNKNOWN"


def test_lane_line_shows_the_tag_card_uid_in_hex_so_it_can_be_bound():
    tagged_with_uid = {**TAGGED, "CARD_UID": [0xE0, 0xCE, 0x1E, 0x3F]}
    assert filament_info_to_string(tagged_with_uid) == (
        "ELEGOO PLA Matte (colour: #1D6C6A (teal), Spoolman id: 104, sku: abc, "
        "card uid: e0ce1e3f)"
    )


def test_lane_line_says_nothing_about_a_uid_when_the_tag_has_none():
    assert filament_info_to_string({**TAGGED, "CARD_UID": [0, 0, 0, 0]}) == (
        "ELEGOO PLA Matte (colour: #1D6C6A (teal), Spoolman id: 104, sku: abc)"
    )
    assert "card uid" not in filament_info_to_string(TAGGED)


def test_generic_vendor_is_treated_as_unknown():
    assert filament_descriptor({"VENDOR": "Generic", "MAIN_TYPE": "PLA"}) == "UNKNOWN"


def test_no_filament_info_is_missing_not_unknown():
    assert filament_info_to_string(None) == "- Missing Filament Info! -"
    assert filament_info_to_string({}) == "- Missing Filament Info! -"


def test_argb_integer_renders_as_rgb_hex_and_name():
    assert friendly_colour(4294967295) == "#FFFFFF (white)"   # 0xFFFFFFFF
    assert friendly_colour(0xFF1D6C6A) == "#1D6C6A (teal)"    # ARGB int, alpha stripped


def test_rgba_hex_string_renders_as_rgb_hex_and_name():
    assert friendly_colour("1D6C6AFF") == "#1D6C6A (teal)"
    assert friendly_colour("#000000") == "#000000 (black)"
    assert friendly_colour("DC2828") == "#DC2828 (red)"


def test_missing_or_bad_colour_reads_unknown():
    assert friendly_colour(None) == "unknown"
    assert friendly_colour("") == "unknown"
    assert friendly_colour("nothex") == "unknown"


def test_is_untagged_filament_cases():
    assert is_untagged_filament(TAGGED) is False
    assert is_untagged_filament(UNTAGGED_LOADED) is True
    assert is_untagged_filament(None) is True
    assert is_untagged_filament({"VENDOR": "Generic"}) is True


def test_debug_level_appends_extra_keys_for_an_identified_spool():
    rendered = filament_info_to_string({**TAGGED, "NFC_ID": "0x42"}, level="debug")
    assert rendered.startswith("ELEGOO PLA Matte (colour: #1D6C6A (teal)")
    assert "NFC_ID->0x42" in rendered
