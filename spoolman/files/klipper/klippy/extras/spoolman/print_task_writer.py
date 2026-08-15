"""Mirror a resolved Spoolman spool's color/material into the U1 print task (screen + AFC).

Both the touchscreen and the AFC panel read color/material from the firmware print_task_config
(by physical extruder). A manual widget pick has no tag to feed them, so the spool's identity is
written back with the firmware's own SET_PRINT_FILAMENT_CONFIG, and the same description the
slicer gets is put on the lane card with SET_LANE_FILAMENT_NAME. RFID-tagged (official) channels are
not written by default: the tag is the source of truth there, and the firmware raises on such a
write (touchscreen shows
a "System Anomaly" popup). The spoolman_overrides_tag experiment switch flips that precedence
and lets the Spoolman pick write anyway. Running in-process means the official check and the
already-matches check read the LIVE print_task_config object, so the stale-subscription race the
old Moonraker bridge had does not exist here.

What goes into that config is also what a slicer reads back: the spool's own brand and material,
plus a sub-type when the Spoolman record carries one. Snapmaker Orca lists the spool when a
filament preset, shipped or user-made, carries exactly that glued name.
"""
import base64
import re

RGB_HEX_LENGTH = 6
RGBA_HEX_LENGTH = 8
OPAQUE_ALPHA = "FF"
GCODE_UNSAFE_CHARS = ('"', "'", "\n", "\r", ";")
CONFIG_FIELD_BY_ARG = {
    "VENDOR": "filament_vendor",
    "FILAMENT_TYPE": "filament_type",
    "FILAMENT_SUBTYPE": "filament_sub_type",
    "FILAMENT_COLOR_RGBA": "filament_color_rgba",
}
QUOTED_ARGS = ("VENDOR", "FILAMENT_TYPE", "FILAMENT_SUBTYPE")
# Spoolman has no sub-type field of its own; the firmware requires one next to FILAMENT_TYPE
# ("incomplete parameters"), and Orca matches on it. Three sources can carry it, tried in the
# order the subtype_sources option lists, first non-empty one wins: a "subtype" extra field the
# user added in Spoolman, the "variant" extra field the extended firmware writes into Spoolman,
# and the filament name itself. With none of them the sub-type reads "Basic", the name Snapmaker
# gives its own base line and what the RFID reader already files for a tag carrying no sub-type,
# so a spool is announced as brand + material + sub-type either way.
BASE_LINE_SUBTYPE = "Basic"
SUBTYPE_PROPERTY_KEYS = ("subtype", "sub_type")
VARIANT_PROPERTY_KEYS = ("variant",)
SUBTYPE_SOURCE_DECLARED = "sub_type"
SUBTYPE_SOURCE_VARIANT = "variant"
SUBTYPE_SOURCE_NAME = "name_inferred"
KNOWN_SUBTYPE_SOURCES = (SUBTYPE_SOURCE_DECLARED, SUBTYPE_SOURCE_VARIANT, SUBTYPE_SOURCE_NAME)
DEFAULT_SUBTYPE_SOURCES = KNOWN_SUBTYPE_SOURCES
# The standard sub-type vocabulary. Snorca enforces no list (any string glues into the preset
# name), so these are the sub-type words the shipped preset names actually use: Snapmaker's own
# profiles plus the vendor profiles OrcaSlicer ships (Basic, HF, Matte, Silk, Hyper, Rapido,
# shore grades, ...). Longest phrase first; a match files the canonical casing given here, so a
# filament named "RAPID PETG Blue" files "Rapid". Anything unusual goes in the extra field.
STANDARD_SUBTYPES = (
    "Full Spectrum",
    "High Speed",
    "High-Flow",
    "Translucent",
    "Transparent",
    "SnapSpeed",
    "Breakaway",
    "Luminous",
    "Odorless",
    "Sparkle",
    "Support",
    "Rapido",
    "Marble",
    "Galaxy",
    "Matte",
    "Metal",
    "Basic",
    "Hyper",
    "Rapid",
    "Tough+",
    "Tough",
    "Silk+",
    "Silk",
    "Glow",
    "Wood",
    "Aero",
    "Eco",
    "HF",
    "HS",
)
# Shore hardness grades (Shore A soft, Shore D hard): any two-or-three-digit number ending in A
# or D files as the sub-type (95A, 82A, 63D), so no list of grades is kept. Two digits minimum,
# or a marketing "3D" in a name would file as a grade.
SHORE_GRADE_TOKEN = re.compile(r"\d{2,3}[AD]", re.IGNORECASE)
# The firmware's empty/cleared print_task_config slot (DEFAULT_PRINT_TASK_CONFIG): vendor, type
# and sub-type read "NONE", colour reads opaque white.
EMPTY_FIELD = "NONE"
EMPTY_COLOR_RGBA = "FFFFFFFF"


def normalize_color_rgba(color_hex):
    cleaned = (color_hex or "").strip().upper()
    if len(cleaned) == RGBA_HEX_LENGTH:
        return cleaned
    if len(cleaned) == RGB_HEX_LENGTH:
        return cleaned + OPAQUE_ALPHA
    return ""


def _filament_record(spool):
    return (spool or {}).get("filament") or {}


# Spoolman stores a text extra field as a JSON string, hence the quote stripping. A field set on
# the spool itself beats the same field on its filament: it is the more specific record.
def _extra_field_on_the_record(spool, property_keys):
    filament = _filament_record(spool)
    extra_fields = {**(filament.get("extra") or {}), **((spool or {}).get("extra") or {})}
    values = (
        str(extra_fields.get(property_key) or "").strip().strip('"').strip()
        for property_key in property_keys
    )
    return next((value for value in values if value), "")


def _subtype_declared_on_the_record(spool):
    return _extra_field_on_the_record(spool, SUBTYPE_PROPERTY_KEYS)


# The extended firmware files the sub-type in its own non-standard "variant" extra field.
def _variant_declared_on_the_record(spool):
    return _extra_field_on_the_record(spool, VARIANT_PROPERTY_KEYS)


def _phrase_found_in_name(name_words_lower, subtype):
    subtype_words = subtype.lower().split()
    window_starts = range(len(name_words_lower) - len(subtype_words) + 1)
    return any(
        name_words_lower[start : start + len(subtype_words)] == subtype_words
        for start in window_starts
    )


def _shore_grade_in_the_name(name_words_lower):
    grades = (word.upper() for word in name_words_lower if SHORE_GRADE_TOKEN.fullmatch(word))
    return next(grades, "")


def _standard_subtype_in_the_name(filament_name):
    name_words_lower = (filament_name or "").lower().split()
    matches = (
        subtype for subtype in STANDARD_SUBTYPES if _phrase_found_in_name(name_words_lower, subtype)
    )
    return next(matches, "") or _shore_grade_in_the_name(name_words_lower)


def _subtype_inferred_from_the_name(spool):
    return _standard_subtype_in_the_name(_filament_record(spool).get("name"))


SUBTYPE_READER_BY_SOURCE = {
    SUBTYPE_SOURCE_DECLARED: _subtype_declared_on_the_record,
    SUBTYPE_SOURCE_VARIANT: _variant_declared_on_the_record,
    SUBTYPE_SOURCE_NAME: _subtype_inferred_from_the_name,
}


def subtype_for_slicers(spool, subtype_sources=DEFAULT_SUBTYPE_SOURCES):
    readers = (SUBTYPE_READER_BY_SOURCE.get(source) for source in subtype_sources)
    found = (read_subtype(spool) for read_subtype in readers if read_subtype)
    return next((subtype for subtype in found if subtype), BASE_LINE_SUBTYPE)


# The three words the printer publishes for the lane: brand, material, sub-type. Empty when the
# record names no material, because the firmware refuses a filament type it was not given and a
# slicer has nothing to match on.
def _slicer_filament_fields(spool, subtype_sources):
    filament = _filament_record(spool)
    material = filament.get("material") or ""
    if not material:
        return ()
    vendor = (filament.get("vendor") or {}).get("name") or ""
    return (vendor, material, subtype_for_slicers(spool, subtype_sources))


def filament_config_args_from_spool(
    spool, physical_extruder, subtype_sources=DEFAULT_SUBTYPE_SOURCES
):
    color = normalize_color_rgba(_filament_record(spool).get("color_hex") or "")
    fields = _slicer_filament_fields(spool, subtype_sources)
    args = {"CONFIG_EXTRUDER": str(physical_extruder)}
    if fields:
        args["VENDOR"], args["FILAMENT_TYPE"], args["FILAMENT_SUBTYPE"] = fields
    if color:
        args["FILAMENT_COLOR_RGBA"] = color
    return args


def filament_config_clear_args(physical_extruder):
    return {
        "CONFIG_EXTRUDER": str(physical_extruder),
        "VENDOR": EMPTY_FIELD,
        "FILAMENT_TYPE": EMPTY_FIELD,
        "FILAMENT_SUBTYPE": EMPTY_FIELD,
        "FILAMENT_COLOR_RGBA": EMPTY_COLOR_RGBA,
    }


def has_filament_fields(args):
    return any(arg in args for arg in CONFIG_FIELD_BY_ARG)


def _strip_unsafe(value):
    return "".join(char for char in value if char not in GCODE_UNSAFE_CHARS)


def _format_arg(key, value):
    if key in QUOTED_ARGS:
        return f'{key}="{_strip_unsafe(value)}"'
    return f"{key}={value}"


def set_print_filament_config_gcode(args):
    pairs = " ".join(_format_arg(key, value) for key, value in args.items())
    return f"SET_PRINT_FILAMENT_CONFIG {pairs}"


# The name a person reads on the AFC lane card is the same filament description the printer
# publishes for that lane to the slicer, "SUNLU PETG Basic". One string everywhere: the card, the
# Device tab in Snapmaker Orca, and the preset the slicer matches. Whatever the user does to
# control it, in Spoolman or through the sub-type sources, moves all three together. The panel
# cannot compose this itself: it shows the Spoolman filament.name alone, and only when it can
# resolve the spool.
def slicer_filament_description(spool, subtype_sources=DEFAULT_SUBTYPE_SOURCES):
    return " ".join(part for part in _slicer_filament_fields(spool, subtype_sources) if part)


# Base64 so a name with spaces survives Klipper's whitespace-splitting gcode parser.
def set_lane_filament_name_gcode(physical_extruder, name):
    encoded = base64.b64encode(name.encode("utf-8")).decode("ascii")
    return f"SET_LANE_FILAMENT_NAME EXTRUDER={physical_extruder} NAME_B64={encoded}"


def _value_at(values, index):
    return values[index] if 0 <= index < len(values) else None


def config_already_matches(print_task_config, physical_extruder, desired_args):
    checks = [
        _value_at(print_task_config.get(field, []), physical_extruder) == desired_args[arg]
        for arg, field in CONFIG_FIELD_BY_ARG.items()
        if arg in desired_args
    ]
    return bool(checks) and all(checks)


def channel_is_official(filament_official, physical_extruder):
    return bool(_value_at(filament_official or [], physical_extruder))


class PrintTaskWriter:
    # spoolman_overrides_tag flips the lane precedence for experiments: the Spoolman pick then
    # rewrites even a tag-filed (official) channel. Off, the tag stays the source of truth.
    def __init__(
        self,
        printer,
        logs,
        macros,
        spoolman_overrides_tag=False,
        subtype_sources=DEFAULT_SUBTYPE_SOURCES,
    ):
        self.printer = printer
        self.logs = logs
        self.macros = macros
        self.spoolman_overrides_tag = spoolman_overrides_tag
        self.subtype_sources = subtype_sources

    def _live_task_config(self):
        task = self.printer.lookup_object("print_task_config", None)
        config = getattr(task, "print_task_config", None)
        return config if isinstance(config, dict) else {}

    def _should_write(self, physical_extruder, desired_args):
        config = self._live_task_config()
        if config_already_matches(config, physical_extruder, desired_args):
            return False
        if self.spoolman_overrides_tag:
            return True
        return not channel_is_official(config.get("filament_official"), physical_extruder)

    def apply_spool(self, physical_extruder, spool):
        desired = filament_config_args_from_spool(spool, physical_extruder, self.subtype_sources)
        if not has_filament_fields(desired):
            return
        if self._should_write(physical_extruder, desired):
            self.macros.run(
                set_print_filament_config_gcode(desired),
                f"could not write filament config for extruder {physical_extruder}",
            )
        self._apply_name(physical_extruder, spool)

    def clear_extruder(self, physical_extruder):
        desired = filament_config_clear_args(physical_extruder)
        if not self._should_write(physical_extruder, desired):
            return
        self.macros.run(
            set_print_filament_config_gcode(desired),
            f"could not clear filament config for extruder {physical_extruder}",
        )
        self.clear_lane_label(physical_extruder)

    # Pushed even when the persisted config already matched, so a re-pick after a restart
    # (which clears the AFC lane's name) re-labels the lane. Also called directly for
    # RFID-resolved lanes: the AFC panel only shows a name a helper pushed, so every resolved
    # lane gets its label, not just manual picks.
    # Hands back the name it put on the lane, so a caller holding a spool record straight from
    # Spoolman can tell whether that record named the lane or whether it has to go and ask.
    def label_lane(self, physical_extruder, spool):
        name = slicer_filament_description(spool, self.subtype_sources)
        if name:
            self._push_name(physical_extruder, name)
        return name

    # An empty label is how a lane gives its name back: the AFC panel emits the field only when
    # it is set, so blanking it restores the panel's own display instead of the last spool's name.
    def clear_lane_label(self, physical_extruder):
        self._push_name(physical_extruder, "")

    def _apply_name(self, physical_extruder, spool):
        self.label_lane(physical_extruder, spool)

    def _push_name(self, physical_extruder, name):
        self.macros.run(
            set_lane_filament_name_gcode(physical_extruder, name),
            f"could not set lane filament name for extruder {physical_extruder}",
        )
