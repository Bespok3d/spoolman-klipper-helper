import json
import logging
import uuid
from urllib.parse import quote

import gcode

from . import card_uids
from .helper_options import HelperOptions
from .spool_candidates import candidate_spools
from .tag_registration import (
    article_number_is_shared,
    article_number_on_another_filament,
    card_details_payload,
    dimensions_of_material,
    filament_payload,
    filament_with_id,
    filaments_with_exact_article_number,
    matching_filament,
    spool_payload,
    vendor_id_named,
    vendor_payload,
)
from .unmatched_tag import (
    REGISTER_ARTICLE_NUMBER_TAKEN,
    REGISTER_DISABLED,
    REGISTER_REFUSED,
    REGISTER_SHARED_ARTICLE_NUMBER,
    REGISTER_TAG_ON_ANOTHER_SPOOL,
    REGISTER_UNMEASURED_MATERIAL,
    REGISTER_UNREACHABLE,
)

PENDING_CLEANUP_SECONDS = 30
MAX_RETRY_DELAY_SECONDS = 5.0
RETRY_DELAY_STEP_SECONDS = 1.0
MAX_REMOTE_METHOD_RETRIES = 10
EMPTY_SPOOL_IDS = (0, "0", "", None)
SPOOLMAN_SILENT_ERROR = "Spoolman did not answer"


# A list answer is usually the whole inventory, which buries the one lane being debugged under
# every spool the user owns, so only its size is logged.
def response_summary(payload):
    if isinstance(payload, list):
        return f"{len(payload)} records"
    return json.dumps(payload)


class Spoolman:
    # `options` is the [spoolman_helper] section. A bench passing none gets the shipped defaults.
    def __init__(self, printer, logs, options=None):
        section = options if options is not None else HelperOptions(None)
        self.printer = printer
        self.logs = logs
        self.gcode = self.printer.lookup_object("gcode")
        self.webhooks = self.printer.lookup_object("webhooks")
        self.strategy_chain = tuple(section.card_uids_strategy)
        self.auto_register = section.card_uids_auto_register
        self.write_form = section.card_uids_write_form
        self.register_from_tag = section.register_from_tag
        self._active_spool_epoch = 0
        self._claims_awaiting_bind = {}
        self._notes_said = set()
        self._new_rolls_awaiting_spool = {}

    def set_active_spool(self, spool_id):
        try:
            spool_id = int(spool_id) if spool_id else None
        except Exception:
            self.logs.warn(f"Cannot set active spool to {spool_id}, value must be a number or None")
            spool_id = None
        self.logs.verbose(f"Active spool is: {spool_id}")
        self._active_spool_epoch += 1
        self._call_set_active_spool(spool_id, self._active_spool_epoch, attempt=0)

    # Right after a Klipper restart Moonraker has not reconnected yet, so its remote methods are
    # not registered and the first push (the ground-truth one) would be lost. Retry with backoff;
    # an epoch guard drops a stale retry when a newer set has superseded it.
    def _call_set_active_spool(self, spool_id, epoch, attempt):
        try:
            self.webhooks.call_remote_method("spoolman_set_active_spool", spool_id=spool_id)
        except gcode.CommandError as command_err:
            if "not registered" not in str(command_err):
                raise
            self._retry_set_active_spool(spool_id, epoch, attempt)

    def _retry_set_active_spool(self, spool_id, epoch, attempt):
        if attempt >= MAX_REMOTE_METHOD_RETRIES:
            self.logs.error(f"Cannot set active spool {spool_id}: Moonraker never registered")
            return
        reactor = self.printer.get_reactor()
        delay = min(MAX_RETRY_DELAY_SECONDS, RETRY_DELAY_STEP_SECONDS * (attempt + 1))

        def retry(eventtime):
            if epoch == self._active_spool_epoch:
                self._call_set_active_spool(spool_id, epoch, attempt + 1)
            return reactor.NEVER

        reactor.register_timer(retry, reactor.monotonic() + delay)

    def fetch_spool(self, spool_id, on_spool):
        def on_result(error, spool):
            if error:
                self.logs.warn(f"Spool fetch failed for {spool_id}: {error}")
            on_spool(None if error else spool)

        SpoolmanRequest(self.webhooks, self.logs, on_result).fetch(
            f"/api/v1/spool/{spool_id}", "")

    # The whole inventory, the one call three different questions are answered from: which spool
    # owns this UID, who already owns it before a bind, and which spools come close to a tag that
    # matched nothing. Archived spools are left out by the server (allow_archived defaults false).
    def _fetch_all_spools(self, on_spools):
        SpoolmanRequest(self.webhooks, self.logs, on_spools).fetch("/api/v1/spool", "")

    # Hands back None when Spoolman could not be reached, an empty list when it answered and
    # nothing came close. The caller has to tell those apart: one is a spool to go and find, the
    # other is a server to go and start.
    def search_candidates(self, filament_info, on_candidates):
        def on_spools(error, spools):
            if error:
                self.logs.verbose(f"Candidate search could not read the inventory: {error}")
                on_candidates(None)
                return
            on_candidates(candidate_spools(filament_info, spools or []))

        self._fetch_all_spools(on_spools)

    # A tag nothing matched and nothing came close to becomes a spool of its own. Their filaments
    # are read first: one of them may already describe this tag, and if none does they are what the
    # new record's diameter and density are copied from. Hands back the new spool id, or the code
    # for why there is none.
    # The setting governs what the helper creates on its own while it resolves a lane. Somebody
    # pressing the add button has asked for this one spool by hand, which is not the thing the
    # setting is there to hold back, so their press goes through with the setting off.
    def register_tag_as_spool(self, filament_info, on_registered, asked_by_hand=False):
        if not (self.register_from_tag or asked_by_hand):
            on_registered(None, REGISTER_DISABLED)
            return

        def on_spools(error, spools):
            if error:
                self._say_registration_unanswered(error, on_registered)
                return
            if self._tag_tied_to_another_spool(filament_info, spools or [], None):
                on_registered(None, REGISTER_TAG_ON_ANOTHER_SPOOL)
                return
            self._register_untied_tag(filament_info, on_registered)

        self._fetch_all_spools(on_spools)

    def _say_registration_unanswered(self, error, on_registered):
        self.logs.verbose(f"Cannot register the tag, Spoolman was silent: {error}")
        on_registered(None, REGISTER_UNREACHABLE)

    def _register_untied_tag(self, filament_info, on_registered):
        def on_filaments(error, filaments):
            if error:
                self._say_registration_unanswered(error, on_registered)
                return
            self._spool_for_tag(filament_info, filaments or [], on_registered)

        SpoolmanRequest(self.webhooks, self.logs, on_filaments).fetch("/api/v1/filament", "")

    def _spool_for_tag(self, filament_info, filaments, on_registered):
        if article_number_is_shared(filament_info, filaments):
            on_registered(None, REGISTER_SHARED_ARTICLE_NUMBER)
            return
        already_theirs = matching_filament(filament_info, filaments)
        if already_theirs:
            self._create_spool(filament_info, already_theirs.get("id"), on_registered)
            return
        dimensions = dimensions_of_material(filament_info, filaments)
        if not dimensions:
            on_registered(None, REGISTER_UNMEASURED_MATERIAL)
            return
        self._create_filament(filament_info, filaments, dimensions, on_registered)

    def _create_filament(self, filament_info, filaments, dimensions, on_registered):
        def with_vendor(vendor_id):
            self._post_filament(filament_info, vendor_id, dimensions, on_registered)

        self._resolve_vendor(filament_info, filaments, with_vendor)

    # A vendor record is only created when the card names a vendor they do not have. A vendor
    # Spoolman would not take is not worth failing the spool over, so the filament goes in without
    # one rather than not at all.
    def _resolve_vendor(self, filament_info, filaments, on_vendor):
        theirs = vendor_id_named(filament_info.get("VENDOR"), filaments)
        payload = vendor_payload(filament_info)
        if theirs or not payload["name"]:
            on_vendor(theirs)
            return

        def on_created(error, vendor):
            on_vendor(None if error else (vendor or {}).get("id"))

        SpoolmanRequest(self.webhooks, self.logs, on_created).fetch(
            "/api/v1/vendor", "", method="POST", body=payload)

    def _post_filament(self, filament_info, vendor_id, dimensions, on_registered):
        def on_created(error, filament):
            filament_id = (filament or {}).get("id")
            if error or not filament_id:
                self.logs.verbose(f"Spoolman refused a filament for the tag: {error}")
                on_registered(None, REGISTER_REFUSED)
                return
            self._create_spool(filament_info, filament_id, on_registered)

        SpoolmanRequest(self.webhooks, self.logs, on_created).fetch(
            "/api/v1/filament", "", method="POST",
            body=filament_payload(filament_info, vendor_id, dimensions))

    # The card is bound to the new spool in the same breath, so the next insert of this spool
    # resolves by its card number instead of creating a second spool for it.
    def _create_spool(self, filament_info, filament_id, on_registered):
        def on_created(error, spool):
            spool_id = (spool or {}).get("id")
            if error or not spool_id:
                self.logs.verbose(f"Spoolman refused a spool for the tag: {error}")
                on_registered(None, REGISTER_REFUSED)
                return
            on_registered(spool_id, None, spool)
            self._bind_claimed_spool(spool_id, card_uids.binding_uids(filament_info))

        SpoolmanRequest(self.webhooks, self.logs, on_created).fetch(
            "/api/v1/spool", "", method="POST", body=spool_payload(filament_id))

    # Writing the tag onto a spool they picked: what the card says goes onto that spool's filament,
    # and the card is bound to the spool so the pick holds next time.
    def apply_tag_to_spool(self, filament_info, spool_id, on_applied):
        if not self.register_from_tag:
            on_applied(REGISTER_DISABLED)
            return

        def on_spools(error, spools):
            if error:
                self.logs.verbose(
                    f"Cannot write the tag onto a spool, Spoolman was silent: {error}")
                on_applied(REGISTER_UNREACHABLE)
                return
            self._write_tag_onto_spool(filament_info, spool_id, spools or [], on_applied)

        self._fetch_all_spools(on_spools)

    def _write_tag_onto_spool(self, filament_info, spool_id, spools, on_applied):
        picked = [spool for spool in spools if spool.get("id") == spool_id]
        filament_id = ((picked[0] if picked else {}).get("filament") or {}).get("id")
        if not filament_id:
            self.logs.verbose(f"Spool {spool_id} is not one of theirs, or carries no filament")
            on_applied(REGISTER_REFUSED)
            return
        if self._tag_tied_to_another_spool(filament_info, spools, spool_id):
            on_applied(REGISTER_TAG_ON_ANOTHER_SPOOL)
            return

        def on_filaments(error, filaments):
            if error:
                self.logs.verbose(
                    f"Cannot check the tag's article number, Spoolman was silent: {error}")
                on_applied(REGISTER_UNREACHABLE)
                return
            if article_number_on_another_filament(filament_info, filaments or [], filament_id):
                on_applied(REGISTER_ARTICLE_NUMBER_TAKEN)
                return
            current_filament = filament_with_id(filaments or [], filament_id)
            self._patch_tag_onto_filament(
                filament_info, spool_id, filament_id, current_filament, on_applied)

        SpoolmanRequest(self.webhooks, self.logs, on_filaments).fetch("/api/v1/filament", "")

    def _patch_tag_onto_filament(
        self, filament_info, spool_id, filament_id, current_filament, on_applied
    ):
        def on_patched(error, payload):
            if error:
                on_applied(REGISTER_REFUSED)
                return
            self.bind_uids(spool_id, card_uids.binding_uids(filament_info))
            on_applied(None)

        SpoolmanRequest(self.webhooks, self.logs, on_patched).fetch(
            f"/api/v1/filament/{filament_id}", "", method="PATCH",
            body=card_details_payload(filament_info, current_filament))

    def patch_location(self, spool_id, location):
        def on_result(error, payload):
            if error:
                self.logs.warn(f"Location update failed for spool {spool_id}: {error}")

        SpoolmanRequest(self.webhooks, self.logs, on_result).fetch(
            f"/api/v1/spool/{spool_id}", "", method="PATCH", body={"location": location})

    def lookup_spoolman(self, sku, callback):
        self.logs.verbose(f"Looking up {sku}")

        def on_filament_result(error, filaments):
            if error:
                self.logs.error(f"filament lookup error for sku {sku}: {json.dumps(error)}")
                callback(f"filaments error for sku {sku}", filaments)
                return
            exact_filaments = filaments_with_exact_article_number(filaments or [], sku)
            if len(exact_filaments) != 1:
                self.logs.log(
                    f"SKU {sku} matches {len(exact_filaments)} filaments exactly "
                    f"({len(filaments or [])} returned), so this spool is not tracked by its SKU")
                callback(None, [])  # a clean "no such filament", never a Spoolman failure
                return
            filament_id = exact_filaments[0]["id"]
            spool_request = SpoolmanRequest(self.webhooks, self.logs, callback)
            spool_request.fetch("/api/v1/spool", f"filament.id={filament_id}")

        filament_request = SpoolmanRequest(self.webhooks, self.logs, on_filament_result)
        filament_request.fetch("/api/v1/filament", f"article_number={quote(str(sku), safe='')}")

    # The callback is handed the resolved spool (None when nothing matched) and whether Spoolman
    # went unanswered along the way, because a lane the user must go and fix in Spoolman and a
    # lane that failed because Spoolman is down are two different problems to report.
    def resolve_spool(self, info, callback):
        uids = card_uids.binding_uids(info)
        self.logs.verbose(
            f"Resolving spool: vendor->{info.get('VENDOR')}, sku->{info.get('SKU')}, "
            f"spool_id->{info.get('SPOOL_ID')}, uids->{uids}, chain->{self.strategy_chain}"
        )
        self._run_strategies(info, uids, list(self.strategy_chain), callback, False)

    def _run_strategies(self, info, uids, remaining, callback, spoolman_unanswered):
        if not remaining:
            self.logs.verbose("Resolution chain exhausted, no strategy matched this tag")
            callback(None, spoolman_unanswered)
            return
        strategy = remaining[0]

        def advance(unanswered=False):
            self._run_strategies(info, uids, remaining[1:], callback,
                                 spoolman_unanswered or unanswered)

        if strategy == "spool_id":
            self._resolve_by_spool_id(info, callback, advance)
        elif strategy == "uid":
            self._resolve_by_uid(uids, callback, advance)
        elif strategy == "sku":
            self._resolve_by_sku(info, uids, callback, advance)
        else:
            advance()  # decoded_id / manual: no automatic resolver yet

    def _resolve_by_spool_id(self, info, callback, advance):
        spool_id = info.get("SPOOL_ID")
        if spool_id and spool_id not in EMPTY_SPOOL_IDS:
            self.logs.debug(f"Resolved by spool_id ({spool_id})")
            callback(spool_id, False)
        else:
            advance()

    def _resolve_by_uid(self, uids, callback, advance):
        if not uids:
            advance()
            return

        def on_spools(error, spools):
            if error:
                advance(unanswered=True)
                return
            spool = card_uids.match_spool_by_card_uids(spools or [], uids)
            if spool:
                self._take_spool_bound_to_tag(spool, uids, spools, callback)
            else:
                advance()

        self._fetch_all_spools(on_spools)

    # Found by one UID, the spool is tied to the others as well: the other side's tag then finds it
    # by the spool identity the two sides share, and a spool bound before that identity was read
    # picks it up. With auto-bind off nothing is written unless the user asks. The lane gets its
    # spool first: a write Moonraker cannot take raises, and must not cost the lane its verdict.
    def _take_spool_bound_to_tag(self, spool, uids, spools, callback):
        self.logs.debug(f"Resolved by one of UIDs {', '.join(uids)}: spool {spool.get('id')}")
        callback(spool, False)
        if self.auto_register:
            self._tie_spool_to_every_uid(spool, uids, spools)

    # A UID of this reel already on another spool means one reel became two spools in Spoolman
    # (an older version could do that), which only the user can sort out.
    def _tie_spool_to_every_uid(self, spool, uids, spools):
        spool_id = spool.get("id")
        missing_uids = card_uids.uids_missing_from_spool(spool, uids)
        if not missing_uids or self._claimed_for_another_spool(missing_uids, spool_id):
            return
        conflict = card_uids.uid_bound_elsewhere(spools, missing_uids, spool_id)
        if conflict is None:
            self._bind_claimed_spool(spool_id, missing_uids)
            return
        _conflicting_uid, owner = conflict
        self._say_once(
            ("one reel on two spools", spool_id, owner.get("id")),
            f"Spools {spool_id} and {owner.get('id')} are the two sides of one reel: archive the "
            "one you do not keep in Spoolman, and the other takes both sides on the next read")

    def _claimed_for_another_spool(self, uids, spool_id):
        return self._claim_of_another_spool(uids, spool_id) is not None

    # A bind in flight is not in the spools in hand yet, but the UID is already taken.
    def _claim_of_another_spool(self, uids, spool_id):
        return next(((uid, self._claims_awaiting_bind[uid]) for uid in uids
                     if self._claims_awaiting_bind.get(uid, spool_id) != spool_id), None)

    # A tag tied to one spool, written onto or made into another, would leave one reel on two
    # spools. `spool_id` is the spool it goes onto, None for a spool still to be made.
    def _tag_tied_to_another_spool(self, filament_info, spools, spool_id):
        uids = card_uids.binding_uids(filament_info)
        conflict = card_uids.uid_bound_elsewhere(spools, uids, spool_id)
        tie = ((conflict[0], conflict[1].get("id")) if conflict
               else self._claim_of_another_spool(uids, spool_id))
        if tie is None:
            return False
        self._say_uid_assigned(*tie)
        return True

    def _say_uid_assigned(self, uid, owner_spool_id):
        self.logs.log(f"UID {uid} already assigned to spool {owner_spool_id}, not re-binding")

    def _resolve_by_sku(self, info, uids, callback, advance):
        sku = info.get("SKU")
        if not sku:
            advance()
            return

        def on_lookup(error, filament_spools):
            if error or not filament_spools:
                self.logs.verbose(f"Cannot find spools for sku: {sku}")
                advance(unanswered=bool(error))
                return
            self._spool_of_this_roll(info, uids, filament_spools, callback, advance)

        self.lookup_spoolman(sku, on_lookup)

    # Each physical roll is its own spool. A tag already bound to a spool is that spool whatever
    # the strategy order, so the owner is looked for first; a tag bound to nothing only ever gets a
    # spool no other tag is bound to, never the spool of another roll of the same filament.
    def _spool_of_this_roll(self, info, uids, filament_spools, callback, advance):
        if not uids:
            self._claim_untagged_spool(info, uids, filament_spools, callback, advance)
            return

        def on_spools(error, spools):
            if error:
                advance(unanswered=True)
                return
            owner = card_uids.match_spool_by_card_uids(spools or [], uids)
            if owner:
                self._take_spool_bound_to_tag(owner, uids, spools, callback)
                return
            self._claim_untagged_spool(info, uids, filament_spools, callback, advance)

        self._fetch_all_spools(on_spools)

    # A claim only shows in Spoolman once its bind lands. Until then a second lane resolving at the
    # same moment would still see the spool untagged and take it for its own roll, so the claims in
    # flight are kept here, per tag, and the spool goes to one roll only.
    def _claim_untagged_spool(self, info, uids, filament_spools, callback, advance):
        own_claim = self._spool_claimed_by(uids, filament_spools)
        if own_claim:
            callback(own_claim, False)
            return
        untagged_spool = card_uids.first_untagged_spool(self._unclaimed_spools(filament_spools))
        if not untagged_spool:
            self._spool_for_new_roll(info, uids, filament_spools, callback, advance)
            return
        self.logs.debug(f"Resolved by sku->{info.get('SKU')}: spool {untagged_spool.get('id')}")
        callback(untagged_spool, False)
        if self.auto_register and uids:
            self._bind_claimed_spool(untagged_spool.get("id"), uids)
        else:
            self._say_spool_is_not_tied(untagged_spool.get("id"), info.get("SKU"), uids)

    # Nothing records which roll took a spool it was not tied to, so the next roll of the same SKU
    # lands on it too. That cannot be told apart here, so the user is told instead.
    def _say_spool_is_not_tied(self, spool_id, sku, uids):
        remedy = (" Link the tag to it with SH_BIND_CARD_UID, or turn on auto-bind, to keep it to "
                  "this roll.") if uids else ""
        self._say_once(
            ("tracked by SKU only", spool_id, tuple(uids)),
            f"Spool {spool_id} is tracked by SKU {sku} only, so another roll of SKU {sku} would "
            f"be tracked on it too.{remedy}")

    # A lane resolves again on every tag read and replay, so a standing condition is told once.
    def _say_once(self, note, message):
        if note in self._notes_said:
            return
        self._notes_said.add(note)
        self.logs.log(message)

    # A claim can be newer than the spools in hand (a spool made after they were fetched), so the
    # claimed id answers on its own then; the chain accepts a bare id as well as a spool.
    def _spool_claimed_by(self, uids, filament_spools):
        claimed_spool_id = next((self._claims_awaiting_bind[uid] for uid in uids
                                 if uid in self._claims_awaiting_bind), None)
        if claimed_spool_id is None:
            return None
        return card_uids.find_spool_by_id(filament_spools, claimed_spool_id) or claimed_spool_id

    def _unclaimed_spools(self, filament_spools):
        claimed_spool_ids = set(self._claims_awaiting_bind.values())
        return [spool for spool in filament_spools if spool.get("id") not in claimed_spool_ids]

    # A later claim of the same UIDs for another spool is that bind's to clear, not this one's.
    def _uids_still_claimed_for(self, uids, spool_id):
        return [uid for uid in uids if self._claims_awaiting_bind.get(uid) == spool_id]

    # Callers give the lane its spool before this: a bind Moonraker cannot take raises here.
    def _bind_claimed_spool(self, spool_id, uids):
        def on_bound(_bound):
            for uid in self._uids_still_claimed_for(uids, spool_id):
                self._claims_awaiting_bind.pop(uid)

        self._claims_awaiting_bind.update(dict.fromkeys(uids, spool_id))
        self.bind_uids(spool_id, uids, on_bound)

    # Every spool of the filament already carries a tag. When this tag and every one of those spools
    # hold a spool identity both sides share, a tag bound to none of them is a new roll and gets a
    # spool of its own. Otherwise it may just as well be the other side of one of those spools (a
    # spool bound before its identity was read holds one side's tag only), so no spool is made: the
    # lane is left for the user to link, or to add as a new spool, by hand.
    def _spool_for_new_roll(self, info, uids, filament_spools, callback, advance):
        sku = info.get("SKU")
        filament_id = (filament_spools[0].get("filament") or {}).get("id")
        if not (self.register_from_tag and filament_id):
            self.logs.verbose(f"Every spool for SKU {sku} has another roll's tag, none is this one")
            advance()
            return
        if not (card_uids.spool_uid(info) and self._every_spool_holds_identity(filament_spools)):
            self._say_no_spool_for_unidentified_roll(sku, uids)
            advance()
            return
        self._create_spool_for_new_roll(info, filament_id, callback, advance)

    def _every_spool_holds_identity(self, filament_spools):
        return all(card_uids.holds_spool_identity(self._uids_of_spool(spool))
                   for spool in filament_spools)

    # A spool made a moment ago holds its UIDs in the claim until its bind lands in Spoolman.
    def _uids_of_spool(self, spool):
        claimed_uids = [uid for uid, claimed_spool_id in self._claims_awaiting_bind.items()
                        if claimed_spool_id == spool.get("id")]
        return card_uids.spool_card_uids(spool) + claimed_uids

    def _say_no_spool_for_unidentified_roll(self, sku, uids):
        if not uids:
            self.logs.verbose(f"Every spool for SKU {sku} has another roll's tag, and this tag has "
                              "no stable UID to tie a new spool to")
            return
        self._say_once(
            ("no spool for unidentified roll", tuple(uids)),
            f"Every spool for SKU {sku} already has a tag, and this tag may be the other side of "
            f"one of them, so no spool is made for it. Link it to its spool with "
            f"SH_BIND_CARD_UID, or add it in Spoolman as a new spool")

    # A roll can resolve again before its new spool lands in Spoolman (a tag read and a replay at
    # the same moment), so that resolution waits for the one spool instead of making a second.
    def _create_spool_for_new_roll(self, info, filament_id, callback, advance):
        roll_identity = card_uids.spool_uid(info)
        creation_in_flight = roll_identity in self._new_rolls_awaiting_spool
        self._new_rolls_awaiting_spool.setdefault(roll_identity, []).append((callback, advance))
        if creation_in_flight:
            return

        def on_registered(spool_id, problem, new_spool=None):
            waiting_resolutions = self._new_rolls_awaiting_spool.pop(roll_identity, [])
            self._report_new_roll_spool(info.get("SKU"), spool_id, problem)
            for on_resolved, advance_resolution in waiting_resolutions:
                self._finish_new_roll_resolution(
                    new_spool, problem, on_resolved, advance_resolution)

        self._create_spool(info, filament_id, on_registered)

    def _report_new_roll_spool(self, sku, spool_id, problem):
        if problem:
            self.logs.warn(f"Spoolman did not make a spool for this new roll of SKU {sku}")
            return
        self.logs.log(
            f"Every spool for SKU {sku} has another roll's tag, so this roll is now "
            f"Spoolman spool {spool_id}")

    def _finish_new_roll_resolution(self, new_spool, problem, on_resolved, advance):
        if problem:
            advance()
            return
        on_resolved(new_spool, False)

    def define_card_uids_field(self):
        def on_done(error, payload):
            self.logs.debug(f"card_uids field define result: error={error}")

        SpoolmanRequest(self.webhooks, self.logs, on_done).fetch(
            f"/api/v1/field/spool/{card_uids.CARD_UIDS_FIELD}", "",
            method="POST", body=card_uids.card_uids_field_definition())

    def bind_uids(self, spool_id, uids, on_done=None):
        if not uids:
            self.logs.warn("Refusing to bind an empty or non-stable UID")
            if on_done:
                on_done(False)
            return

        def on_spools(error, spools):
            if error:
                self._report_failed_bind(spool_id, error, on_done)
                return
            self._bind_unless_assigned(spool_id, uids, spools or [], on_done)

        self._fetch_all_spools(on_spools)

    # Without the inventory there is no telling whether another spool owns the UID, and no current
    # extra to merge into, so a PATCH then would wipe the spool's other extra fields.
    def _report_failed_bind(self, spool_id, error, on_done):
        self.logs.log(f"Bind failed for spool {spool_id}: {error}")
        if on_done:
            on_done(False)

    def _bind_unless_assigned(self, spool_id, uids, spools, on_done):
        conflict = card_uids.uid_bound_elsewhere(spools, uids, spool_id)
        if conflict is not None:
            conflicting_uid, owner = conflict
            self._say_uid_assigned(conflicting_uid, owner.get("id"))
            if on_done:
                on_done(False)
            return
        target = card_uids.find_spool_by_id(spools, spool_id)
        if target is None:
            self._bind_spool_left_out_of_inventory(spool_id, uids, on_done)
            return
        self._bind_missing_uids(spool_id, uids, target, on_done)

    # The inventory leaves archived spools out, and Spoolman replaces the whole extra on a PATCH,
    # so such a spool is read on its own: merging onto nothing would wipe its other extra fields.
    def _bind_spool_left_out_of_inventory(self, spool_id, uids, on_done):
        def on_spool(error, spool):
            if error or not spool:
                self._report_failed_bind(spool_id, error or "Spoolman sent no spool", on_done)
                return
            self._bind_missing_uids(spool_id, uids, spool, on_done)

        SpoolmanRequest(self.webhooks, self.logs, on_spool).fetch(f"/api/v1/spool/{spool_id}", "")

    def _bind_missing_uids(self, spool_id, uids, target, on_done):
        missing_uids = card_uids.uids_missing_from_spool(target, uids)
        if not missing_uids:
            self.logs.debug(f"UID {', '.join(uids)} already bound to spool {spool_id}")
            if on_done:
                on_done(True)
            return
        self._patch_card_uids(spool_id, missing_uids, target, on_done)

    def _patch_card_uids(self, spool_id, uids, spool, on_done):
        extra = card_uids.merged_extra_with_card_uids(spool, uids, self.write_form)

        def on_patched(error, payload):
            if error:
                self.logs.log(f"Bind failed for spool {spool_id}: {error}")
            else:
                self.logs.log(f"Bound UID {', '.join(uids)} -> spool {spool_id}")
            if on_done:
                on_done(not error)

        SpoolmanRequest(self.webhooks, self.logs, on_patched).fetch(
            f"/api/v1/spool/{spool_id}", "", method="PATCH", body={"extra": extra})


class SpoolmanRequest:
    _pending = {}

    def __init__(self, webhooks, logs, callback):
        self.webhooks = webhooks
        self.logs = logs
        self.callback = callback
        self.request_id = uuid.uuid4().hex
        self.reactor = webhooks.printer.get_reactor()
        self._retry_args = None
        self._endpoint_registered = False
        self._retry_count = 0

    def fetch(self, path, query, method="GET", body=None):
        try:
            self._dispatch_fetch(path, query, method, body)
        except gcode.CommandError as command_err:
            if "not registered" not in str(command_err):
                raise
            self._schedule_retry(path, query, method, body)
        except Exception as fetch_err:
            logging.exception("fetch error")
            self.logs.error(f"Spoolman request {method} {path} could not be sent: {fetch_err}")

    # A request Moonraker never answers used to be dropped in silence, leaving the resolution
    # chain waiting on a callback that would never come. The give-up says so on the console and
    # answers the chain, so the lane reaches a verdict instead of sitting in limbo. A timer armed
    # for a superseded attempt is stale and says nothing; the live attempt owns the verdict.
    def _arm_cleanup_timer(self):
        armed_for_attempt = self._retry_count

        def _cleanup(eventtime):
            if armed_for_attempt == self._retry_count:
                self._give_up_on_answer(f"no reply in {PENDING_CLEANUP_SECONDS}s")
            return self.reactor.NEVER

        self.reactor.register_timer(_cleanup, self.reactor.monotonic() + PENDING_CLEANUP_SECONDS)

    def _give_up_on_answer(self, reason):
        if SpoolmanRequest._pending.pop(self.request_id, None) is None:
            return
        self.logs.warn(f"{SPOOLMAN_SILENT_ERROR}: {reason}")
        if not self.callback:
            return
        try:
            self.callback(SPOOLMAN_SILENT_ERROR, None)
        except Exception:
            logging.exception("SpoolmanRequest give-up callback failed")

    def _dispatch_fetch(self, path, query, method, body):
        cb_endpoint = f"spoolman_helper/result/{self.request_id}"
        self.logs.debug(f"SpoolmanRequest fetch: {path}?{query} id={self.request_id}")
        SpoolmanRequest._pending[self.request_id] = self
        self._arm_cleanup_timer()
        if not self._endpoint_registered:
            self.webhooks.register_endpoint(cb_endpoint, SpoolmanRequest._dispatch)
            self._endpoint_registered = True
        self.webhooks.call_remote_method(
            "spoolman_proxy",
            cb_endpoint=cb_endpoint,
            request_method=method,
            path=path,
            query=query,
            body=body,
        )

    def _schedule_retry(self, path, query, method, body):
        self._retry_args = (path, query, method, body)
        self._retry_count += 1
        if self._retry_count > MAX_REMOTE_METHOD_RETRIES:
            self._give_up_on_answer(
                f"Moonraker never took the request after {self._retry_count} attempts")
            return
        delay = min(MAX_RETRY_DELAY_SECONDS, RETRY_DELAY_STEP_SECONDS * self._retry_count)
        self.logs.debug(
            f"spoolman_proxy not ready, retry {self._retry_count} in {delay}s "
            f"id={self.request_id}"
        )
        self.reactor.register_timer(self._retry_fetch, self.reactor.monotonic() + delay)

    def _retry_fetch(self, eventtime):
        if not self._retry_args:
            return self.reactor.NEVER
        path, query, method, body = self._retry_args
        self._retry_args = None
        self.logs.debug(f"Retrying fetch id={self.request_id}")
        self.fetch(path, query, method, body)
        return self.reactor.NEVER

    @staticmethod
    def _dispatch(web_request):
        try:
            request_id = web_request.method.split("/")[-1]
        except Exception:
            web_request.send({"ok": False, "error": "bad callback path"})
            return
        inst = SpoolmanRequest._pending.pop(request_id, None)
        if not inst:
            web_request.send({"ok": False, "error": "unknown request"})
            return
        inst._on_result(web_request)

    def _on_result(self, web_request):
        params = web_request.params
        payload = params.get("payload")
        error = params.get("error")
        self.logs.debug(
            f"_on_result id={self.request_id} payload={response_summary(payload)} error={error}"
        )
        if self.callback:
            try:
                self.callback(error, payload)
            except Exception:
                logging.exception("SpoolmanRequest callback failed")
        web_request.send({"ok": True})
