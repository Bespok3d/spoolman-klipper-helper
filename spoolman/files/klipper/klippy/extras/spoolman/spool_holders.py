"""The per-channel spool holders and the resolved-spool cache.

One holder per physical channel carries the filament info the printer reported for that lane
(tag data, or an untagged-but-loaded marker); ``spools_by_id`` caches every resolved spool so
labels and summaries can name it. Filament presence and releasing a lane's spool are injected
callables: whether a lane physically holds filament is device knowledge, which tools a lane feeds
is the tracking's, and this store stays free of both.
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
    def __init__(self, logs, macros, release_lane, lane_has_filament):
        self.logs = logs
        self.macros = macros
        self.release_lane = release_lane
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
            self.logs.log(f"Lane E{channel} loaded with UNKNOWN filament (unassigned, no tag)")
        else:
            self.spool_holders[channel] = None
            self.logs.verbose(f"Lane E{channel} is empty")

    def clear_channel(self, channel, force=False):
        self.logs.log(f"Clearing spool from extruder {channel}")
        if channel is None:
            return
        if force:
            self.macros.clear_print_task_config(channel)
        self._drop_cached_spool(channel)
        self.spool_holders[channel] = None
        self.release_lane(channel)

    def _drop_cached_spool(self, channel):
        holder = self.spool_holders[channel]
        if holder is None:
            return
        spool_id = holder.get("SPOOL_ID")
        if spool_id and spool_id in self.spools_by_id:
            del self.spools_by_id[spool_id]

    # Drops the tag report only: the lane's pick and the firmware color stay as they are.
    def forget_tag(self, channel):
        if 0 <= channel < EXTRUDERS_COUNT:
            self.spool_holders[channel] = None

    def tag_on_lane(self, channel):
        holder = self.spool_holders[channel] if 0 <= channel < EXTRUDERS_COUNT else None
        return None if is_untagged_filament(holder) else holder

    def lane_is_tagged(self, channel):
        return self.tag_on_lane(channel) is not None

    def merge_detected_spools(self, detected_spools):
        for holder, detected_fields in zip(self.spool_holders, detected_spools):
            _merge_detected_fields(holder, detected_fields)

    def clear_ids(self):
        self.spools_by_id.clear()
