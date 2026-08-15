"""Which of the user's own Spoolman spools come closest to a tag that matched nothing.

The user is standing at the printer with a spool the helper could not place, so the shortlist has
to be short and best first: a long unordered list is the same as no list. Material is the one field
a candidate must share, because a spool of another material is never the one on the tag; vendor,
colour and filament name then decide the order. An exact colour outranks a colour of the same
family, so "the same red" comes before "a red".

Archived spools are dropped here even though `/api/v1/spool` already leaves them out
(`allow_archived` defaults to false): a spool the user retired is never worth offering, and the
list must stay right whatever the server was asked.
"""
from .filament_info import filament_info_from_spoolman, nearest_colour_name, rgb_hex

MAX_CANDIDATES = 5
SAME_MATERIAL_SCORE = 1
SAME_VENDOR_SCORE = 2
SAME_FILAMENT_NAME_SCORE = 1
EXACT_COLOUR_SCORE = 4
SAME_COLOUR_FAMILY_SCORE = 2
NO_SCORE = 0


def candidate_spools(tag_info, spools):
    scored = [(_match_score(tag_info, filament_info_from_spoolman(spool)), spool)
              for spool in spools or [] if not spool.get("archived")]
    offered = [(-score, _spool_number(spool), spool)
               for score, spool in scored if score > NO_SCORE]
    return [spool for _rank, _number, spool in
            sorted(offered, key=_rank_then_spool_number)][:MAX_CANDIDATES]


def _rank_then_spool_number(offered_spool):
    rank, spool_number, _spool = offered_spool
    return (rank, spool_number)


def _spool_number(spool):
    return spool.get("id") or 0


def _match_score(tag_info, spool_info):
    if not _same_value(tag_info, spool_info, "MAIN_TYPE"):
        return NO_SCORE
    return (SAME_MATERIAL_SCORE
            + _colour_score(tag_info, spool_info)
            + _field_score(tag_info, spool_info, "VENDOR", SAME_VENDOR_SCORE)
            + _field_score(tag_info, spool_info, "SUB_TYPE", SAME_FILAMENT_NAME_SCORE))


def _field_score(tag_info, spool_info, field, score):
    return score if _same_value(tag_info, spool_info, field) else NO_SCORE


def _colour_score(tag_info, spool_info):
    tag_colour = rgb_hex(tag_info.get("ARGB_COLOR"))
    spool_colour = rgb_hex(spool_info.get("ARGB_COLOR"))
    if not (tag_colour and spool_colour):
        return NO_SCORE
    if tag_colour == spool_colour:
        return EXACT_COLOUR_SCORE
    if nearest_colour_name(tag_colour) == nearest_colour_name(spool_colour):
        return SAME_COLOUR_FAMILY_SCORE
    return NO_SCORE


def _same_value(tag_info, spool_info, field):
    on_tag = _comparable(tag_info.get(field))
    return bool(on_tag) and on_tag == _comparable(spool_info.get(field))


def _comparable(value):
    return str(value or "").strip().upper()
