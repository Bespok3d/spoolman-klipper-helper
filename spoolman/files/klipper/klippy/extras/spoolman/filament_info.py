"""Human labels for a spool the helper is tracking.

A spool that is physically loaded but carries no vendor/material (untagged, and not assigned a
Spoolman spool) is labelled UNKNOWN here rather than the firmware's raw "NONE": it is present but
unidentified, not absent. This naming lives ONLY in our logs/labels; the firmware's
print_task_config keeps "NONE" as its own sentinel for "not edited", which it relies on.
"""
from .card_uids import trackable_uid

UNKNOWN_DESCRIPTOR = "UNKNOWN"
UNTAGGED_VENDORS = ("NONE", "Generic")
KNOWN_KEYS = {"VENDOR", "MAIN_TYPE", "SUB_TYPE", "ARGB_COLOR", "SPOOL_ID", "SKU", "CARD_UID"}

RGB_MASK = 0xFFFFFF
RGB_HEX_LENGTH = 6
HEX_BASE = 16
HEX_DIGITS = "0123456789ABCDEF"

# Anchor colours a person recognises; an exact #RRGGBB is matched to its nearest one for a hint.
NAMED_COLOURS = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "grey": (128, 128, 128),
    "red": (220, 40, 40),
    "orange": (240, 140, 30),
    "yellow": (240, 220, 40),
    "green": (40, 160, 60),
    "teal": (30, 130, 130),
    "blue": (40, 80, 220),
    "purple": (130, 60, 190),
    "pink": (230, 110, 180),
    "brown": (120, 70, 40),
}


def is_untagged_filament(filament_info):
    if not filament_info:
        return True
    return filament_info.get("VENDOR") in UNTAGGED_VENDORS


# A Spoolman spool rendered into the same shape a tag produces, so a manually picked spool logs
# exactly like a tagged one ("Vendor Material Name (colour: ..., Spoolman id: N, sku: ...)").
def filament_info_from_spoolman(spool):
    filament = (spool or {}).get("filament") or {}
    return {
        "VENDOR": (filament.get("vendor") or {}).get("name") or "",
        "MAIN_TYPE": filament.get("material") or "",
        "SUB_TYPE": filament.get("name") or "",
        "ARGB_COLOR": filament.get("color_hex") or "",
        "SPOOL_ID": (spool or {}).get("id"),
        "SKU": filament.get("article_number") or "",
    }


def filament_descriptor(filament_info):
    if is_untagged_filament(filament_info):
        return UNKNOWN_DESCRIPTOR
    vendor = filament_info.get("VENDOR")
    main = filament_info.get("MAIN_TYPE")
    sub = filament_info.get("SUB_TYPE")
    return f"{vendor} {main} {sub}"


# The firmware/detector hand back a colour either as an ARGB integer (0xAARRGGBB, e.g. 4294967295)
# or an RRGGBBAA hex string. Both render to "#RRGGBB (name)" a person can actually read.
def friendly_colour(argb_color):
    rgb = rgb_hex(argb_color)
    if not rgb:
        return "unknown"
    return f"#{rgb} ({nearest_colour_name(rgb)})"


# Public because comparing two colours (a tag against a Spoolman spool) means comparing them in
# the one form both arrive in, and that normalisation lives here with the colours it knows.
def rgb_hex(argb_color):
    if isinstance(argb_color, int):
        return f"{argb_color & RGB_MASK:0{RGB_HEX_LENGTH}X}"
    if isinstance(argb_color, str):
        cleaned = argb_color.strip().lstrip("#").upper()
        prefix = cleaned[:RGB_HEX_LENGTH]
        if len(cleaned) >= RGB_HEX_LENGTH and all(digit in HEX_DIGITS for digit in prefix):
            return prefix
    return ""


def nearest_colour_name(colour_hex):
    red = int(colour_hex[0:2], HEX_BASE)
    green = int(colour_hex[2:4], HEX_BASE)
    blue = int(colour_hex[4:6], HEX_BASE)
    return min(
        NAMED_COLOURS, key=lambda name: _colour_distance(NAMED_COLOURS[name], red, green, blue)
    )


def _colour_distance(anchor, red, green, blue):
    return (anchor[0] - red) ** 2 + (anchor[1] - green) ** 2 + (anchor[2] - blue) ** 2


def filament_info_to_string(filament_info, level="info"):
    if not filament_info:
        return "- Missing Filament Info! -"
    base = _summary(filament_info)
    if level != "debug":
        return base
    extras = [f"{key}->{value}" for key, value in filament_info.items() if key not in KNOWN_KEYS]
    if not extras:
        return base
    return base + "\nadditional filament info: " + ", ".join(extras)


# An unidentified spool has no meaningful colour / spool-id / sku (they are firmware defaults), so
# it reads as just UNKNOWN instead of carrying noise like a white default colour.
def _summary(filament_info):
    if is_untagged_filament(filament_info):
        return UNKNOWN_DESCRIPTOR
    descriptor = filament_descriptor(filament_info)
    colour = friendly_colour(filament_info.get("ARGB_COLOR"))
    spool_id = filament_info.get("SPOOL_ID")
    sku = filament_info.get("SKU")
    uid_clause = _card_uid_clause(filament_info)
    return f"{descriptor} (colour: {colour}, Spoolman id: {spool_id}, sku: {sku}{uid_clause})"


# The tag's own hardware UID, so a person reading the lane line can bind it to a Spoolman spool
# (SH_BIND_CARD_UID) without digging it out of the firmware. Says nothing for a tag with no UID,
# or a re-randomized one, since neither can be bound.
def _card_uid_clause(filament_info):
    card_uid = trackable_uid(filament_info.get("CARD_UID"))
    if not card_uid:
        return ""
    return f", card uid: {card_uid}"
