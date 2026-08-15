"""Turning a tag Spoolman has nothing for into the records Spoolman needs before it takes a spool.

Spoolman refuses a spool that belongs to no filament, and refuses a filament that carries no
diameter and no density. A tag carries neither number. A number nobody measured is not one this
helper is willing to write into someone's inventory, so both are copied from their own filaments of
the same material: those are numbers they already stand behind. A material they have never bought
therefore registers nothing, and the console says which filament is missing.

Everything else on the new records comes off the card and nowhere else: vendor, material, filament
name, colour and article number. No weight, no temperature, no date, no invented SKU.

This module is pure. It reads what Spoolman already holds, builds the payloads, and touches nothing.
"""
from .filament_info import filament_info_from_spoolman, rgb_hex


def _comparable(value):
    return str(value or "").strip().upper()


def _same_text(on_tag, in_spoolman):
    return _comparable(on_tag) == _comparable(in_spoolman)


# The tag may already be a filament they own: their own article number is the strongest word on
# that, and a vendor, material, name and colour that all agree is the next strongest. Either way
# the new spool hangs off the record they already curate instead of a duplicate of it.
def matching_filament(tag_info, filaments):
    by_article_number = [filament for filament in filaments
                         if _comparable(tag_info.get("SKU"))
                         and _same_text(tag_info.get("SKU"), filament.get("article_number"))]
    if by_article_number:
        return by_article_number[0]
    described = [filament for filament in filaments if _describes_same_filament(tag_info, filament)]
    return described[0] if described else None


def _describes_same_filament(tag_info, filament):
    described = filament_info_from_spoolman({"filament": filament})
    colour = rgb_hex(tag_info.get("ARGB_COLOR"))
    return bool(colour) and colour == rgb_hex(described.get("ARGB_COLOR")) and all((
        _comparable(tag_info.get("MAIN_TYPE")),
        _same_text(tag_info.get("MAIN_TYPE"), described.get("MAIN_TYPE")),
        _same_text(tag_info.get("SUB_TYPE"), described.get("SUB_TYPE")),
        _same_text(tag_info.get("VENDOR"), described.get("VENDOR")),
    ))


# The one place a number the card does not carry may come from: filaments of this same material
# that are already in their Spoolman. Where those disagree, the value the most of them carry wins.
# Nothing of this material means nothing to copy, and the caller creates nothing.
def dimensions_of_material(tag_info, filaments):
    material = _comparable(tag_info.get("MAIN_TYPE"))
    measured = [filament for filament in filaments
                if material and _comparable(filament.get("material")) == material
                and _is_positive(filament.get("diameter"))
                and _is_positive(filament.get("density"))]
    if not measured:
        return None
    return {"diameter": _commonest_value(measured, "diameter"),
            "density": _commonest_value(measured, "density")}


def _is_positive(number):
    return isinstance(number, (int, float)) and not isinstance(number, bool) and number > 0


def _commonest_value(filaments, field):
    values = [filament.get(field) for filament in filaments]
    return max(values, key=values.count)


# Spoolman nests each filament's vendor inside the filament, so their vendor list is already in
# hand and a vendor is only created when the card names one they do not have yet.
def vendor_id_named(vendor_name, filaments):
    if not _comparable(vendor_name):
        return None
    vendors = [filament.get("vendor") or {} for filament in filaments]
    matching_ids = [vendor.get("id") for vendor in vendors
                    if _same_text(vendor_name, vendor.get("name"))]
    return matching_ids[0] if matching_ids else None


def vendor_payload(tag_info):
    return {"name": str(tag_info.get("VENDOR") or "").strip()}


# The fields the card itself holds, and the only ones written to a filament record. A field the
# card left blank is left out rather than written as an empty string over something they wrote.
def card_details_payload(tag_info):
    return _without_blanks({
        "material": str(tag_info.get("MAIN_TYPE") or "").strip(),
        "name": str(tag_info.get("SUB_TYPE") or "").strip(),
        "color_hex": rgb_hex(tag_info.get("ARGB_COLOR")),
        "article_number": str(tag_info.get("SKU") or "").strip(),
    })


def filament_payload(tag_info, vendor_id, dimensions):
    return _without_blanks({
        **card_details_payload(tag_info),
        "diameter": dimensions["diameter"],
        "density": dimensions["density"],
        "vendor_id": vendor_id,
    })


def spool_payload(filament_id):
    return {"filament_id": filament_id}


def _without_blanks(payload):
    return {field: value for field, value in payload.items() if value not in ("", None)}
