"""Regression tests for the g-code command layer.

SH_DEBUG SKU=... was broken from the repo's first commit: `lookup_spoolman` had grown a dead
`info` parameter while its one command-layer caller kept the original two-arg call, so the
command raised TypeError before any request went out. Nothing exercised the command layer, so
141 tests stayed green over it. These tests drive the REAL Spoolman method through the REAL
command handler, so an arity drift between the two files fails here.
"""
import types

import gcode
from klipper_fakes import FakePrinter, RecordingLogs, RecordingWebhooks, drain_timers
from spoolman.commands import Commands
from spoolman.spoolman import Spoolman


class FakeGcmd:
    def __init__(self, params):
        self.params = params

    def get(self, key, default=None):
        return self.params.get(key, default)

    def get_int(self, key, default=None, minval=None, maxval=None):
        return self.params.get(key, default)


def _command_stack():
    printer = FakePrinter()
    logs = RecordingLogs()
    helper = types.SimpleNamespace(
        mode="auto", logging="info", spoolman=Spoolman(printer, logs)
    )
    return Commands(printer, logs, helper), printer


def test_sh_debug_sku_lookup_reaches_the_spoolman_proxy():
    commands, printer = _command_stack()
    commands.cmd_SH_DEBUG(FakeGcmd({"SKU": "900002"}))
    remote_calls = printer.objects["webhooks"].remote_calls
    assert remote_calls, "SH_DEBUG SKU=... must issue a spoolman_proxy request, not TypeError"
    method, params = remote_calls[0]
    assert method == "spoolman_proxy"
    assert params["path"] == "/api/v1/filament"
    assert params["query"] == "article_number=900002"


def test_sh_debug_without_sku_only_reports_config():
    commands, printer = _command_stack()
    commands.cmd_SH_DEBUG(FakeGcmd({}))
    assert printer.objects["webhooks"].remote_calls == []


class NotYetConnectedWebhooks(RecordingWebhooks):
    """Moonraker right after a Klipper restart: remote methods not registered yet."""

    def __init__(self, printer, failures):
        super().__init__(printer)
        self.failures = failures

    def call_remote_method(self, method, **params):
        if self.failures > 0:
            self.failures -= 1
            raise gcode.CommandError(f"Remote method '{method}' not registered")
        super().call_remote_method(method, **params)


def test_set_active_spool_retries_until_moonraker_registers():
    # The ground-truth push at klippy-ready must not be lost to the Moonraker reconnect window.
    printer = FakePrinter()
    printer.objects["webhooks"] = NotYetConnectedWebhooks(printer, failures=2)
    spoolman = Spoolman(printer, RecordingLogs())
    spoolman.set_active_spool(42)
    drain_timers(printer.reactor)
    assert printer.objects["webhooks"].remote_calls == [
        ("spoolman_set_active_spool", {"spool_id": 42})
    ]


def test_a_superseded_active_spool_retry_is_dropped():
    printer = FakePrinter()
    printer.objects["webhooks"] = NotYetConnectedWebhooks(printer, failures=1)
    spoolman = Spoolman(printer, RecordingLogs())
    spoolman.set_active_spool(42)   # fails once, schedules a retry
    printer.objects["webhooks"].failures = 0
    spoolman.set_active_spool(55)   # newer set supersedes the pending retry
    drain_timers(printer.reactor)
    calls = printer.objects["webhooks"].remote_calls
    assert calls == [("spoolman_set_active_spool", {"spool_id": 55})]
