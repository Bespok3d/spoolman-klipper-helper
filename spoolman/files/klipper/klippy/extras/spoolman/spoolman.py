import json
import logging
import uuid

import gcode

from . import nfc_tracking

PENDING_CLEANUP_SECONDS = 30
MAX_RETRY_DELAY_SECONDS = 5.0
RETRY_DELAY_STEP_SECONDS = 1.0
EMPTY_SPOOL_IDS = (0, "0", "", None)


class Spoolman:
    def __init__(self, printer, logs, strategy_chain=nfc_tracking.DEFAULT_STRATEGY,
                 auto_register=True):
        self.printer = printer
        self.logs = logs
        self.gcode = self.printer.lookup_object("gcode")
        self.webhooks = self.printer.lookup_object("webhooks")
        self.strategy_chain = tuple(strategy_chain)
        self.auto_register = auto_register

    def set_active_spool(self, spool_id):
        try:
            spool_id = int(spool_id) if spool_id else None
        except Exception:
            self.logs.warn(f"Cannot set active spool to {spool_id}, value must be a number or None")
            spool_id = None
        self.logs.verbose(f"Active spool is: {spool_id}")
        self.webhooks.call_remote_method("spoolman_set_active_spool", spool_id=spool_id)

    def clear_active_spool(self):
        self.logs.verbose("Active spool cleared")
        self.set_active_spool(None)

    def lookup_spoolman(self, sku, info, callback):
        self.logs.verbose(f"Looking up {sku}")

        def on_spool_result(error, spools):
            self.logs.debug(f"SPOOL RESULT -> {spools}")
            callback(error, spools)

        def on_filament_result(error, filaments):
            self.logs.debug(f"FILAMENT RESULT -> {filaments}")
            if error or not filaments:
                self.logs.error(f"filament lookup error for sku {sku}: {json.dumps(error)}")
                callback(f"filaments error for sku {sku}", filaments)
                return
            filament_id = filaments[0]["id"]
            spool_request = SpoolmanRequest(self.webhooks, self.logs, on_spool_result)
            spool_request.fetch("/api/v1/spool", f"filament.id={filament_id}")

        filament_request = SpoolmanRequest(self.webhooks, self.logs, on_filament_result)
        filament_request.fetch("/api/v1/filament", f"article_number={sku}")

    def resolve_spool(self, info, callback):
        uid = nfc_tracking.trackable_uid(info.get("CARD_UID"))
        self.logs.verbose(
            f"Resolving spool: vendor->{info.get('VENDOR')}, sku->{info.get('SKU')}, "
            f"spool_id->{info.get('SPOOL_ID')}, uid->{uid}, chain->{self.strategy_chain}"
        )
        self._run_strategies(info, uid, list(self.strategy_chain), callback)

    def _run_strategies(self, info, uid, remaining, callback):
        if not remaining:
            self.logs.warn("No resolution strategy matched this tag")
            callback(None)
            return
        strategy = remaining[0]

        def advance():
            self._run_strategies(info, uid, remaining[1:], callback)

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
            callback(spool_id)
        else:
            advance()

    def _resolve_by_uid(self, uid, callback, advance):
        if not uid:
            advance()
            return

        def on_spools(error, spools):
            spool = nfc_tracking.match_spool_by_nfc_id(spools or [], uid)
            if spool:
                self.logs.debug(f"Resolved by UID {uid}: spool {spool.get('id')}")
                callback(spool)
            else:
                advance()

        SpoolmanRequest(self.webhooks, self.logs, on_spools).fetch("/api/v1/spool", "")

    def _resolve_by_sku(self, info, uid, callback, advance):
        sku = info.get("SKU")
        if not sku:
            advance()
            return

        def on_lookup(error, spools):
            if error or not spools:
                self.logs.error(f"Cannot find spools for sku: {sku}")
                advance()
                return
            self.logs.debug(f"Resolved by sku->{sku}: spool {spools[0].get('id')}")
            self._auto_register_uid(spools[0], uid)
            callback(spools[0])

        self.lookup_spoolman(sku, None, on_lookup)

    def _auto_register_uid(self, spool, uid):
        if not (self.auto_register and uid) or nfc_tracking.spool_has_nfc_id(spool, uid):
            return
        spool_id = spool.get("id")
        if spool_id:
            self.bind_uid(spool_id, uid)

    def define_nfc_id_field(self):
        def on_done(error, payload):
            self.logs.debug(f"nfc_id field define result: error={error}")

        SpoolmanRequest(self.webhooks, self.logs, on_done).fetch(
            f"/api/v1/field/spool/{nfc_tracking.NFC_ID_FIELD}", "",
            method="POST", body=nfc_tracking.nfc_id_field_definition())

    def bind_uid(self, spool_id, uid, on_done=None):
        if not uid:
            self.logs.warn("Refusing to bind an empty or non-stable UID")
            if on_done:
                on_done(False)
            return

        def on_spools(error, spools):
            self._bind_unless_assigned(spool_id, uid, spools or [], on_done)

        SpoolmanRequest(self.webhooks, self.logs, on_spools).fetch("/api/v1/spool", "")

    def _bind_unless_assigned(self, spool_id, uid, spools, on_done):
        owner = nfc_tracking.match_spool_by_nfc_id(spools, uid)
        if owner is not None:
            owner_id = owner.get("id")
            self.logs.log(f"UID {uid} already assigned to spool {owner_id}, not re-binding")
            if on_done:
                on_done(owner_id == spool_id)
            return
        target = nfc_tracking.find_spool_by_id(spools, spool_id) or {}
        self._patch_nfc_id(spool_id, uid, target, on_done)

    def _patch_nfc_id(self, spool_id, uid, spool, on_done):
        extra = nfc_tracking.merged_extra_with_nfc_id(spool, uid)

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
        self._max_retries = 10

    def fetch(self, path, query, method="GET", body=None):
        try:
            self._dispatch_fetch(path, query, method, body)
        except gcode.CommandError as command_err:
            if "not registered" not in str(command_err):
                raise
            self._schedule_retry(path, query, method, body)
        except Exception:
            logging.exception("fetch error")

    def _arm_cleanup_timer(self):
        def _cleanup(eventtime):
            SpoolmanRequest._pending.pop(self.request_id, None)
            return self.reactor.NEVER

        self.reactor.register_timer(_cleanup, self.reactor.monotonic() + PENDING_CLEANUP_SECONDS)

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
        if self._retry_count > self._max_retries:
            self.logs.error(
                f"spoolman_proxy unavailable after {self._retry_count} attempts "
                f"id={self.request_id}"
            )
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
