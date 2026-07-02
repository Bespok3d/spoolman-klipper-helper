"""Mirror a resolved Spoolman spool's color/material into the U1 print task (screen + AFC).

Both the touchscreen and the AFC panel read color/material from the firmware print_task_config
(by physical extruder). A manual widget pick has no tag to feed them, so the spool's identity is
written back with the firmware's own SET_PRINT_FILAMENT_CONFIG, and the vendor+name display
label with SET_LANE_FILAMENT_NAME. RFID-tagged (official) channels are never written: the tag is
the source of truth there, and the firmware raises on such a write (touchscreen shows a "System
Anomaly" popup). Running in-process means the official check and the already-matches check read
the LIVE print_task_config object, so the stale-subscription race the old Moonraker bridge had
does not exist here.
"""
import base64

RGB_HEX_LENGTH = 6
RGBA_HEX_LENGTH = 8
OPAQUE_ALPHA = "FF"
GCODE_UNSAFE_CHARS = ('"', "'", "\n", "\r", ";")
CONFIG_FIELD_BY_ARG = {
    "VENDOR": "filament_vendor",
    "FILAMENT_TYPE": "filament_type",
    "FILAMENT_SUBTYPE": "filament_sub_type",
    "FILAMENT_COLOR_RGBA": "filament_color_rgba",
}
QUOTED_ARGS = ("VENDOR", "FILAMENT_TYPE", "FILAMENT_SUBTYPE")
# Spoolman has no sub-type concept, but the firmware's SET_PRINT_FILAMENT_CONFIG rejects a
# FILAMENT_TYPE that arrives without both VENDOR and FILAMENT_SUBTYPE ("incomplete parameters").
NO_SUBTYPE = ""
# The firmware's empty/cleared print_task_config slot (DEFAULT_PRINT_TASK_CONFIG): vendor, type
# and sub-type read "NONE", colour reads opaque white.
EMPTY_FIELD = "NONE"
EMPTY_COLOR_RGBA = "FFFFFFFF"


def normalize_color_rgba(color_hex):
    cleaned = (color_hex or "").strip().upper()
    if len(cleaned) == RGBA_HEX_LENGTH:
        return cleaned
    if len(cleaned) == RGB_HEX_LENGTH:
        return cleaned + OPAQUE_ALPHA
    return ""


def filament_config_args_from_spool(spool, physical_extruder):
    filament = (spool or {}).get("filament") or {}
    vendor = (filament.get("vendor") or {}).get("name") or ""
    material = filament.get("material") or ""
    color = normalize_color_rgba(filament.get("color_hex") or "")
    args = {"CONFIG_EXTRUDER": str(physical_extruder)}
    if material:
        args["VENDOR"] = vendor
        args["FILAMENT_TYPE"] = material
        args["FILAMENT_SUBTYPE"] = NO_SUBTYPE
    if color:
        args["FILAMENT_COLOR_RGBA"] = color
    return args


def filament_config_clear_args(physical_extruder):
    return {
        "CONFIG_EXTRUDER": str(physical_extruder),
        "VENDOR": EMPTY_FIELD,
        "FILAMENT_TYPE": EMPTY_FIELD,
        "FILAMENT_SUBTYPE": EMPTY_FIELD,
        "FILAMENT_COLOR_RGBA": EMPTY_COLOR_RGBA,
    }


def has_filament_fields(args):
    return any(arg in args for arg in CONFIG_FIELD_BY_ARG)


def _strip_unsafe(value):
    return "".join(char for char in value if char not in GCODE_UNSAFE_CHARS)


def _format_arg(key, value):
    if key in QUOTED_ARGS:
        return f'{key}="{_strip_unsafe(value)}"'
    return f"{key}={value}"


def set_print_filament_config_gcode(args):
    pairs = " ".join(_format_arg(key, value) for key, value in args.items())
    return f"SET_PRINT_FILAMENT_CONFIG {pairs}"


# A vendor+name display label ("ZIRO Silk Gold") the AFC panel cannot compose itself: it shows
# the Spoolman filament.name alone, and only when the frontend can resolve the spool.
def composed_filament_name(spool):
    filament = (spool or {}).get("filament") or {}
    vendor = (filament.get("vendor") or {}).get("name") or ""
    name = filament.get("name") or ""
    return " ".join(part for part in (vendor, name) if part)


# Base64 so a name with spaces survives Klipper's whitespace-splitting gcode parser.
def set_lane_filament_name_gcode(physical_extruder, name):
    encoded = base64.b64encode(name.encode("utf-8")).decode("ascii")
    return f"SET_LANE_FILAMENT_NAME EXTRUDER={physical_extruder} NAME_B64={encoded}"


def _value_at(values, index):
    return values[index] if 0 <= index < len(values) else None


def config_already_matches(print_task_config, physical_extruder, desired_args):
    checks = [
        _value_at(print_task_config.get(field, []), physical_extruder) == desired_args[arg]
        for arg, field in CONFIG_FIELD_BY_ARG.items()
        if arg in desired_args
    ]
    return bool(checks) and all(checks)


def channel_is_official(filament_official, physical_extruder):
    return bool(_value_at(filament_official or [], physical_extruder))


class PrintTaskWriter:
    def __init__(self, printer, logs, macros):
        self.printer = printer
        self.logs = logs
        self.macros = macros

    def _live_task_config(self):
        task = self.printer.lookup_object("print_task_config", None)
        config = getattr(task, "print_task_config", None)
        return config if isinstance(config, dict) else {}

    def _should_write(self, physical_extruder, desired_args):
        config = self._live_task_config()
        if config_already_matches(config, physical_extruder, desired_args):
            return False
        return not channel_is_official(config.get("filament_official"), physical_extruder)

    def apply_spool(self, physical_extruder, spool):
        desired = filament_config_args_from_spool(spool, physical_extruder)
        if not has_filament_fields(desired):
            return
        if self._should_write(physical_extruder, desired):
            self.macros.run(
                set_print_filament_config_gcode(desired),
                f"could not write filament config for extruder {physical_extruder}",
            )
        self._apply_name(physical_extruder, spool)

    def clear_extruder(self, physical_extruder):
        desired = filament_config_clear_args(physical_extruder)
        if not self._should_write(physical_extruder, desired):
            return
        self.macros.run(
            set_print_filament_config_gcode(desired),
            f"could not clear filament config for extruder {physical_extruder}",
        )
        self._push_name(physical_extruder, "")

    # Pushed even when the persisted config already matched, so a re-pick after a restart
    # (which clears the AFC lane's name) re-labels the lane. Also called directly for
    # RFID-resolved lanes: the AFC panel only shows a name a helper pushed, so every resolved
    # lane gets its label, not just manual picks.
    def label_lane(self, physical_extruder, spool):
        name = composed_filament_name(spool)
        if name:
            self._push_name(physical_extruder, name)

    def _apply_name(self, physical_extruder, spool):
        self.label_lane(physical_extruder, spool)

    def _push_name(self, physical_extruder, name):
        self.macros.run(
            set_lane_filament_name_gcode(physical_extruder, name),
            f"could not set lane filament name for extruder {physical_extruder}",
        )
