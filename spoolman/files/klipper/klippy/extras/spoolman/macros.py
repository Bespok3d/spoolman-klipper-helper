class Macros:
    def __init__(self, printer, logs):
        self.printer = printer
        self.logs = logs
        self.gcode = self.printer.lookup_object("gcode")
        self.webhooks = self.printer.lookup_object("webhooks")

    def run(self, command, error_msg):
        try:
            self.gcode.run_script_from_command(command)
        except Exception:
            self.logs.error(f"for {command}: {error_msg}")

    def set_spool_id_for_tool(self, tool, spool_id):
        macro = self.printer.lookup_object(f"gcode_macro {tool}", None)
        if macro is None or "spool_id" not in getattr(macro, "variables", {}):
            self.logs.verbose(f"{tool} has no spool_id variable, skipping")
            return
        spool_id = repr(spool_id)
        command = f"SET_GCODE_VARIABLE MACRO={tool} VARIABLE=spool_id VALUE={spool_id}"
        self.logs.verbose(f"updating tool with command {command}")
        self.run(command, f"tool {tool} does not have a spool_id variable")

    # FORCE=1 is required because the firmware refuses to overwrite an "official" (RFID-tagged)
    # extruder otherwise (print_task_config.cmd_SET_PRINT_FILAMENT_CONFIG). An explicit
    # CLEAR_ALL_SPOOLS is the user asking for a full reset, so it wipes every slot including
    # tagged ones.
    def clear_print_task_config(self, channel):
        command = (
            f"SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER={channel} FORCE=1 "
            'VENDOR="NONE" FILAMENT_TYPE="NONE" FILAMENT_SUBTYPE="NONE" '
            "FILAMENT_COLOR_RGBA=FFFFFFFF"
        )
        self.run(command, f"could not clear print task config for extruder {channel}")

    def get_spool_id_for_tool(self, tool_id):
        try:
            macro = self.printer.lookup_object(f"gcode_macro T{tool_id}")
            self.logs.verbose(f"macro for T{tool_id} variables: {macro.variables}")
            spool_id = macro.variables.get('spool_id', None)
            if spool_id is None:
                self.logs.verbose(f"T{tool_id} macro has no spool_id")
            return spool_id
        except Exception:
            self.logs.warn(f"T{tool_id} macro not found")
            return None

    def detect_spool(self, channel):
        command = f"FILAMENT_DT_UPDATE CHANNEL={channel}"
        self.run(command, f"did not update channel {channel}")
