"""The [spoolman_helper] section options: defaults, coercions, and the bare-printer fallback."""
from spoolman.card_uids import ARRAY_WRITE_FORM, COMMA_SEPARATED_WRITE_FORM, DEFAULT_STRATEGY
from spoolman.helper_options import HelperOptions, read_flag, read_mode, read_text
from spoolman.print_task_writer import DEFAULT_SUBTYPE_SOURCES


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class BarePrinter:
    """A test-bench helper is sometimes built straight from a printer: no .get at all."""


def test_defaults_from_an_empty_section():
    options = HelperOptions(FakeConfig({}))
    assert options.mode == "auto"
    assert options.logging == "info"
    assert options.track_location is False
    assert options.location == ""
    assert options.spoolman_overrides_tag is False
    assert options.subtype_sources == tuple(DEFAULT_SUBTYPE_SOURCES)
    assert options.card_uids_strategy == tuple(DEFAULT_STRATEGY)
    assert options.card_uids_auto_register is False
    assert options.card_uids_write_form == ARRAY_WRITE_FORM


def test_bare_printer_object_yields_all_defaults():
    options = HelperOptions(BarePrinter())
    assert options.mode == "auto"
    assert options.logging == "info"
    assert options.card_uids_strategy == tuple(DEFAULT_STRATEGY)


def test_configured_section_is_read_through():
    options = HelperOptions(FakeConfig({
        "mode": "manual",
        "logging": "debug",
        "track_location": "true",
        "location": " unU1 ",
        "card_uids_strategy": "uid,sku",
        "card_uids_auto_register": "TRUE",
        "card_uids_write_form": " Comma_Separated ",
        "spoolman_overrides_tag": "on",
        "subtype_sources": " Variant , sub_type ",
    }))
    assert options.spoolman_overrides_tag is True
    assert options.subtype_sources == ("variant", "sub_type")
    assert options.mode == "manual"
    assert options.logging == "debug"
    assert options.track_location is True
    assert options.location == "unU1"
    assert options.card_uids_strategy == ("uid", "sku")
    assert options.card_uids_auto_register is True
    assert options.card_uids_write_form == COMMA_SEPARATED_WRITE_FORM


def test_an_unreadable_subtype_source_list_keeps_the_shipped_order():
    # A typo, or a template variable the printer never got substituted.
    options = HelperOptions(FakeConfig({"subtype_sources": "$SPOOLMAN_SUBTYPE_SOURCES"}))
    assert options.subtype_sources == tuple(DEFAULT_SUBTYPE_SOURCES)


def test_a_section_that_reached_the_printer_unsubstituted_still_loads():
    """Every value still its template variable: the printer keeps working on the shipped defaults.

    An option the installer never filled in reaches the printer as literal `$NAME` text. Reading
    one of those strictly (Klipper's getboolean) raises, Klipper then refuses the whole config, and
    the printer is down: not just this plugin, every plugin on it. Nothing here may raise.
    """
    options = HelperOptions(FakeConfig({
        "mode": "$SPOOLMAN_MODE",
        "logging": "$SPOOLMAN_LOGGING",
        "track_location": "$SPOOLMAN_TRACK_LOCATION",
        "location": "$SPOOLMAN_LOCATION",
        "spoolman_overrides_tag": "$SPOOLMAN_OVERRIDES_TAG",
        "subtype_sources": "$SPOOLMAN_SUBTYPE_SOURCES",
        "card_uids_strategy": "$CARD_UIDS_STRATEGY",
        "card_uids_auto_register": "$CARD_UIDS_AUTO_REGISTER",
        "card_uids_write_form": "$CARD_UIDS_WRITE_FORM",
    }))
    assert options.mode == "auto"
    assert options.track_location is False
    assert options.location == "$SPOOLMAN_LOCATION"
    assert options.spoolman_overrides_tag is False
    assert options.subtype_sources == tuple(DEFAULT_SUBTYPE_SOURCES)
    assert options.card_uids_strategy == tuple(DEFAULT_STRATEGY)
    assert options.card_uids_auto_register is False
    assert options.card_uids_write_form == ARRAY_WRITE_FORM


def test_unknown_write_form_falls_back_to_the_array():
    options = HelperOptions(FakeConfig({"card_uids_write_form": "yolo"}))
    assert options.card_uids_write_form == ARRAY_WRITE_FORM


def test_unknown_mode_falls_back_to_auto():
    assert read_mode(FakeConfig({"mode": "yolo"})) == "auto"


def test_flag_accepts_the_truthy_spellings():
    for spelling in ("true", "1", "on", "YES", " True "):
        assert read_flag(FakeConfig({"track_location": spelling}), "track_location") is True
    assert read_flag(FakeConfig({"track_location": "nope"}), "track_location") is False


def test_text_strips_and_tolerates_none():
    assert read_text(FakeConfig({"location": None}), "location") == ""
    assert read_text(FakeConfig({"location": "  shelf A "}), "location") == "shelf A"
