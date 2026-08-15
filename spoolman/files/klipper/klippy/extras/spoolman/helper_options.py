"""The [spoolman_helper] config section parsed into plain options.

Every read tolerates a bare printer object in place of a Klipper config wrapper (the test
benches construct the helper that way), falling back to the option's default.
"""
from .card_uids import parse_strategy_chain, parse_write_form
from .print_task_writer import DEFAULT_SUBTYPE_SOURCES, KNOWN_SUBTYPE_SOURCES

TRUTHY = ("true", "1", "on", "yes")
POSSIBLE_MODES = ("manual", "auto")


def _read_raw(config, key, default):
    return config.get(key, default) if hasattr(config, "get") else default


# An option nobody wrote keeps its default, so a switch that is on until it is turned off reads
# the same way as one that is off until it is turned on.
def read_flag(config, key, default=False):
    written = str(_read_raw(config, key, "")).strip().lower()
    return written in TRUTHY if written else default


def read_text(config, key):
    return (_read_raw(config, key, "") or "").strip()


def read_mode(config):
    mode = _read_raw(config, "mode", "auto")
    return mode if mode in POSSIBLE_MODES else "auto"


class HelperOptions:
    def __init__(self, config):
        self.mode = read_mode(config)
        self.logging = _read_raw(config, "logging", "info")
        self.track_location = read_flag(config, "track_location")
        self.location = read_text(config, "location")
        self.spoolman_overrides_tag = read_flag(config, "spoolman_overrides_tag")
        self.subtype_sources = parse_strategy_chain(
            _read_raw(config, "subtype_sources", ""),
            default=DEFAULT_SUBTYPE_SOURCES,
            known=KNOWN_SUBTYPE_SOURCES,
        )
        self.card_uids_strategy = parse_strategy_chain(_read_raw(config, "card_uids_strategy", ""))
        self.card_uids_auto_register = read_flag(config, "card_uids_auto_register")
        self.register_from_tag = read_flag(config, "register_from_tag", default=True)
        self.card_uids_write_form = parse_write_form(_read_raw(config, "card_uids_write_form", ""))
