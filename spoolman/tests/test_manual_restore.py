# ruff: noqa: PLR2004  Tests assert against literal spool ids and tool indexes.
"""Manual-pick policy: remember only untagged lanes, replay at ready, a tag always wins."""
import types

from spoolman.manual_restore import ManualSpoolRestore
from spoolman.manual_spools import load_manual_spools, store_manual_spool


class RecordingLogs:
    def __init__(self):
        self.lines = []

    def _record(self, message):
        self.lines.append(message)

    log = warn = error = verbose = debug = _record


class RecordingTracking:
    def __init__(self):
        self.lane_picks = []

    def pick_for_lane(self, lane, spool_id):
        self.lane_picks.append((lane, spool_id))


class FakeHolders:
    def __init__(self, tagged_lanes=()):
        self.tagged_lanes = set(tagged_lanes)

    def lane_is_tagged(self, channel):
        return channel in self.tagged_lanes


def build_restore(tmp_path, tagged_lanes=()):
    helper = types.SimpleNamespace(
        logs=RecordingLogs(),
        holders=FakeHolders(tagged_lanes),
        tracking=RecordingTracking(),
    )
    restore = ManualSpoolRestore(helper, str(tmp_path / "manual_spools.json"))
    return restore, helper


def test_remember_persists_an_untagged_lanes_pick(tmp_path):
    restore, _helper = build_restore(tmp_path)
    restore.remember(2, 104)
    assert load_manual_spools(restore.manual_spools_path) == {2: 104}


def test_remember_skips_a_tagged_lane(tmp_path):
    restore, _helper = build_restore(tmp_path, tagged_lanes={2})
    restore.remember(2, 104)
    assert load_manual_spools(restore.manual_spools_path) == {}


def test_restore_replays_picks_through_the_normal_cascade(tmp_path):
    restore, helper = build_restore(tmp_path)
    store_manual_spool(restore.manual_spools_path, 2, 104)
    restore.restore_all()
    assert helper.tracking.lane_picks == [(2, 104)]
    assert any("Restoring manual spool 104 for lane E2" in line for line in helper.logs.lines)


def test_a_tag_that_appeared_while_off_supersedes_the_stale_pick(tmp_path):
    restore, helper = build_restore(tmp_path, tagged_lanes={2})
    store_manual_spool(restore.manual_spools_path, 2, 104)
    restore.restore_all()
    assert helper.tracking.lane_picks == []
    assert load_manual_spools(restore.manual_spools_path) == {}  # stale entry dropped
    assert any("superseded by a tag" in line for line in helper.logs.lines)
