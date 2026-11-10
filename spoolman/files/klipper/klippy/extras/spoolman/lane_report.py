"""SH_DUMP_SPOOLS rendering: an honest summary of what each lane effectively carries."""
import json

from .afc import lane_filament_name
from .filament_info import filament_info_to_string, is_untagged_filament
from .u1_tools import EXTRUDERS_COUNT


class LaneReport:
    def __init__(self, helper):
        self.helper = helper
        self.printer = helper.printer
        self.logs = helper.logs
        self.macros = helper.macros
        self.holders = helper.holders

    def dump(self, raw):
        if raw:
            self.logs.log(
                f"\nspool_holders: {json.dumps(self.holders.spool_holders, indent=2)}\n"
                f"spools_by_id: {self.holders.spools_by_id}"
            )
            return
        self.logs.log("Dumping Spool Holders:")
        for channel in range(EXTRUDERS_COUNT):
            self.logs.log(f"T{channel}: {self._lane_summary(channel)}")

    # The spool a lane effectively carries: a real detected (tagged/resolved) spool wins;
    # otherwise a hand-assigned Spoolman spool_id; otherwise a loaded-but-unidentified spool is
    # UNKNOWN and a bare lane is empty. Keeps DUMP honest about a manual pick instead of UNKNOWN.
    def _lane_summary(self, channel):
        holder = self.holders.spool_holders[channel]
        if holder and not is_untagged_filament(holder):
            return filament_info_to_string(holder, self.helper.logging)
        manual = self._manual_lane_summary(channel)
        if manual:
            return manual
        return "UNKNOWN (loaded, no tag)" if holder else "empty"

    def _manual_lane_summary(self, channel):
        assigned = self.macros.get_spool_id_for_tool(channel)
        if not assigned:
            return None
        resolved = self.holders.spools_by_id.get(assigned)
        if resolved:
            return filament_info_to_string(resolved, self.helper.logging)
        label = self._assigned_lane_label(channel)
        return f"Spoolman spool {assigned} (manually assigned){label}"

    # A manual pick the resolver has not cached yet still gets named: the helper already pushed
    # "<vendor> <filament name>" onto the AFC lane, so DUMP reads it back from there.
    def _assigned_lane_label(self, channel):
        name = lane_filament_name(self.printer, channel)
        return f": {name}" if name else ""
