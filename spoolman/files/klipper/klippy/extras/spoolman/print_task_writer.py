"""Mirror a resolved Spoolman spool's color/material into the U1 print task (screen + AFC).

Both the touchscreen and the AFC panel read color/material from the firmware print_task_config
(by physical extruder). A manual widget pick has no tag to feed them, so the spool's identity is
written back with the firmware's own SET_PRINT_FILAMENT_CONFIG, and the same description the
slicer gets is put on the lane card with SET_LANE_FILAMENT_NAME. RFID-tagged (official) channels are
not written by default: the tag is the source of truth there, and the firmware raises on such a
write (touchscreen shows a "System Anomaly" popup). Two things write one anyway, with FORCE=1 so
the firmware accepts it. The spoolman_overrides_tag experiment switch flips that precedence for
every Spoolman pick. And the spool the tag itself names is written when its Spoolman record holds
more colours than the tag filed on the lane, so a multi colour spool is never left showing the
one colour its tag carries. Running in-process means the official check and the already-matches
check read the LIVE print_task_config object, so the stale-subscription race the old Moonraker
bridge had does not exist here.

What goes into that config is also what a slicer reads back: the spool's own brand and material,
plus a sub-type when the Spoolman record carries one. Snapmaker Orca lists the spool when a
filament preset, shipped or user-made, carries exactly that glued name.
"""
import base64
import re
from typing import Any, NamedTuple

from .active_spool import coerce_spool_id
from .spool_colours import (
    FIRMWARE_COLOURS_SEPARATOR,
    colours_in_spoolman,
    multi_colour_args,
    tag_describing_spool,
)
from .u1_tools import ACTIVE_PRINT_STATES, print_state

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
# A multi colour write names its colours in COLORS instead of FILAMENT_COLOR_RGBA.
FILAMENT_FIELD_ARGS = (*CONFIG_FIELD_BY_ARG, "COLORS")
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
    "Starlight",
    "Luminous",
    "Odorless",
    "Sparkle",
    "Support",
    "Rainbow",
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


# color_hex comes first because it may carry an alpha; a filament that files its one colour
# in the multi colour list instead still gets that colour.
def _single_colour_hex(spool):
    color_hex = _filament_record(spool).get("color_hex")
    if color_hex:
        return color_hex
    return next(iter(colours_in_spoolman(spool)), "")


# tag_info is the tag on the lane when it describes this same spool; its colours win when it
# names more of them than the Spoolman record does.
def filament_config_args_from_spool(
    spool, physical_extruder, subtype_sources=DEFAULT_SUBTYPE_SOURCES, tag_info=None
):
    color = normalize_color_rgba(_single_colour_hex(spool))
    multi_colour = multi_colour_args(spool, tag_info)
    fields = _slicer_filament_fields(spool, subtype_sources)
    args = {"CONFIG_EXTRUDER": str(physical_extruder)}
    if fields:
        args["VENDOR"], args["FILAMENT_TYPE"], args["FILAMENT_SUBTYPE"] = fields
    if multi_colour:
        return {**args, **multi_colour}
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
    return any(arg in args for arg in FILAMENT_FIELD_ARGS)


def _strip_unsafe(value):
    return "".join(char for char in value if char not in GCODE_UNSAFE_CHARS)


def _format_arg(key, value):
    if key in QUOTED_ARGS:
        return f'{key}="{_strip_unsafe(value)}"'
    return f"{key}={value}"


def set_print_filament_config_gcode(args):
    pairs = " ".join(_format_arg(key, value) for key, value in args.items())
    return f"SET_PRINT_FILAMENT_CONFIG {pairs}"


# The firmware refuses SET_PRINT_FILAMENT_CONFIG on an official (RFID-tagged) channel unless
# FORCE=1 is present, the same flag CLEAR_ALL_SPOOLS already sends. Inserted after
# CONFIG_EXTRUDER so the command matches that reset's shape.
def filament_config_args_forcing_an_official_channel(args):
    forced = {}
    for key, value in args.items():
        forced[key] = value
        if key == "CONFIG_EXTRUDER":
            forced["FORCE"] = "1"
    return forced


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


# The colours a lane holds, as the firmware files them: a tag fills all five slots and says how
# many count. None when the config carries no colour list for the lane.
def _lane_colours(print_task_config, physical_extruder):
    lane_multi = _value_at(print_task_config.get("filament_color_multi") or [], physical_extruder)
    if not isinstance(lane_multi, dict) or "colors" not in lane_multi:
        return None
    colour_count = lane_multi.get("nums", 1)
    return [str(colour).upper() for colour in lane_multi["colors"][:colour_count]]


def _lane_alpha(print_task_config, physical_extruder):
    lane_multi = _value_at(print_task_config.get("filament_color_multi") or [], physical_extruder)
    return lane_multi.get("alpha") if isinstance(lane_multi, dict) else None


def _desired_colours(desired_args):
    if "COLORS" in desired_args:
        return desired_args["COLORS"].split(FIRMWARE_COLOURS_SEPARATOR)
    return [desired_args["FILAMENT_COLOR_RGBA"][:RGB_HEX_LENGTH]]


def _multi_colours_match(print_task_config, physical_extruder, desired_args):
    return (
        _lane_colours(print_task_config, physical_extruder) == _desired_colours(desired_args)
        and _lane_alpha(print_task_config, physical_extruder) == int(desired_args["ALPHA"])
    )


# A lane left holding several colours is not matched by a single colour that happens to equal
# its first one. A config with no colour list for the lane is judged on the RGBA alone.
def _single_colour_matches(print_task_config, physical_extruder, desired_args):
    lane_colours = _lane_colours(print_task_config, physical_extruder)
    return lane_colours is None or lane_colours == _desired_colours(desired_args)


def _colour_checks(print_task_config, physical_extruder, desired_args):
    if "COLORS" in desired_args:
        return [_multi_colours_match(print_task_config, physical_extruder, desired_args)]
    if "FILAMENT_COLOR_RGBA" in desired_args:
        return [_single_colour_matches(print_task_config, physical_extruder, desired_args)]
    return []


def config_already_matches(print_task_config, physical_extruder, desired_args):
    field_checks = [
        _value_at(print_task_config.get(field, []), physical_extruder) == desired_args[arg]
        for arg, field in CONFIG_FIELD_BY_ARG.items()
        if arg in desired_args
    ]
    checks = field_checks + _colour_checks(print_task_config, physical_extruder, desired_args)
    return bool(checks) and all(checks)


def channel_is_official(filament_official, physical_extruder):
    return bool(_value_at(filament_official or [], physical_extruder))


# A tag can file fewer colours than the spool it names carries, a dual colour spool on a one
# colour tag. Once written, the lane holds as many colours as the write named, so it is not
# written again until the tag is read anew and the printer files its fewer colours once more.
# A lane with no colour list gives nothing to count.
def write_names_more_colours_than_lane(print_task_config, physical_extruder, desired_args):
    lane_colours = _lane_colours(print_task_config, physical_extruder)
    if "COLORS" not in desired_args or lane_colours is None:
        return False
    return len(_desired_colours(desired_args)) > len(lane_colours)


# The spool a held write was made for is kept, not whether the tag named it then: a tag read
# during the print can put another reel on the lane before the write is released.
class HeldWrite(NamedTuple):
    desired_args: dict[str, str]
    failure_problem: str
    spool_id: Any


class PrintTaskWriter:
    # spoolman_overrides_tag flips the lane precedence: the Spoolman record then rewrites even a
    # tag-filed (official) channel, and FORCE=1 goes with that write so the firmware accepts it.
    # Off, the tag stays the source of truth, unless the spool it names carries more colours in
    # Spoolman: that spool's record is then written over the tag.
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
        self.writes_held_during_print = {}

    # Only a write already judged worth sending gets here, so an official lane means the override
    # or a spool naming more colours than its tag let it through, and the firmware needs FORCE=1.
    def _firmware_write_args(self, physical_extruder, desired):
        filament_official = self._live_task_config().get("filament_official")
        lane_is_official = channel_is_official(filament_official, physical_extruder)
        if not (self.spoolman_overrides_tag or lane_is_official):
            return desired
        return filament_config_args_forcing_an_official_channel(desired)

    def _live_task_config(self):
        task = self.printer.lookup_object("print_task_config", None)
        config = getattr(task, "print_task_config", None)
        return config if isinstance(config, dict) else {}

    # tag_names_this_spool says the tag on the lane was resolved to the very spool being written,
    # the only spool whose richer colours may be written over a tag-filed lane.
    def _should_write(self, physical_extruder, desired_args, tag_names_this_spool=False):
        config = self._live_task_config()
        if config_already_matches(config, physical_extruder, desired_args):
            return False
        if self.spoolman_overrides_tag:
            return True
        if not channel_is_official(config.get("filament_official"), physical_extruder):
            return True
        return tag_names_this_spool and write_names_more_colours_than_lane(
            config, physical_extruder, desired_args
        )

    # The firmware runs FLOW_RESET_K on every filament config write it accepts, which resets
    # the live extruder's pressure advance. A write that lands while the printer is printing
    # therefore changes how the running print extrudes, so it waits: the last write per extruder
    # is held and sent once the print is over. The lane name is never held, because the AFC panel
    # label touches nothing the print is doing.
    def _send_or_hold_filament_config(
        self, physical_extruder, desired_args, failure_problem, spool_id=None
    ):
        if print_state(self.printer) in ACTIVE_PRINT_STATES:
            self.writes_held_during_print[physical_extruder] = HeldWrite(
                desired_args, failure_problem, spool_id,
            )
            self.logs.verbose(
                f"Holding filament config for extruder {physical_extruder} until the print ends"
            )
            return
        self.macros.run(
            set_print_filament_config_gcode(
                self._firmware_write_args(physical_extruder, desired_args)
            ),
            failure_problem,
        )

    # Called when the print leaves printing/paused, by the same coordinator that drains its own
    # held writes there. Each one is re-judged against the lane now: a write held for a spool the
    # lane no longer holds is dropped, because a tool remapped onto the lane or a pick made since
    # put another spool there. The rest are re-judged against the live config and the tag: the
    # firmware may already carry what was held, the lane may have become official meanwhile, and
    # its tag may no longer name the spool the write was held for.
    def release_writes_held_during_print(self, tag_on_lane, spool_on_lane):
        held = self.writes_held_during_print
        self.writes_held_during_print = {}
        for physical_extruder, held_write in held.items():
            if coerce_spool_id(held_write.spool_id) != spool_on_lane(physical_extruder):
                continue
            self._release_held_write(physical_extruder, held_write, tag_on_lane(physical_extruder))

    # The newest judgement for a lane is the one that holds: a write held for a spool since
    # swapped out or cleared would otherwise land over the lane's new state when the print ends.
    def _drop_held_write(self, physical_extruder):
        self.writes_held_during_print.pop(physical_extruder, None)

    def _release_held_write(self, physical_extruder, held_write, tag_on_lane_now):
        tag_info = tag_describing_spool(tag_on_lane_now, held_write.spool_id)
        tag_names_this_spool = tag_info is not None
        if not self._should_write(physical_extruder, held_write.desired_args, tag_names_this_spool):
            return
        self._send_or_hold_filament_config(physical_extruder, *held_write)

    # tag_on_lane is the tag report the lane holds, if any: when it describes this same spool and
    # names more colours than the Spoolman record, a multi colour tag is not flattened; when the
    # Spoolman record names more, those colours are written even over the tag.
    def apply_spool(self, physical_extruder, spool, tag_on_lane=None):
        spool_id = (spool or {}).get("id")
        tag_info = tag_describing_spool(tag_on_lane, spool_id)
        tag_names_this_spool = tag_info is not None
        desired = filament_config_args_from_spool(
            spool, physical_extruder, self.subtype_sources, tag_info
        )
        if not has_filament_fields(desired):
            self._drop_held_write(physical_extruder)
            return
        if self._should_write(physical_extruder, desired, tag_names_this_spool):
            self._send_or_hold_filament_config(
                physical_extruder, desired,
                f"could not write filament config for extruder {physical_extruder}",
                spool_id,
            )
        else:
            self._drop_held_write(physical_extruder)
        return self.label_lane(physical_extruder, spool)

    def clear_extruder(self, physical_extruder):
        desired = filament_config_clear_args(physical_extruder)
        if not self._should_write(physical_extruder, desired):
            self._drop_held_write(physical_extruder)
            return
        self._send_or_hold_filament_config(
            physical_extruder, desired,
            f"could not clear filament config for extruder {physical_extruder}",
        )
        self.clear_lane_label(physical_extruder)

    # Pushed even when the persisted config already matched, so a re-pick after a restart
    # (which clears the AFC lane's name) re-labels the lane. RFID-resolved lanes go through
    # apply_spool, which always names the lane even when it leaves an official channel
    # alone (override off). When it does write one, it sends FORCE=1 so the firmware
    # accepts the Spoolman record on a tagged lane. Hands back the name it put
    # on the lane, so a caller holding a spool record straight from Spoolman can tell
    # whether that record named the lane or whether it has to go and ask.
    def label_lane(self, physical_extruder, spool):
        name = slicer_filament_description(spool, self.subtype_sources)
        if name:
            self._push_name(physical_extruder, name)
        return name

    # An empty label is how a lane gives its name back: the AFC panel emits the field only when
    # it is set, so blanking it restores the panel's own display instead of the last spool's name.
    def clear_lane_label(self, physical_extruder):
        self._push_name(physical_extruder, "")

    def _push_name(self, physical_extruder, name):
        self.macros.run(
            set_lane_filament_name_gcode(physical_extruder, name),
            f"could not set lane filament name for extruder {physical_extruder}",
        )
