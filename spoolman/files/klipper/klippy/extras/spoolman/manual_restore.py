"""Manual-pick continuity across restarts: remember picks, replay them at klippy-ready.

The pure file store is manual_spools.py; this module owns the policy. Only untagged lanes are
remembered (a tag is its own persistence), and at replay a tag that appeared while powered off
wins over the stale pick. The store path is injected: where the file lives on a given machine
is deployment knowledge, not this module's.
"""
from .manual_spools import load_manual_spools, store_manual_spool


class ManualSpoolRestore:
    def __init__(self, helper, manual_spools_path):
        self.helper = helper
        self.logs = helper.logs
        self.holders = helper.holders
        self.manual_spools_path = manual_spools_path

    # Manual picks have no tag to re-read them from after a restart, so they persist to a JSON
    # file (the rfid_data.json pattern). Only untagged lanes: a tag is its own persistence.
    def remember(self, lane, spool_id):
        if self.holders.lane_is_tagged(lane):
            return
        store_manual_spool(self.manual_spools_path, lane, spool_id)

    def restore_all(self):
        for lane, spool_id in load_manual_spools(self.manual_spools_path).items():
            self._restore_pick(lane, spool_id)

    # A tag that appeared on the lane while powered off wins (and the stale entry is dropped).
    # Filament presence is deliberately NOT checked here: at klippy-ready the firmware's
    # filament_exist still holds its all-False defaults, so trusting it deletes every pick; a
    # genuinely pulled filament is released by the removal watcher once the firmware reports it.
    # Replaying through the normal pick cascade re-labels the tool macros, screen, AFC lane, and
    # location.
    def _restore_pick(self, lane, spool_id):
        if self.holders.lane_is_tagged(lane):
            store_manual_spool(self.manual_spools_path, lane, None)
            self.logs.verbose(f"Manual spool {spool_id} for lane E{lane} superseded by a tag")
            return
        self.logs.log(f"Restoring manual spool {spool_id} for lane E{lane}")
        self.helper.tracking.pick_for_lane(lane, spool_id)
