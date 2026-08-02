"""Pure helpers for identifying a Spoolman spool by its tag's hardware UID.

A tag's UID (``CARD_UID`` in the filament struct, captured for every NTAG and surfaced for
keyless proprietary tags by the rfid-ntag substrate) can bind a spool in Spoolman through a
text extra field named ``card_uids`` (a JSON array of UIDs). The name is shared with the mobile
companion app that writes the same field, and every writer of it spells a UID its own way, so we
read all of them: any letter case, ``:`` or ``-`` between the bytes, the bytes spaced apart, a
leading ``0x``, a JSON array or one flat comma-separated value. Each one is reduced to the same
bare lowercase hex before it is compared or stored, so one physical tag is one UID here whatever
wrote it. The reverse is not guaranteed: our value is a JSON array inside the JSON string Spoolman
requires of a text field, which a reader that only splits on commas does not decode, and the
``comma_separated`` write form exists for that reader.
This module is the stdlib-only, unit-tested core: UID normalization + the random-UID guard, the
field definition, reading a spool's ``card_uids``, the client-side match, the GET-merge-PATCH
extra builder, and parsing the configurable resolution-strategy chain. The async Spoolman calls
live in ``spoolman.py``.
"""
import json

CARD_UIDS_FIELD = "card_uids"
RANDOM_UID_PREFIX = 0x08  # NXP AN10927: a re-randomized UID starts with 0x08, not a stable key
KNOWN_STRATEGIES = ("spool_id", "uid", "decoded_id", "sku", "manual")
DEFAULT_STRATEGY = ("spool_id", "uid", "sku")
# Spoolman validates a text extra field's VALUE the same way as its default: json.loads(value)
# must yield a string (extra_fields.validate_extra_field_value, enforced on spool PATCH). Our
# payload inside that string is itself a JSON array, so a stored value is JSON two layers deep.
TEXT_FIELD_JSON_LAYERS = 2
# Both write forms are a JSON string, because that is what Spoolman validates a text field to be.
# They differ in what the string holds: ARRAY keeps a UID list a list, which is the right data type
# and the default; COMMA_SEPARATED flattens it so a reader that only splits on commas (another app
# writing this same field) can read a binding we made. Reading accepts both either way.
ARRAY_WRITE_FORM = "array"
COMMA_SEPARATED_WRITE_FORM = "comma_separated"
KNOWN_WRITE_FORMS = (ARRAY_WRITE_FORM, COMMA_SEPARATED_WRITE_FORM)
DEFAULT_WRITE_FORM = ARRAY_WRITE_FORM
# What another writer puts between a UID's bytes, and what it puts in front of the whole UID.
UID_BYTE_SEPARATORS = (":", "-")
HEX_LITERAL_PREFIX = "0x"
# A tag carries a 4-byte or a 7-byte UID, so 8 hex digits is the shortest a whole UID can be, and
# anything narrower is a single byte of one that a writer spaced out rather than a UID of its own.
SHORTEST_UID_HEX_DIGITS = 8


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


def parse_write_form(text, default=DEFAULT_WRITE_FORM):
    """Validate the configured write form, falling back to the default on anything unknown."""
    name = str(text or "").strip().lower()
    return name if name in KNOWN_WRITE_FORMS else default


def encode_card_uids(uids, write_form=DEFAULT_WRITE_FORM):
    """The stored value for a list of UIDs, in the form the user asked us to write."""
    if write_form == COMMA_SEPARATED_WRITE_FORM:
        return json.dumps(",".join(uids))
    return json.dumps(json.dumps(uids))


def card_uids_field_definition():
    """Body for POST /api/v1/field/spool/card_uids (idempotent; Spoolman 409s if it exists).

    A "text" field's default_value must itself decode to a string (Spoolman validates the type),
    so the empty-list default is JSON-encoded twice: once for the list, once for the text field.
    """
    return {"name": "Card UIDs", "field_type": "text", "default_value": json.dumps(json.dumps([]))}


def canonical_uid(written_uid):
    """One UID reduced to the form we compare and store: bare lowercase hex.

    Writers of this field spell the same tag as ``04A1B2C3``, ``04:a1:b2:c3``, ``04-A1-B2-C3`` or
    ``0x04a1b2c3``. All four are the one tag, so the punctuation and the case come off before
    anything compares them.
    """
    text = "".join(str(written_uid).lower().split())
    for separator in UID_BYTE_SEPARATORS:
        text = text.replace(separator, "")
    return text[len(HEX_LITERAL_PREFIX):] if text.startswith(HEX_LITERAL_PREFIX) else text


def spool_card_uids(spool):
    """The UIDs bound to a spool. A spool has 2 sides / 2 tags, so card_uids holds a LIST.

    Tolerant of how the value was written (by us or another app): a JSON array, a single string,
    or one comma-separated value, in any case and with any byte punctuation, all read back as the
    same clean list of canonical hex UIDs.
    """
    if not isinstance(spool, dict):
        return []
    raw = (spool.get("extra") or {}).get(CARD_UIDS_FIELD)
    return _decode_uid_list(raw)


def _decode_uid_list(raw):
    if raw is None:
        return []
    value = _unwrap_json_layers(raw)
    if isinstance(value, list):
        return [uid for uid in [canonical_uid(item) for item in value] if uid]
    return _uids_from_flat_value(str(value))


def _uids_from_flat_value(text):
    """A flat value holds one UID or several; a comma always separates two of them."""
    return [uid for field in text.split(",") for uid in _uids_in_field(field)]


def _uids_in_field(field):
    """Whitespace inside one comma-separated field is ambiguous, so the width decides.

    A writer that spaces out a UID's bytes leaves tokens narrower than a whole UID, and those are
    joined back into the one UID; anything wider is a UID in its own right.
    """
    tokens = [uid for uid in [canonical_uid(token) for token in field.split()] if uid]
    if tokens and all(len(token) < SHORTEST_UID_HEX_DIGITS for token in tokens):
        return ["".join(tokens)]
    return tokens


def _unwrap_json_layers(raw):
    value = raw
    for _ in range(TEXT_FIELD_JSON_LAYERS):
        if not isinstance(value, str):
            return value
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return value


def spool_has_card_uid(spool, uid_hex):
    """Whether this UID is already bound to the spool (so we never append a duplicate)."""
    return uid_is_bound(uid_hex, spool_card_uids(spool))


def uid_is_bound(uid_hex, bound_uids):
    """Membership on the canonical form: one physical tag is one UID however it was spelled."""
    if not uid_hex:
        return False
    return canonical_uid(uid_hex) in [canonical_uid(bound) for bound in bound_uids]


def match_spool_by_card_uid(spools, uid_hex):
    """The first spool whose card_uids list contains the UID (on a collision: first wins)."""
    if not uid_hex or not isinstance(spools, list):
        return None
    for spool in spools:
        if spool_has_card_uid(spool, uid_hex):
            return spool
    return None


def find_spool_by_id(spools, spool_id):
    """The spool with this id in a fetched list (to read its current extra before a PATCH)."""
    for spool in spools or []:
        if spool.get("id") == spool_id:
            return spool
    return None


def merged_extra_with_card_uids(spool, uid_hex, write_form=DEFAULT_WRITE_FORM):
    """GET-merge-PATCH body: append the UID to the spool's card_uids LIST.

    Spoolman replaces the whole ``extra`` object on PATCH, so every existing extra field is
    carried through unchanged, and the existing UID list (ours or another app's) is kept; the new
    UID is appended only if absent (no duplicates). However the value was spelled, it goes back in
    the canonical UID form and in the configured write form.
    """
    existing = (spool.get("extra") if isinstance(spool, dict) else None) or {}
    merged = dict(existing)
    uids = spool_card_uids(spool)
    if uid_hex and not uid_is_bound(uid_hex, uids):
        uids = uids + [canonical_uid(uid_hex)]
    merged[CARD_UIDS_FIELD] = encode_card_uids(uids, write_form)
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
