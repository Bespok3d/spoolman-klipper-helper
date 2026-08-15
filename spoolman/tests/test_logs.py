"""Regression tests for the Spoolman user-notification logger."""
from logs import Logs


class FakeGcode:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def respond_info(self, message: str) -> None:
        self.messages.append(message)


class FakePrinter:
    def __init__(self, gcode: FakeGcode) -> None:
        self._gcode = gcode

    def lookup_object(self, name: str) -> FakeGcode:
        return self._gcode


class FakeHelper:
    def __init__(self, level: str) -> None:
        self.logging = level


def make_logs(level: str) -> tuple[Logs, FakeGcode]:
    gcode = FakeGcode()
    return Logs(FakePrinter(gcode), FakeHelper(level)), gcode


def test_should_output_respects_configured_level() -> None:
    logs, _ = make_logs("info")
    assert logs.should_output("error")
    assert logs.should_output("info")
    assert not logs.should_output("debug")


def test_a_warning_is_visible_at_the_default_level() -> None:
    logs, gcode = make_logs("info")
    logs.warn("the tag on channel 0 matched nothing")
    assert gcode.messages == ["🟡🧶 SH [WARNING]: the tag on channel 0 matched nothing"]


def test_routine_chatter_stays_quiet_at_the_default_level() -> None:
    logs, gcode = make_logs("info")
    logs.verbose("resolving")
    assert gcode.messages == []


def test_format_message_shape() -> None:
    logs, _ = make_logs("debug")
    assert logs.format_message("INFO", "X", "hi") == "X🧶 SH [INFO]: hi"


def test_error_emits_when_level_allows() -> None:
    logs, gcode = make_logs("error")
    logs.error("boom")
    assert gcode.messages == ["🔴🧶 SH [ERROR]: boom"]


def test_debug_suppressed_at_error_level() -> None:
    logs, gcode = make_logs("error")
    logs.debug("noise")
    assert gcode.messages == []
