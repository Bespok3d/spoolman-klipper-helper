"""Fakes for the Klipper host objects a test needs to drive the REAL plugin code.

Every test that exercises the real `Spoolman` needs the same four stand-ins (a reactor whose
timers a test drains by hand, a webhooks that records the Moonraker calls, a printer that hands
those out, and a log sink). They lived in one test file and were about to be copied into a
third, so they live here once.
"""


class FakeReactor:
    NEVER = 9e99

    def __init__(self):
        self.timers = []

    def monotonic(self):
        return 0.0

    def register_timer(self, callback, when):
        self.timers.append((callback, when))


class RecordingGcode:
    def __init__(self):
        self.commands = {}
        self.responses = []

    def register_command(self, name, handler, desc=""):
        self.commands[name] = handler

    def respond_info(self, message):
        self.responses.append(message)


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


def drain_timers(reactor):
    while reactor.timers:
        callback, _when = reactor.timers.pop(0)
        callback(0.0)
