from typing import Any


class Logs:
    """User-facing notifications only; this module must never crash the print."""

    # A warning outranks routine information: it is the level a user must still see at the
    # default setting. Anything that is merely routine belongs at verbose, never at warn.
    levels = ["error", "warn", "info", "verbose", "debug"]

    def __init__(self, printer: Any, helper: Any) -> None:
        self.printer = printer
        self.gcode = self.printer.lookup_object("gcode")
        self.prefix = "🧶 SH"
        self.helper = helper

    def should_output(self, request_level: str) -> bool:
        return self.levels.index(request_level) <= self.levels.index(self.helper.logging)

    def format_message(self, level: str, icon: str, message: str) -> str:
        return f"{icon}{self.prefix} [{level}]: {message}"

    def debug(self, message: str) -> None:
        if self.should_output("debug"):
            self.gcode.respond_info(self.format_message("DEBUG", "🔵", message))

    def log(self, message: str) -> None:
        if self.should_output("info"):
            self.gcode.respond_info(self.format_message("INFO", "", message))

    def warn(self, message: str) -> None:
        if self.should_output("warn"):
            self.gcode.respond_info(self.format_message("WARNING", "🟡", message))

    def verbose(self, message: str) -> None:
        if self.should_output("verbose"):
            self.gcode.respond_info(self.format_message("VERBOSE", "🟣", message))

    def error(self, message: str) -> None:
        if self.should_output("error"):
            self.gcode.respond_info(self.format_message("ERROR", "🔴", message))
