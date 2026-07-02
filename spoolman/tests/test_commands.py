"""Regression tests for the g-code command layer.

SH_DEBUG SKU=... was broken from the repo's first commit: `lookup_spoolman` had grown a dead
`info` parameter while its one command-layer caller kept the original two-arg call, so the
command raised TypeError before any request went out. Nothing exercised the command layer, so
141 tests stayed green over it. These tests drive the REAL Spoolman method through the REAL
command handler, so an arity drift between the two files fails here.
"""
import sys
import types

_gcode_stub = types.ModuleType("gcode")
_gcode_stub.CommandError = type("CommandError", (Exception,), {})
sys.modules.setdefault("gcode", _gcode_stub)

from commands import Commands  # noqa: E402  (the gcode stub must exist before this import)
from spoolman.spoolman import Spoolman  # noqa: E402


class RecordingGcode:
    def __init__(self):
        self.commands = {}
        self.responses = []

    def register_command(self, name, handler, desc=""):
        self.commands[name] = handler

    def respond_info(self, message):
        self.responses.append(message)


class FakeReactor:
    NEVER = 9e99

    def __init__(self):
        self.timers = []

    def monotonic(self):
        return 0.0

    def register_timer(self, callback, when):
        self.timers.append((callback, when))


class RecordingWebhooks:
    def __init__(self, printer):
        self.printer = printer
        self.endpoints = {}
        self.remote_calls = []

    def register_endpoint(self, path, handler):
        self.endpoints[path] = handler

    def call_remote_method(self, method, **params):
        self.remote_calls.append((method, params))


class FakePrinter:
    def __init__(self):
        self.reactor = FakeReactor()
        self.objects = {"gcode": RecordingGcode()}
        self.objects["webhooks"] = RecordingWebhooks(self)

    def lookup_object(self, name, default=KeyError):
        if name in self.objects:
            return self.objects[name]
        if default is KeyError:
            raise KeyError(name)
        return default

    def get_reactor(self):
        return self.reactor


class RecordingLogs:
    def __init__(self):
        self.lines = []

    def _record(self, message):
        self.lines.append(message)

    log = warn = error = verbose = debug = _record


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
            raise _gcode_stub.CommandError(f"Remote method '{method}' not registered")
        super().call_remote_method(method, **params)


def _drain_timers(reactor):
    while reactor.timers:
        callback, _when = reactor.timers.pop(0)
        callback(0.0)


def test_set_active_spool_retries_until_moonraker_registers():
    # The ground-truth push at klippy-ready must not be lost to the Moonraker reconnect window.
    printer = FakePrinter()
    printer.objects["webhooks"] = NotYetConnectedWebhooks(printer, failures=2)
    spoolman = Spoolman(printer, RecordingLogs())
    spoolman.set_active_spool(42)
    _drain_timers(printer.reactor)
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
    _drain_timers(printer.reactor)
    calls = printer.objects["webhooks"].remote_calls
    assert calls == [("spoolman_set_active_spool", {"spool_id": 55})]
