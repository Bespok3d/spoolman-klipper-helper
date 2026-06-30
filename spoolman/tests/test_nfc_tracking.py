# ruff: noqa: PLR2004  Tests assert against literal spool ids and byte values.
"""Regression tests for the UID-tracking pure helpers (the testable core of B1).

The async resolver chain + the actual Spoolman GET/PATCH calls live in spoolman.py and follow
the existing un-unit-tested SpoolmanRequest pattern (device-validated); these tests pin the pure
decisions: UID normalization + the random-UID guard, the nfc_id read, the client-side match, the
GET-merge-PATCH extra builder (preserves other fields), and the strategy-chain parse/order.
"""
import json

from nfc_tracking import (
    DEFAULT_STRATEGY,
    NFC_ID_FIELD,
    find_spool_by_id,
    is_random_uid,
    match_spool_by_nfc_id,
    merged_extra_with_nfc_id,
    nfc_id_field_definition,
    normalize_uid,
    parse_strategy_chain,
    spool_has_nfc_id,
    spool_nfc_ids,
    trackable_uid,
)

GENUINE_UID = [0x04, 0xA1, 0xB2, 0xC3]
GENUINE_HEX = "04a1b2c3"
RANDOM_UID = [0x08, 0x11, 0x22, 0x33]


def _spool(spool_id, extra):
    return {"id": spool_id, "extra": extra}


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


def test_spool_nfc_ids_reads_array_and_tolerates_legacy_forms():
    two_tags = _spool(1, {NFC_ID_FIELD: json.dumps([GENUINE_HEX, "deadbeef"])})
    assert spool_nfc_ids(two_tags) == [GENUINE_HEX, "deadbeef"]
    assert spool_nfc_ids(_spool(1, {NFC_ID_FIELD: GENUINE_HEX})) == [GENUINE_HEX]
    assert spool_nfc_ids(_spool(1, {NFC_ID_FIELD: "aa, bb cc"})) == ["aa", "bb", "cc"]
    assert spool_nfc_ids(_spool(1, {NFC_ID_FIELD: json.dumps([])})) == []
    assert spool_nfc_ids(_spool(1, {})) == []
    assert spool_nfc_ids({}) == []


def test_spool_has_nfc_id():
    spool = _spool(1, {NFC_ID_FIELD: json.dumps([GENUINE_HEX, "deadbeef"])})
    assert spool_has_nfc_id(spool, GENUINE_HEX) is True
    assert spool_has_nfc_id(spool, "nope") is False
    assert spool_has_nfc_id(spool, None) is False


def test_match_spool_by_nfc_id_array_membership():
    spools = [
        _spool(10, {NFC_ID_FIELD: json.dumps(["deadbeef"])}),
        _spool(20, {NFC_ID_FIELD: json.dumps(["cafebabe", GENUINE_HEX])}),  # 2nd tag of a spool
    ]
    assert match_spool_by_nfc_id(spools, GENUINE_HEX)["id"] == 20
    assert match_spool_by_nfc_id(spools, "no-such-uid") is None
    assert match_spool_by_nfc_id(spools, None) is None
    assert match_spool_by_nfc_id([], GENUINE_HEX) is None


def test_match_takes_first_on_collision():
    spools = [
        _spool(10, {NFC_ID_FIELD: json.dumps([GENUINE_HEX])}),
        _spool(20, {NFC_ID_FIELD: json.dumps([GENUINE_HEX])}),
    ]
    assert match_spool_by_nfc_id(spools, GENUINE_HEX)["id"] == 10  # first wins, "and fuck it"


def test_find_spool_by_id():
    spools = [_spool(10, {}), _spool(20, {})]
    assert find_spool_by_id(spools, 20)["id"] == 20
    assert find_spool_by_id(spools, 99) is None
    assert find_spool_by_id([], 1) is None


def test_merged_extra_preserves_other_fields():
    spool = _spool(5, {"price": json.dumps(25), "lot_nr": json.dumps("L42")})
    merged = merged_extra_with_nfc_id(spool, GENUINE_HEX)
    # other extras carried through unchanged (Spoolman replaces the whole extra object)
    assert merged["price"] == json.dumps(25)
    assert merged["lot_nr"] == json.dumps("L42")
    # nfc_id is a JSON array
    assert merged[NFC_ID_FIELD] == json.dumps([GENUINE_HEX])
    # the source spool is not mutated
    assert NFC_ID_FIELD not in spool["extra"]


def test_merged_extra_appends_second_tag():
    spool = _spool(5, {NFC_ID_FIELD: json.dumps(["sideA"])})
    merged = merged_extra_with_nfc_id(spool, GENUINE_HEX)
    assert json.loads(merged[NFC_ID_FIELD]) == ["sideA", GENUINE_HEX]


def test_merged_extra_does_not_append_duplicate():
    spool = _spool(5, {NFC_ID_FIELD: json.dumps([GENUINE_HEX])})
    merged = merged_extra_with_nfc_id(spool, GENUINE_HEX)
    assert json.loads(merged[NFC_ID_FIELD]) == [GENUINE_HEX]  # no duplicate appended


def test_merged_extra_handles_missing_extra():
    merged = merged_extra_with_nfc_id({"id": 5}, GENUINE_HEX)
    assert merged == {NFC_ID_FIELD: json.dumps([GENUINE_HEX])}


def test_field_definition_shape():
    definition = nfc_id_field_definition()
    assert definition["name"]
    assert definition["field_type"] == "text"
    assert definition["default_value"] == json.dumps([])


def test_parse_strategy_chain_default_and_custom():
    assert parse_strategy_chain("") == tuple(DEFAULT_STRATEGY)
    assert parse_strategy_chain(None) == tuple(DEFAULT_STRATEGY)
    assert parse_strategy_chain("uid,spool_id") == ("uid", "spool_id")
    assert parse_strategy_chain("SKU, UID") == ("sku", "uid")  # case + whitespace tolerant


def test_parse_strategy_chain_dedupes_and_drops_unknown():
    assert parse_strategy_chain("uid,uid,sku") == ("uid", "sku")
    assert parse_strategy_chain("bogus,uid,garbage") == ("uid",)
    assert parse_strategy_chain("only-garbage") == tuple(DEFAULT_STRATEGY)
