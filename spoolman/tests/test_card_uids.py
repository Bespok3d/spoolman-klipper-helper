# ruff: noqa: PLR2004  Tests assert against literal spool ids and byte values.
"""Regression tests for the UID-tracking pure helpers (the testable core of B1).

The async resolver chain + the actual Spoolman GET/PATCH calls live in spoolman.py and follow
the existing un-unit-tested SpoolmanRequest pattern (device-validated); these tests pin the pure
decisions: UID normalization + the random-UID guard, the card_uids read, the client-side match,
the GET-merge-PATCH extra builder (preserves other fields), and the strategy-chain parse/order.
"""
import json

from card_uids import (
    CARD_UIDS_FIELD,
    DEFAULT_STRATEGY,
    card_uids_field_definition,
    find_spool_by_id,
    is_random_uid,
    match_spool_by_card_uid,
    merged_extra_with_card_uids,
    normalize_uid,
    parse_strategy_chain,
    spool_card_uids,
    spool_has_card_uid,
    trackable_uid,
)

GENUINE_UID = [0x04, 0xA1, 0xB2, 0xC3]
GENUINE_HEX = "04a1b2c3"
RANDOM_UID = [0x08, 0x11, 0x22, 0x33]


def _spool(spool_id, extra):
    return {"id": spool_id, "extra": extra}


# The on-the-wire form of a card_uids value: Spoolman validates that json.loads of a text-field
# value is a STRING (it 400s the PATCH otherwise), and our payload inside that string is a JSON
# array -- so a correctly stored value is JSON two layers deep.
def _wire(uids):
    return json.dumps(json.dumps(uids))


def test_normalize_uid_to_lowercase_hex():
    assert normalize_uid(GENUINE_UID) == GENUINE_HEX
    assert normalize_uid([0x04, 0x05]) == "0405"


def test_normalize_uid_empty_or_sentinel():
    assert normalize_uid(0) is None          # firmware empty sentinel
    assert normalize_uid([]) is None
    assert normalize_uid([0, 0, 0, 0]) is None  # all-zero = no UID
    assert normalize_uid("nope") is None


def test_random_uid_is_flagged_and_not_trackable():
    assert is_random_uid(RANDOM_UID) is True
    assert is_random_uid(GENUINE_UID) is False
    assert trackable_uid(RANDOM_UID) is None
    assert trackable_uid(GENUINE_UID) == GENUINE_HEX


def test_spool_card_uids_reads_wire_form():
    # what Spoolman actually returns after a correct write (or the field's own default)
    assert spool_card_uids(_spool(1, {CARD_UIDS_FIELD: _wire([GENUINE_HEX, "deadbeef"])})) == [
        GENUINE_HEX, "deadbeef",
    ]
    assert spool_card_uids(_spool(1, {CARD_UIDS_FIELD: _wire([])})) == []


def test_spool_card_uids_tolerates_legacy_forms():
    two_tags = _spool(1, {CARD_UIDS_FIELD: json.dumps([GENUINE_HEX, "deadbeef"])})
    assert spool_card_uids(two_tags) == [GENUINE_HEX, "deadbeef"]
    assert spool_card_uids(_spool(1, {CARD_UIDS_FIELD: GENUINE_HEX})) == [GENUINE_HEX]
    assert spool_card_uids(_spool(1, {CARD_UIDS_FIELD: "aa, bb cc"})) == ["aa", "bb", "cc"]
    assert spool_card_uids(_spool(1, {CARD_UIDS_FIELD: json.dumps([])})) == []
    assert spool_card_uids(_spool(1, {})) == []
    assert spool_card_uids({}) == []


def test_spool_has_card_uid():
    spool = _spool(1, {CARD_UIDS_FIELD: json.dumps([GENUINE_HEX, "deadbeef"])})
    assert spool_has_card_uid(spool, GENUINE_HEX) is True
    assert spool_has_card_uid(spool, "nope") is False
    assert spool_has_card_uid(spool, None) is False


def test_match_spool_by_card_uid_array_membership():
    spools = [
        _spool(10, {CARD_UIDS_FIELD: json.dumps(["deadbeef"])}),
        _spool(20, {CARD_UIDS_FIELD: json.dumps(["cafebabe", GENUINE_HEX])}),  # 2nd tag of a spool
    ]
    assert match_spool_by_card_uid(spools, GENUINE_HEX)["id"] == 20
    assert match_spool_by_card_uid(spools, "no-such-uid") is None
    assert match_spool_by_card_uid(spools, None) is None
    assert match_spool_by_card_uid([], GENUINE_HEX) is None


def test_match_takes_first_on_collision():
    spools = [
        _spool(10, {CARD_UIDS_FIELD: json.dumps([GENUINE_HEX])}),
        _spool(20, {CARD_UIDS_FIELD: json.dumps([GENUINE_HEX])}),
    ]
    assert match_spool_by_card_uid(spools, GENUINE_HEX)["id"] == 10  # first wins


def test_find_spool_by_id():
    spools = [_spool(10, {}), _spool(20, {})]
    assert find_spool_by_id(spools, 20)["id"] == 20
    assert find_spool_by_id(spools, 99) is None
    assert find_spool_by_id([], 1) is None


def test_merged_extra_preserves_other_fields():
    spool = _spool(5, {"price": json.dumps(25), "lot_nr": json.dumps("L42")})
    merged = merged_extra_with_card_uids(spool, GENUINE_HEX)
    # other extras carried through unchanged (Spoolman replaces the whole extra object)
    assert merged["price"] == json.dumps(25)
    assert merged["lot_nr"] == json.dumps("L42")
    # card_uids goes out in the wire form Spoolman's text-field validation accepts
    assert merged[CARD_UIDS_FIELD] == _wire([GENUINE_HEX])
    # the source spool is not mutated
    assert CARD_UIDS_FIELD not in spool["extra"]


def test_merged_extra_value_passes_spoolman_text_validation():
    # Spoolman's validate_extra_field_value: json.loads(value) must be a str, else 400.
    merged = merged_extra_with_card_uids({"id": 5}, GENUINE_HEX)
    assert isinstance(json.loads(merged[CARD_UIDS_FIELD]), str)


def test_merged_extra_appends_second_tag():
    spool = _spool(5, {CARD_UIDS_FIELD: _wire(["sideA"])})
    merged = merged_extra_with_card_uids(spool, GENUINE_HEX)
    assert merged[CARD_UIDS_FIELD] == _wire(["sideA", GENUINE_HEX])


def test_merged_extra_migrates_legacy_single_encoded_value():
    spool = _spool(5, {CARD_UIDS_FIELD: json.dumps(["sideA"])})
    merged = merged_extra_with_card_uids(spool, GENUINE_HEX)
    assert merged[CARD_UIDS_FIELD] == _wire(["sideA", GENUINE_HEX])


def test_merged_extra_does_not_append_duplicate():
    spool = _spool(5, {CARD_UIDS_FIELD: _wire([GENUINE_HEX])})
    merged = merged_extra_with_card_uids(spool, GENUINE_HEX)
    assert merged[CARD_UIDS_FIELD] == _wire([GENUINE_HEX])  # no duplicate appended


def test_merged_extra_handles_missing_extra():
    merged = merged_extra_with_card_uids({"id": 5}, GENUINE_HEX)
    assert merged == {CARD_UIDS_FIELD: _wire([GENUINE_HEX])}


def test_field_definition_shape():
    definition = card_uids_field_definition()
    assert definition["name"]
    assert definition["field_type"] == "text"
    # A "text" field's default_value must decode to a str (Spoolman: "Value is not a string."
    # otherwise), so the list default is wrapped in a second json.dumps.
    assert isinstance(json.loads(definition["default_value"]), str)
    assert json.loads(json.loads(definition["default_value"])) == []


def test_parse_strategy_chain_default_and_custom():
    assert parse_strategy_chain("") == tuple(DEFAULT_STRATEGY)
    assert parse_strategy_chain(None) == tuple(DEFAULT_STRATEGY)
    assert parse_strategy_chain("uid,spool_id") == ("uid", "spool_id")
    assert parse_strategy_chain("SKU, UID") == ("sku", "uid")  # case + whitespace tolerant


def test_parse_strategy_chain_dedupes_and_drops_unknown():
    assert parse_strategy_chain("uid,uid,sku") == ("uid", "sku")
    assert parse_strategy_chain("bogus,uid,garbage") == ("uid",)
    assert parse_strategy_chain("only-garbage") == tuple(DEFAULT_STRATEGY)
