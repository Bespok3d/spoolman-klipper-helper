"""Bulk spool detection and print-start sync.

DETECT_SPOOLS re-reads what the printer already knows: the firmware's print-task spool fields
enrich the holders, persisted tag data (rfid_data.json, written by the RFID substrate) restores
identified lanes across a restart, then every channel re-reads its tag. The re-read is
asynchronous: this module snapshots each lane's current pick (tool macro first, AFC lane
otherwise) and waits for the fresh RFID callback. A tagged report replaces the snapshot. A
report with no tag restores that pick so a panel or screen choice is not wiped. A lane with
no pick is left alone: the helper does not write NONE over a screen-set color. The print-start
sync refreshes the tool map (auto mode) or re-resolves every manual macro pick (manual mode).
The data-file path is injected: where the file lives on a given machine is deployment
knowledge, not this module's.
"""
import json

from .active_spool import coerce_spool_id
from .afc import lane_spool_id
from .filament_info import is_untagged_filament
from .u1_tools import MAX_TOOLS_COUNT


class SpoolDetection:
    def __init__(self, helper, rfid_data_path):
        self.helper = helper
        self.logs = helper.logs
        self.macros = helper.macros
        self.spoolman = helper.spoolman
        self.u1_tools = helper.u1_tools
        self.holders = helper.holders
        self.rfid_data_path = rfid_data_path
        self.pending_picks = {}

    def detect_spools(self):
        detected_spools = self.u1_tools.get_spools_config()
        self.logs.debug(f"detect_spools spools: {detected_spools}")
        self.holders.merge_detected_spools(detected_spools)

        for channel_key, info in self._load_rfid_data().items():
            self._restore_tagged_lane(int(channel_key), info)

        self.pending_picks = {}
        for extruder in range(len(detected_spools)):
            self.pending_picks[extruder] = self.pick_on_channel(extruder)
            self.macros.detect_spool(extruder)

    def pick_on_channel(self, channel):
        return (
            coerce_spool_id(self.macros.get_spool_id_for_tool(channel))
            or coerce_spool_id(lane_spool_id(self.helper.printer, channel))
        )

    def take_pending_pick(self, channel):
        if channel not in self.pending_picks:
            return None, False
        return self.pending_picks.pop(channel), True

    def _load_rfid_data(self):
        try:
            with open(self.rfid_data_path) as rfid_file:
                return json.load(rfid_file)
        except Exception:
            return {}

    def _restore_tagged_lane(self, extruder, info):
        in_range = 0 <= extruder < len(self.holders.spool_holders)
        if in_range and not is_untagged_filament(info):
            self.holders.spool_holders[extruder] = info
            self.logs.verbose(f"Restored rfid data for extruder {extruder} from rfid_data.json")

    def sync_spools_tools(self):
        # Auto mode needs no sync: the tool map is read live, never cached. Only manual picks,
        # which have no live source, are replayed onto their mapped tools here.
        if self.helper.mode != 'manual':
            return
        for tool_id in range(MAX_TOOLS_COUNT):
            self._sync_manual_tool(tool_id)

    def _sync_manual_tool(self, tool_id):
        spool_id = self.macros.get_spool_id_for_tool(tool_id)
        if not spool_id:
            return

        def on_spool(spool, spoolman_unanswered, picked_spool_id=spool_id):
            self.holders.spools_by_id[picked_spool_id] = spool
        self.spoolman.resolve_spool({"SPOOL_ID": spool_id}, on_spool)
        extruder = self.u1_tools.extruder_for_tool(tool_id)
        if extruder is not None:
            self.helper.push_spool_to_afc(extruder, spool_id)
