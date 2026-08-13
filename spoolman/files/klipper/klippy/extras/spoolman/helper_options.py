"""The [spoolman_helper] config section parsed into plain options.

Every read tolerates a bare printer object in place of a Klipper config wrapper (the test
benches construct the helper that way), falling back to the option's default.
"""
from .card_uids import parse_strategy_chain, parse_write_form

TRUTHY = ("true", "1", "on", "yes")
FALSY = ("false", "0", "off", "no")
POSSIBLE_MODES = ("manual", "auto")


def _read_raw(config, key, default):
    return config.get(key, default) if hasattr(config, "get") else default


def read_flag(config, key):
    return str(_read_raw(config, key, "false")).strip().lower() in TRUTHY


# Only an explicit off turns it off: a value that never reached the printer (an unsubstituted
# $VAR) reads as on, the setting's own default, instead of halting Klipper or silently
# switching the feature off.
def read_flag_default_on(config, key):
    return str(_read_raw(config, key, "true")).strip().lower() not in FALSY


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
        self.force_generic_vendor = read_flag_default_on(config, "force_generic_vendor")
        self.location = read_text(config, "location")
        self.card_uids_strategy = parse_strategy_chain(_read_raw(config, "card_uids_strategy", ""))
        self.card_uids_auto_register = read_flag(config, "card_uids_auto_register")
        self.card_uids_write_form = parse_write_form(_read_raw(config, "card_uids_write_form", ""))
