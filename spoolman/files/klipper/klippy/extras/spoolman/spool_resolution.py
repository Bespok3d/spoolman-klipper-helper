"""Resolving a lane's filament info to a Spoolman spool and mirroring it out.

A stored holder resolves to a Spoolman spool id through the configured strategy chain; the id
is then mirrored everywhere the user sees it: the tool macro's spool_id variable, the AFC lane,
and the lane's Spoolman display name. The tool->spool queries answer "which spool does tool N
use" from the macro variable (a manual pick) or the mapped channel's holder (a tag), honoring
the helper's runtime mode. The by-hand entries live here too: SH_BIND_CARD_UID binds the tag
currently sitting on a channel to a chosen spool, and SH_ADD_SPOOL_FROM_TAG turns that tag into
a spool of its own.
"""
from .card_uids import trackable_uid
from .filament_info import filament_info_to_string, is_untagged_filament
from .u1_tools import EXTRUDERS_COUNT
from .unmatched_tag import (
    applied_tag_message,
    candidate_report,
    registered_spool_message,
    registration_problem_message,
    spoolman_silent_message,
    unmatched_tag_message,
)


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
        if not spool or is_untagged_filament(spool):
            self._reapply_manual_pick(extruder)
            return

        self.logs.verbose(
            f"Resolving filament info {filament_info_to_string(spool, self.helper.logging)} "
            f"for extruder {extruder}"
        )

        def on_resolve_spool(resolved, spoolman_unanswered, spool=spool):
            if resolved:
                if not spool.get("SPOOL_ID") and resolved.get("id"):
                    spool["SPOOL_ID"] = resolved["id"]
                spool_id = spool.get("SPOOL_ID") or resolved.get("id")
            else:
                spool_id = spool.get("SPOOL_ID")

            if not spool_id:
                # A tagged lane that matched nothing must stop advertising the old spool.
                # An untagged lane never reaches here: it re-applies a hand pick or stays put.
                self._unbind_lane_spool(extruder)
                self._report_unresolved_tag(extruder, spool, spoolman_unanswered)
                return

            self._bind_resolved_spool(extruder, spool, spool_id)

        self.spoolman.resolve_spool(spool, on_resolve_spool)

    # FILAMENT_DT_UPDATE can blank print_task_config on a lane with no tag. A hand-picked
    # spool_id still sitting on the tool macro is re-applied so color and material come back.
    # No pick means the screen-set config stays: the helper does not write NONE over it.
    def _reapply_manual_pick(self, extruder):
        spool_id = self.macros.get_spool_id_for_tool(extruder)
        if not spool_id:
            self.logs.verbose(f"No filament info for extruder {extruder}. Normal if no RFID tag.")
            return
        self.logs.verbose(
            f"Re-applying hand-picked spool {spool_id} on extruder {extruder}"
        )
        self._apply_spoolman_to_the_lane(extruder, spool_id)

    # A lane with nothing on it has no tag to report and nothing to search for, so it says
    # nothing. A Spoolman that never answered is not a tag that matched nothing: it gets its own
    # line and no shortlist, because a list built from an inventory nobody read would be a lie.
    def _report_unresolved_tag(self, channel, spool, spoolman_unanswered):
        if is_untagged_filament(spool):
            return
        if spoolman_unanswered:
            self.logs.warn(spoolman_silent_message(channel, spool, self.helper.logging))
            return
        self.logs.warn(unmatched_tag_message(channel, spool, self.helper.logging))
        self._offer_own_spools(channel, spool)

    def _offer_own_spools(self, channel, spool):
        def on_candidates(candidates, channel=channel):
            for line in candidate_report(channel, candidates, self.helper.logging):
                self.logs.warn(line)
            self._register_when_nothing_came_close(channel, spool, candidates)

        self.spoolman.search_candidates(spool, on_candidates)

    # Creating waits for the shortlist. A spool that came close is theirs to pick, and a search
    # that reached nobody proves nothing about what they own: only an inventory that answered and
    # held nothing like this tag earns a new spool.
    def _register_when_nothing_came_close(self, channel, spool, candidates):
        if candidates is None or candidates:
            return

        def on_registered(spool_id, problem, new_spool=None, channel=channel, spool=spool):
            self._report_registration(channel, spool, spool_id, problem, new_spool)

        self.spoolman.register_tag_as_spool(spool, on_registered)

    def _report_registration(self, channel, spool, spool_id, problem, new_spool=None):
        if problem:
            self.logs.warn(registration_problem_message(channel, problem, spool))
            return
        self.logs.log(registered_spool_message(channel, spool_id))
        spool["SPOOL_ID"] = spool_id
        self._bind_resolved_spool(channel, spool, spool_id, new_spool)

    def _bind_resolved_spool(self, extruder, spool, spool_id, new_spool=None):
        self.holders.spools_by_id[spool_id] = spool
        self.logs.verbose(f"Got spool_id: {spool_id}")
        tool = f"T{extruder}"
        self.logs.log(
            f"Tool {tool} is using: {filament_info_to_string(spool, self.helper.logging)}"
        )
        self.macros.set_spool_id_for_tool(tool, spool_id)
        self.helper.push_spool_to_afc(extruder, spool_id)
        self._apply_spoolman_to_the_lane(extruder, spool_id, new_spool)

    # The exact inverse of a bind, for a lane whose filament matched no spool: it must stop
    # advertising the one that was there before, in all three places a bind writes to. The holder
    # stays, so the lane still reports the tag it is carrying.
    def _unbind_lane_spool(self, extruder):
        self.macros.set_spool_id_for_tool(f"T{extruder}", None)
        self.helper.push_spool_to_afc(extruder, None)
        self.writer.clear_lane_label(extruder)

    # A resolved tag is mirrored the same way a manual pick is: apply_spool writes the Spoolman
    # record into print_task_config (what Snapmaker Orca reads) and names the AFC lane. Naming
    # the lane alone left Orca on whatever the firmware had filed, so a tagged Silk spool could
    # show correctly in Fluidd and still reach the slicer as Basic. apply_spool itself still
    # refuses an official channel unless spoolman_overrides_tag is on. A spool the helper just
    # created comes back whole, so it is applied in the same breath as the add; asking Spoolman
    # for it again left the lane unnamed until that answer arrived. A lane resolved any other
    # way still has to ask.
    def _apply_spoolman_to_the_lane(self, extruder, spool_id, new_spool=None):
        if new_spool and self.writer.apply_spool(extruder, new_spool):
            return

        def on_spool(spoolman_spool, target_extruder=extruder):
            if spoolman_spool:
                self.writer.apply_spool(target_extruder, spoolman_spool)

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
            self.logs.verbose(f"Cannot find filament info for T{tool_id} on extruder {extruder}")
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

    # Writing the tag onto a spool they picked: what the card says goes onto that spool, and the
    # lane starts reporting it at once, because they picked it while standing at the machine.
    def apply_tag_to_spool(self, channel, spool_id):
        tag = self._tag_on_channel(channel)
        if tag is None:
            return

        def on_applied(problem, channel=channel, tag=tag):
            if problem:
                self.logs.warn(registration_problem_message(channel, problem, tag))
                return
            self.logs.log(applied_tag_message(channel, spool_id))
            tag["SPOOL_ID"] = spool_id
            self._bind_resolved_spool(channel, tag, spool_id)

        self.spoolman.apply_tag_to_spool(tag, spool_id, on_applied)

    # The add button: the tag sitting on the lane becomes a spool of its own and the lane is put
    # on it. Creating binds the tag's UID to the new spool on its own, so the same reel is
    # recognised the next time it goes in.
    def add_spool_from_tag(self, channel):
        tag = self._tag_on_channel(channel)
        if tag is None:
            return

        def on_registered(spool_id, problem, new_spool=None, channel=channel, tag=tag):
            self._report_registration(channel, tag, spool_id, problem, new_spool)

        self.spoolman.register_tag_as_spool(tag, on_registered, asked_by_hand=True)

    def _tag_on_channel(self, channel):
        if not (0 <= channel < EXTRUDERS_COUNT):
            self.logs.error(f"Channel must be 0..{EXTRUDERS_COUNT - 1}")
            return None
        tag = self.holders.spool_holders[channel]
        if not tag or is_untagged_filament(tag):
            self.logs.warn(f"There is no tag on channel {channel} to write onto a spool")
            return None
        return tag

    # Link spool: the reel on the lane is bound to a spool they picked, and the lane is put on that
    # spool at once, the same act writing the tag onto a spool already performs. The bind alone left
    # the lane on whatever it was showing until something else resolved it. A UID another spool
    # already owns is not bound, so that lane is left alone too.
    def bind_channel_card_uid(self, channel, spool_id):
        if not (0 <= channel < EXTRUDERS_COUNT):
            self.logs.error(f"Channel must be 0..{EXTRUDERS_COUNT - 1}")
            return
        tag = self.holders.spool_holders[channel]
        uid = trackable_uid(tag.get("CARD_UID")) if tag else None
        if not uid:
            self.logs.warn(f"No stable tag UID on channel {channel} to bind to spool {spool_id}")
            return
        self.logs.log(f"Binding channel {channel} UID {uid} -> spool {spool_id}")

        def on_bound(bound, channel=channel, tag=tag):
            if not bound:
                return
            tag["SPOOL_ID"] = spool_id
            self._bind_resolved_spool(channel, tag, spool_id)

        self.spoolman.bind_uid(spool_id, uid, on_bound)
