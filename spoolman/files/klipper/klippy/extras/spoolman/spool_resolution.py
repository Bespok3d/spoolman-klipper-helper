"""Resolving a lane's filament info to a Spoolman spool and mirroring it out.

A stored holder resolves to a Spoolman spool id through the configured strategy chain; the id
is then mirrored everywhere the user sees it: the tool macro's spool_id variable, the AFC lane,
and the lane's Spoolman display name. The tool->spool queries answer "which spool does tool N
use" from the macro variable (a manual pick) or the mapped channel's holder (a tag), honoring
the helper's runtime mode. The UID-binding entry (SH_BIND_CARD_UID) lives here too: it binds
the tag currently sitting on a channel to a chosen spool.
"""
from .card_uids import trackable_uid
from .filament_info import filament_info_to_string, is_untagged_filament
from .u1_tools import EXTRUDERS_COUNT


class SpoolResolution:
    def __init__(self, helper):
        self.helper = helper
        self.logs = helper.logs
        self.macros = helper.macros
        self.spoolman = helper.spoolman
        self.u1_tools = helper.u1_tools
        self.writer = helper.writer
        self.holders = helper.holders

    def apply_spool_for_extruder(self, extruder):
        self.logs.verbose(f"Trying to bind spool to extruder {extruder}")
        spool = self.holders.spool_holders[extruder]
        if not spool:
            self.logs.warn(f"No filament info for extruder {extruder}. Normal if no RFID tag.")
            return

        self.logs.verbose(
            f"Resolving filament info {filament_info_to_string(spool, self.helper.logging)} "
            f"for extruder {extruder}"
        )

        def on_resolve_spool(resolved, spool=spool):
            if resolved:
                if not spool.get("SPOOL_ID") and resolved.get("id"):
                    spool["SPOOL_ID"] = resolved["id"]
                spool_id = spool.get("SPOOL_ID") or resolved.get("id")
            else:
                spool_id = spool.get("SPOOL_ID")

            if not spool_id:
                self.logs.warn(
                    f"Unable to resolve spool id for extruder {extruder} and filament "
                    f"{filament_info_to_string(spool, self.helper.logging)}"
                )
                self._unbind_lane_spool(extruder)
                return

            self._bind_resolved_spool(extruder, spool, spool_id)

        self.spoolman.resolve_spool(spool, on_resolve_spool)

    def _bind_resolved_spool(self, extruder, spool, spool_id):
        self.holders.spools_by_id[spool_id] = spool
        self.logs.verbose(f"Got spool_id: {spool_id}")
        tool = f"T{extruder}"
        self.logs.log(
            f"Tool {tool} is using: {filament_info_to_string(spool, self.helper.logging)}"
        )
        self.macros.set_spool_id_for_tool(tool, spool_id)
        self.helper.push_spool_to_afc(extruder, spool_id)
        self._label_lane_from_spoolman(extruder, spool_id)

    # The exact inverse of a bind, for a lane whose filament matched no spool: it must stop
    # advertising the one that was there before, in all three places a bind writes to. The holder
    # stays, so the lane still reports the tag it is carrying.
    def _unbind_lane_spool(self, extruder):
        self.macros.set_spool_id_for_tool(f"T{extruder}", None)
        self.helper.push_spool_to_afc(extruder, None)
        self.writer.clear_lane_label(extruder)

    # The AFC panel only displays a lane name the helper pushed; an RFID-resolved lane deserves
    # one as much as a manual pick does (the tag's own vendor/type is not the Spoolman name).
    def _label_lane_from_spoolman(self, extruder, spool_id):
        def on_spool(spoolman_spool, target_extruder=extruder):
            if spoolman_spool:
                self.writer.label_lane(target_extruder, spoolman_spool)

        self.spoolman.fetch_spool(spool_id, on_spool)

    def find_spool_for_tool(self, tool_id):
        macro_spool = self.get_spool_for_tool(tool_id)
        mapped_spool = self.get_mapped_spool_for_tool(tool_id)
        self.logs.verbose(f"Possible spools: macro->{macro_spool}, mapped->{mapped_spool}")
        if self.helper.mode == 'manual':
            return macro_spool or mapped_spool
        else:
            return mapped_spool if mapped_spool and "SPOOL_ID" in mapped_spool else macro_spool

    def get_spool_for_tool(self, tool_id):
        spool_id = self.macros.get_spool_id_for_tool(tool_id)
        if spool_id:
            return self.holders.spools_by_id.get(spool_id, {"SPOOL_ID": spool_id})

    def get_mapped_spool_for_tool(self, tool_id):
        self.logs.verbose(f"Resolving extruder for T{tool_id}")
        extruder = self.u1_tools.extruder_for_tool(tool_id)
        if extruder is None:
            self.logs.warn(f"Cannot find mapped extruder for T{tool_id}")
            return None
        spool = self.holders.spool_holders[extruder]
        if is_untagged_filament(spool):
            self.logs.verbose(f"Filament for T{extruder} is untagged, falling back to manual")
            spool = self.get_spool_for_tool(extruder)
        if spool is None:
            self.logs.warn(f"Cannot find filament info for T{tool_id} on extruder {extruder}")
            return None
        self.logs.verbose(
            f"Found filament for T{tool_id} on extruder {extruder}: "
            f"{filament_info_to_string(spool, self.helper.logging)}"
        )
        return spool

    def set_active_tool(self, tool_id):
        spool = self.find_spool_for_tool(tool_id)
        self.logs.verbose(f"Spool for requested tool: {spool}")
        if not (spool and spool.get("SPOOL_ID")):
            self.logs.warn(f"Cannot set active spool for T{tool_id}: unable to resolve spool id")
            return
        self.helper.tracking.track_tool_spool(spool["SPOOL_ID"])

    def bind_channel_card_uid(self, channel, spool_id):
        if not (0 <= channel < EXTRUDERS_COUNT):
            self.logs.error(f"Channel must be 0..{EXTRUDERS_COUNT - 1}")
            return
        info = self.holders.spool_holders[channel]
        uid = trackable_uid(info.get("CARD_UID")) if info else None
        if not uid:
            self.logs.warn(f"No stable tag UID on channel {channel} to bind to spool {spool_id}")
            return
        self.logs.log(f"Binding channel {channel} UID {uid} -> spool {spool_id}")
        self.spoolman.bind_uid(spool_id, uid)
