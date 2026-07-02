"""The per-channel spool holders and the resolved-spool cache.

One holder per physical channel carries the filament info the printer reported for that lane
(tag data, or an untagged-but-loaded marker); ``spools_by_id`` caches every resolved spool so
labels and summaries can name it. Filament presence is an injected callable: whether a lane
physically holds filament is device knowledge, and this store stays free of it.
"""
from .filament_info import is_untagged_filament
from .u1_tools import EXTRUDERS_COUNT


def _merge_detected_fields(holder, detected_fields):
    if not holder or not detected_fields:
        return
    for field, value in detected_fields.items():
        if value is not None:
            holder[field] = value


class SpoolHolders:
    def __init__(self, logs, macros, push_spool_to_afc, lane_has_filament):
        self.logs = logs
        self.macros = macros
        self.push_spool_to_afc = push_spool_to_afc
        self.lane_has_filament = lane_has_filament
        self.spool_holders = [None] * EXTRUDERS_COUNT
        self.spools_by_id = {}

    def store_channel_report(self, channel, filament_info):
        """Store a channel's filament report; True when a tagged spool needs resolving."""
        self.logs.verbose(f"Received spool for extruder {channel}")
        if not (0 <= channel < EXTRUDERS_COUNT):
            self.logs.error(f"Extruder must be 0..{EXTRUDERS_COUNT - 1}")
            return False
        if is_untagged_filament(filament_info):
            self._note_untagged_lane(channel, filament_info)
            return False
        self.spool_holders[channel] = filament_info
        return True

    # An untagged report is a "loaded but unidentified" spool only when filament is actually
    # present. DETECT_SPOOLS re-reads every channel, so a bare lane reports untagged too -- that
    # is empty, not UNKNOWN. The injected presence callable is the signal.
    def _note_untagged_lane(self, channel, filament_info):
        if self.lane_has_filament(channel):
            self.spool_holders[channel] = filament_info
            self.logs.log(f"Tool T{channel} loaded with UNKNOWN filament (unassigned, no tag)")
        else:
            self.spool_holders[channel] = None
            self.logs.verbose(f"Tool T{channel} is empty")

    def clear_channel(self, channel, force=False):
        self.logs.log(f"Clearing spool from extruder {channel}")
        if channel is None:
            return
        self.macros.set_spool_id_for_tool(f"T{channel}", None)
        self.push_spool_to_afc(channel, None)
        if force:
            self.macros.clear_print_task_config(channel)
        self._drop_cached_spool(channel)
        self.spool_holders[channel] = None

    def _drop_cached_spool(self, channel):
        holder = self.spool_holders[channel]
        if holder is None:
            return
        spool_id = holder.get("SPOOL_ID")
        if spool_id and spool_id in self.spools_by_id:
            del self.spools_by_id[spool_id]

    def lane_is_tagged(self, channel):
        holder = self.spool_holders[channel] if 0 <= channel < EXTRUDERS_COUNT else None
        return bool(holder) and not is_untagged_filament(holder)

    def merge_detected_spools(self, detected_spools):
        for holder, detected_fields in zip(self.spool_holders, detected_spools):
            _merge_detected_fields(holder, detected_fields)

    def clear_ids(self):
        self.spools_by_id.clear()
