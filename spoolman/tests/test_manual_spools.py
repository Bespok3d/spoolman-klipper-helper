"""Manual-pick persistence: the rfid_data.json pattern for spools with no tag."""
import json

from manual_spools import load_manual_spools, store_manual_spool


def test_round_trip(tmp_path):
    path = str(tmp_path / "manual_spools.json")
    store_manual_spool(path, 0, 94)
    store_manual_spool(path, 2, 104)
    assert load_manual_spools(path) == {0: 94, 2: 104}


def test_clearing_a_pick_removes_its_entry(tmp_path):
    path = str(tmp_path / "manual_spools.json")
    store_manual_spool(path, 0, 94)
    store_manual_spool(path, 0, None)
    assert load_manual_spools(path) == {}


def test_missing_or_corrupt_file_reads_as_no_picks(tmp_path):
    assert load_manual_spools(str(tmp_path / "nope.json")) == {}
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert load_manual_spools(str(corrupt)) == {}
    not_a_dict = tmp_path / "list.json"
    not_a_dict.write_text("[1, 2]")
    assert load_manual_spools(str(not_a_dict)) == {}


def test_garbage_entries_are_dropped_not_fatal(tmp_path):
    mixed = tmp_path / "mixed.json"
    mixed.write_text(json.dumps({"0": 94, "bogus": 5, "1": "not-a-spool"}))
    assert load_manual_spools(str(mixed)) == {0: 94}


def test_unchanged_value_does_not_rewrite_the_file(tmp_path):
    path = tmp_path / "manual_spools.json"
    store_manual_spool(str(path), 0, 94)
    first_mtime = path.stat().st_mtime_ns
    store_manual_spool(str(path), 0, 94)
    assert path.stat().st_mtime_ns == first_mtime
