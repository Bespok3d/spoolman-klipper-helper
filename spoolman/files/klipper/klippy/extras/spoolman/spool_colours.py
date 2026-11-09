"""The colour arguments a spool's filament config write carries, single or multi colour.

A spool can carry several colours (silk dual and tri colour, coaxial, gradient). Spoolman files
them in multi_color_hexes and leaves color_hex empty; an RFID tag files them as COLOR_NUMS and
RGB_1..RGB_5. The firmware takes them the same way it reads them off a tag: COLOR_NUMS, COLORS,
MULTI_MODE and ALPHA on SET_PRINT_FILAMENT_CONFIG.

The richest record wins. When the tag on the lane and the Spoolman record describe the same spool,
whichever names more colours is the one written, so a multi colour spool is never flattened to a
single colour by the other source. A tie goes to Spoolman, the record being applied.
"""
from .active_spool import coerce_spool_id
from .filament_info import is_untagged_filament, rgb_hex

FIRMWARE_COLOR_NUMS_MAX = 5
SINGLE_COLOUR_COUNT = 1
OPAQUE_ALPHA_VALUE = 255
# The firmware's own default. What the other values of MULTI_MODE mean is not documented in the
# firmware, so the mode a tag carries is never guessed at and every write sends the default.
DEFAULT_MULTI_MODE = "0"
SPOOLMAN_COLOUR_SEPARATOR = ","
FIRMWARE_COLOURS_SEPARATOR = ","


def colours_in_spoolman(spool):
    filament = (spool or {}).get("filament") or {}
    listed = (filament.get("multi_color_hexes") or "").split(SPOOLMAN_COLOUR_SEPARATOR)
    multi_colours = [rgb_hex(listed_colour) for listed_colour in listed]
    named = [colour for colour in multi_colours if colour]
    if named:
        return named
    single_colour = rgb_hex(filament.get("color_hex") or "")
    return [single_colour] if single_colour else []


def colours_on_tag(tag_info):
    if is_untagged_filament(tag_info):
        return []
    colour_count = tag_info.get("COLOR_NUMS")
    if not isinstance(colour_count, int) or colour_count < SINGLE_COLOUR_COUNT:
        single_colour = rgb_hex(tag_info.get("ARGB_COLOR"))
        return [single_colour] if single_colour else []
    rgb_values = (tag_info.get(f"RGB_{position}") for position in range(1, colour_count + 1))
    return [rgb_hex(rgb_value) for rgb_value in rgb_values if rgb_value is not None]


def _tag_alpha(tag_info):
    alpha = tag_info.get("ALPHA")
    return alpha if isinstance(alpha, int) else OPAQUE_ALPHA_VALUE


def _multi_colour_args(colours, alpha):
    written = colours[:FIRMWARE_COLOR_NUMS_MAX]
    return {
        "COLOR_NUMS": str(len(written)),
        "COLORS": FIRMWARE_COLOURS_SEPARATOR.join(written),
        "MULTI_MODE": DEFAULT_MULTI_MODE,
        "ALPHA": str(alpha),
    }


# Empty when neither source names more than one colour: the lone colour is then written the
# single colour way, exactly as before multi colour spools were known.
def multi_colour_args(spool, tag_info):
    spoolman_colours = colours_in_spoolman(spool)
    tag_colours = colours_on_tag(tag_info)
    if len(tag_colours) > max(len(spoolman_colours), SINGLE_COLOUR_COUNT):
        return _multi_colour_args(tag_colours, _tag_alpha(tag_info))
    if len(spoolman_colours) > SINGLE_COLOUR_COUNT:
        return _multi_colour_args(spoolman_colours, OPAQUE_ALPHA_VALUE)
    return {}


# The tag only speaks for the spool when it was resolved to that very Spoolman record; a lane
# whose tag names another spool says nothing about this one.
def tag_describing_spool(tag_info, spool_id):
    wanted_spool_id = coerce_spool_id(spool_id)
    if wanted_spool_id is None or is_untagged_filament(tag_info):
        return None
    if coerce_spool_id(tag_info.get("SPOOL_ID")) != wanted_spool_id:
        return None
    return tag_info
