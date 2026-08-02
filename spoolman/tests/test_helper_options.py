"""The [spoolman_helper] section options: defaults, coercions, and the bare-printer fallback."""
from spoolman.card_uids import ARRAY_WRITE_FORM, COMMA_SEPARATED_WRITE_FORM, DEFAULT_STRATEGY
from spoolman.helper_options import HelperOptions, read_flag, read_mode, read_text


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
    }))
    assert options.mode == "manual"
    assert options.logging == "debug"
    assert options.track_location is True
    assert options.location == "unU1"
    assert options.card_uids_strategy == ("uid", "sku")
    assert options.card_uids_auto_register is True
    assert options.card_uids_write_form == COMMA_SEPARATED_WRITE_FORM


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
