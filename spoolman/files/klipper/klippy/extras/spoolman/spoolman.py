import json
import logging
import uuid

import gcode

from . import card_uids
from .helper_options import HelperOptions
from .spool_candidates import candidate_spools
from .tag_registration import (
    card_details_payload,
    dimensions_of_material,
    filament_payload,
    matching_filament,
    spool_payload,
    vendor_id_named,
    vendor_payload,
)
from .unmatched_tag import (
    REGISTER_DISABLED,
    REGISTER_REFUSED,
    REGISTER_UNMEASURED_MATERIAL,
    REGISTER_UNREACHABLE,
)

PENDING_CLEANUP_SECONDS = 30
MAX_RETRY_DELAY_SECONDS = 5.0
RETRY_DELAY_STEP_SECONDS = 1.0
MAX_REMOTE_METHOD_RETRIES = 10
EMPTY_SPOOL_IDS = (0, "0", "", None)
SPOOLMAN_SILENT_ERROR = "Spoolman did not answer"


# Spoolman matches `article_number` as a SUBSTRING, so a short SKU like "2" comes back carrying
# every filament whose article number merely contains it. Only an exact article number is this
# tag's filament.
def filaments_with_exact_article_number(filaments, sku):
    tag_article_number = str(sku).strip()
    return [filament for filament in filaments
            if str(filament.get("article_number", "")).strip() == tag_article_number]


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

        def on_filaments(error, filaments):
            if error:
                self.logs.verbose(f"Cannot register the tag, Spoolman was silent: {error}")
                on_registered(None, REGISTER_UNREACHABLE)
                return
            self._spool_for_tag(filament_info, filaments or [], on_registered)

        SpoolmanRequest(self.webhooks, self.logs, on_filaments).fetch("/api/v1/filament", "")

    def _spool_for_tag(self, filament_info, filaments, on_registered):
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
            self.bind_uid(spool_id, card_uids.trackable_uid(filament_info.get("CARD_UID")))
            on_registered(spool_id, None, spool)

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

        def on_patched(error, payload):
            if error:
                on_applied(REGISTER_REFUSED)
                return
            self.bind_uid(spool_id, card_uids.trackable_uid(filament_info.get("CARD_UID")))
            on_applied(None)

        SpoolmanRequest(self.webhooks, self.logs, on_patched).fetch(
            f"/api/v1/filament/{filament_id}", "", method="PATCH",
            body=card_details_payload(filament_info))

    def patch_location(self, spool_id, location):
        def on_result(error, payload):
            if error:
                self.logs.warn(f"Location update failed for spool {spool_id}: {error}")

        SpoolmanRequest(self.webhooks, self.logs, on_result).fetch(
            f"/api/v1/spool/{spool_id}", "", method="PATCH", body={"location": location})

    def lookup_spoolman(self, sku, callback):
        self.logs.verbose(f"Looking up {sku}")

        def on_spool_result(error, spools):
            self.logs.debug(f"SPOOL RESULT -> {spools}")
            callback(error, spools)

        def on_filament_result(error, filaments):
            self.logs.debug(f"FILAMENT RESULT -> {filaments}")
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
            spool_request = SpoolmanRequest(self.webhooks, self.logs, on_spool_result)
            spool_request.fetch("/api/v1/spool", f"filament.id={filament_id}")

        filament_request = SpoolmanRequest(self.webhooks, self.logs, on_filament_result)
        filament_request.fetch("/api/v1/filament", f"article_number={sku}")

    # The callback is handed the resolved spool (None when nothing matched) and whether Spoolman
    # went unanswered along the way, because a lane the user must go and fix in Spoolman and a
    # lane that failed because Spoolman is down are two different problems to report.
    def resolve_spool(self, info, callback):
        uid = card_uids.trackable_uid(info.get("CARD_UID"))
        self.logs.verbose(
            f"Resolving spool: vendor->{info.get('VENDOR')}, sku->{info.get('SKU')}, "
            f"spool_id->{info.get('SPOOL_ID')}, uid->{uid}, chain->{self.strategy_chain}"
        )
        self._run_strategies(info, uid, list(self.strategy_chain), callback, False)

    def _run_strategies(self, info, uid, remaining, callback, spoolman_unanswered):
        if not remaining:
            self.logs.verbose("Resolution chain exhausted, no strategy matched this tag")
            callback(None, spoolman_unanswered)
            return
        strategy = remaining[0]

        def advance(unanswered=False):
            self._run_strategies(info, uid, remaining[1:], callback,
                                 spoolman_unanswered or unanswered)

        if strategy == "spool_id":
            self._resolve_by_spool_id(info, callback, advance)
        elif strategy == "uid":
            self._resolve_by_uid(uid, callback, advance)
        elif strategy == "sku":
            self._resolve_by_sku(info, uid, callback, advance)
        else:
            advance()  # decoded_id / manual: no automatic resolver yet

    def _resolve_by_spool_id(self, info, callback, advance):
        spool_id = info.get("SPOOL_ID")
        if spool_id and spool_id not in EMPTY_SPOOL_IDS:
            self.logs.debug(f"Resolved by spool_id ({spool_id})")
            callback(spool_id, False)
        else:
            advance()

    def _resolve_by_uid(self, uid, callback, advance):
        if not uid:
            advance()
            return

        def on_spools(error, spools):
            if error:
                advance(unanswered=True)
                return
            spool = card_uids.match_spool_by_card_uid(spools or [], uid)
            if spool:
                self.logs.debug(f"Resolved by UID {uid}: spool {spool.get('id')}")
                callback(spool, False)
            else:
                advance()

        self._fetch_all_spools(on_spools)

    def _resolve_by_sku(self, info, uid, callback, advance):
        sku = info.get("SKU")
        if not sku:
            advance()
            return

        def on_lookup(error, spools):
            if error or not spools:
                self.logs.verbose(f"Cannot find spools for sku: {sku}")
                advance(unanswered=bool(error))
                return
            self.logs.debug(f"Resolved by sku->{sku}: spool {spools[0].get('id')}")
            self._auto_register_uid(spools[0], uid)
            callback(spools[0], False)

        self.lookup_spoolman(sku, on_lookup)

    def _auto_register_uid(self, spool, uid):
        if not (self.auto_register and uid) or card_uids.spool_has_card_uid(spool, uid):
            return
        spool_id = spool.get("id")
        if spool_id:
            self.bind_uid(spool_id, uid)

    def define_card_uids_field(self):
        def on_done(error, payload):
            self.logs.debug(f"card_uids field define result: error={error}")

        SpoolmanRequest(self.webhooks, self.logs, on_done).fetch(
            f"/api/v1/field/spool/{card_uids.CARD_UIDS_FIELD}", "",
            method="POST", body=card_uids.card_uids_field_definition())

    def bind_uid(self, spool_id, uid, on_done=None):
        if not uid:
            self.logs.warn("Refusing to bind an empty or non-stable UID")
            if on_done:
                on_done(False)
            return

        def on_spools(error, spools):
            self._bind_unless_assigned(spool_id, uid, spools or [], on_done)

        self._fetch_all_spools(on_spools)

    def _bind_unless_assigned(self, spool_id, uid, spools, on_done):
        owner = card_uids.match_spool_by_card_uid(spools, uid)
        if owner is not None:
            owner_id = owner.get("id")
            self.logs.log(f"UID {uid} already assigned to spool {owner_id}, not re-binding")
            if on_done:
                on_done(owner_id == spool_id)
            return
        target = card_uids.find_spool_by_id(spools, spool_id) or {}
        self._patch_card_uids(spool_id, uid, target, on_done)

    def _patch_card_uids(self, spool_id, uid, spool, on_done):
        extra = card_uids.merged_extra_with_card_uids(spool, uid, self.write_form)

        def on_patched(error, payload):
            if error:
                self.logs.log(f"Bind failed for spool {spool_id}: {error}")
            else:
                self.logs.log(f"Bound UID {uid} -> spool {spool_id}")
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
            f"_on_result id={self.request_id} payload={json.dumps(payload)} error={error}"
        )
        if self.callback:
            try:
                self.callback(error, payload)
            except Exception:
                logging.exception("SpoolmanRequest callback failed")
        web_request.send({"ok": True})
