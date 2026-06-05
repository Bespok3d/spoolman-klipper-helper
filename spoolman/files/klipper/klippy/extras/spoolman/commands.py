# ruff: noqa: N802  Klipper registers g-code handlers by method name; they must be cmd_UPPERCASE.
import json

MAX_TOOLS_COUNT = 32
MAX_TOOLS_MAX_INDEX = MAX_TOOLS_COUNT - 1


class Commands:
    def __init__(self, printer, logs, helper):
        self.printer = printer
        self.gcode = self.printer.lookup_object("gcode")
        self.logs = logs
        self.helper = helper

        self.gcode.register_command(
            "SH_SET_ACTIVE_TOOL", self.cmd_SET_ACTIVE_TOOL,
            desc="Spoolman Helper: Set the current active spool by tool",
        )
        self.gcode.register_command(
            "SH_CLEAR_ACTIVE_SPOOL", self.cmd_CLEAR_ACTIVE_SPOOL,
            desc="Spoolman Helper: Clear the current active spool",
        )
        self.gcode.register_command(
            "SH_CLEAR_ALL_SPOOLS", self.cmd_CLEAR_ALL_SPOOLS,
            desc="Spoolman Helper: Clear all spools associated to tools",
        )
        self.gcode.register_command(
            "SH_DETECT_SPOOLS", self.cmd_DETECT_SPOOLS,
            desc="Spoolman Helper: Detect and configure spools",
        )
        self.gcode.register_command(
            "SH_DUMP_SPOOLS", self.cmd_DUMP_SPOOLS,
            desc="Spoolman Helper: Dump current spool configuration",
        )
        self.gcode.register_command(
            "SH_CONFIG", self.cmd_SH_CONFIG,
            desc="Spoolman Helper: Configure module options at runtime",
        )
        self.gcode.register_command(
            "SH_DEBUG", self.cmd_SH_DEBUG,
            desc="Spoolman Helper: Debug utilities",
        )

    def cmd_SET_ACTIVE_TOOL(self, gcmd):
        tool_id = gcmd.get_int("TOOL", minval=0, maxval=MAX_TOOLS_MAX_INDEX)
        self.logs.verbose(f"SET_ACTIVE_TOOL: T{tool_id}")
        self.helper.set_active_tool(tool_id)

    def cmd_CLEAR_ACTIVE_SPOOL(self, gcmd):
        self.logs.log("Active Spool Cleared")
        self.helper.spoolman.set_active_spool(None)

    def cmd_CLEAR_ALL_SPOOLS(self, gcmd):
        self.helper.clear_spool_ids()
        for tool in range(MAX_TOOLS_COUNT):
            self.logs.verbose(f"Clearing spool config for T{tool}")
            self.helper.macros.set_spool_id_for_tool(f"T{tool}", None)

    def cmd_DETECT_SPOOLS(self, gcmd):
        self.helper.detect_spools()

    def cmd_DUMP_SPOOLS(self, gcmd):
        raw = gcmd.get("RAW", None)
        self.helper.dump(raw)

    def cmd_SH_CONFIG(self, gcmd):
        mode = gcmd.get("MODE", None)
        log_level = gcmd.get("LOGS", None)

        if mode is not None:
            mode = mode.lower()
            if mode not in ("auto", "manual"):
                self.logs.error("MODE must be: auto or manual")
                return
            self.helper.mode = mode

        if log_level is not None:
            log_level = log_level.lower()
            if log_level not in ("error", "info", "warn", "verbose", "debug"):
                self.logs.error("LOGS must be: error, info, warn, verbose, debug")
                return
            self.helper.logging = log_level

        self.logs.log(f"Config: mode->{self.helper.mode}, log level->{self.helper.logging}")

    def cmd_SH_DEBUG(self, gcmd):
        sku = gcmd.get("SKU", None)
        self.logs.log(f"Config: mode->{self.helper.mode}, log level->{self.helper.logging}")
        if sku:
            def on_spool_result(error, spools):
                if error:
                    self.logs.error(f"on_spool_result {json.dumps(error)}")
                    return
                spool = spools[0] if spools else None
                self.logs.log(f"cmd_SH_DEBUG found spool: {spool}")
            self.helper.spoolman.lookup_spoolman(sku, on_spool_result)
