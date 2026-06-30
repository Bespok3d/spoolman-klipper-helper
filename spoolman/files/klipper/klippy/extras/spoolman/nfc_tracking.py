"""Pure helpers for identifying a Spoolman spool by its tag's hardware UID.

A tag's UID (``CARD_UID`` in the filament struct, captured for every NTAG and surfaced for
keyless proprietary tags by the rfid-ntag substrate) can bind a spool in Spoolman through a
text extra field named ``nfc_id`` (SpoolSense-compatible). This module is the stdlib-only,
unit-tested core: UID normalization + the random-UID guard, the field definition, reading a
spool's ``nfc_id``, the client-side match, the GET-merge-PATCH extra builder, and parsing the
configurable resolution-strategy chain. The async Spoolman calls live in ``spoolman.py``.
"""
import json

NFC_ID_FIELD = "nfc_id"
RANDOM_UID_PREFIX = 0x08  # NXP AN10927: a re-randomized UID starts with 0x08, not a stable key
KNOWN_STRATEGIES = ("spool_id", "uid", "decoded_id", "sku", "manual")
DEFAULT_STRATEGY = ("spool_id", "uid", "sku")


def normalize_uid(card_uid):
    """Render a CARD_UID (a list of byte ints) as a lowercase hex string, or None if absent.

    The firmware's empty sentinel is the integer 0; an unread channel has no UID.
    """
    if not isinstance(card_uid, (list, tuple)) or not card_uid:
        return None
    try:
        byte_values = [int(byte) & 0xFF for byte in card_uid]
    except (TypeError, ValueError):
        return None
    if not any(byte_values):
        return None
    return "".join(f"{byte:02x}" for byte in byte_values)


def is_random_uid(card_uid):
    """A re-randomized (DESFire) UID is not a stable tracking key."""
    if not isinstance(card_uid, (list, tuple)) or not card_uid:
        return False
    return int(card_uid[0]) == RANDOM_UID_PREFIX


def trackable_uid(card_uid):
    """The hex UID to track, or None when the tag has no UID or a non-stable one."""
    if is_random_uid(card_uid):
        return None
    return normalize_uid(card_uid)


def nfc_id_field_definition():
    """Body for POST /api/v1/field/spool/nfc_id (idempotent; Spoolman 409s if it exists)."""
    return {"name": "NFC tag ids", "field_type": "text", "default_value": json.dumps([])}


def spool_nfc_ids(spool):
    """The UIDs bound to a spool. A spool has 2 sides / 2 tags, so nfc_id holds a LIST.

    Tolerant of how the value was written (by us or a mobile app): a JSON array, a single
    string, or a comma/space-separated string all read back as a clean list of hex UIDs.
    """
    if not isinstance(spool, dict):
        return []
    raw = (spool.get("extra") or {}).get(NFC_ID_FIELD)
    return _decode_uid_list(raw)


def _decode_uid_list(raw):
    if raw is None:
        return []
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw.replace(",", " ")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [token for token in str(value).split() if token.strip()]


def spool_has_nfc_id(spool, uid_hex):
    """Whether this UID is already bound to the spool (so we never append a duplicate)."""
    return bool(uid_hex) and uid_hex in spool_nfc_ids(spool)


def match_spool_by_nfc_id(spools, uid_hex):
    """The first spool whose nfc_id list contains the UID (on a collision: first wins)."""
    if not uid_hex or not isinstance(spools, list):
        return None
    for spool in spools:
        if spool_has_nfc_id(spool, uid_hex):
            return spool
    return None


def find_spool_by_id(spools, spool_id):
    """The spool with this id in a fetched list (to read its current extra before a PATCH)."""
    for spool in spools or []:
        if spool.get("id") == spool_id:
            return spool
    return None


def merged_extra_with_nfc_id(spool, uid_hex):
    """GET-merge-PATCH body: append the UID to the spool's nfc_id LIST (issue #638).

    Spoolman replaces the whole ``extra`` object on PATCH, so every existing extra field is
    carried through unchanged, and the existing UID list (ours or a mobile app's) is kept; the
    new UID is appended only if absent (no duplicates). Values are JSON-encoded.
    """
    existing = (spool.get("extra") if isinstance(spool, dict) else None) or {}
    merged = dict(existing)
    uids = spool_nfc_ids(spool)
    if uid_hex and uid_hex not in uids:
        uids = uids + [uid_hex]
    merged[NFC_ID_FIELD] = json.dumps(uids)
    return merged


def parse_strategy_chain(text, default=DEFAULT_STRATEGY):
    """Parse a comma-separated strategy list into a validated ordered tuple, deduped."""
    if not text:
        return tuple(default)
    seen = []
    for token in str(text).split(","):
        name = token.strip().lower()
        if name in KNOWN_STRATEGIES and name not in seen:
            seen.append(name)
    return tuple(seen) if seen else tuple(default)
