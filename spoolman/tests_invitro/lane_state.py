"""Pure views over the firmware's print-task state and the helper's on-device config.

No I/O here: everything takes already-fetched dicts or text, so the views stay testable
and the wire stays in printer_wire.
"""
from typing import NamedTuple

PRINT_TASK_FIELD_BY_NAME = {
    "vendor": "filament_vendor",
    "material": "filament_type",
    "sub_type": "filament_sub_type",
    "color_rgba": "filament_color_rgba",
    "official": "filament_official",
    "sku": "filament_sku",
    "edit": "filament_edit",
    "exist": "filament_exist",
}


def _field_value(print_task_config, field_name, physical_extruder):
    lane_values = print_task_config.get(field_name) or []
    if physical_extruder >= len(lane_values):
        return None
    return lane_values[physical_extruder]


def firmware_lane_fields(print_task_config, physical_extruder):
    return {
        view_name: _field_value(print_task_config, field_name, physical_extruder)
        for view_name, field_name in PRINT_TASK_FIELD_BY_NAME.items()
    }


def bound_spool_id(lane_status):
    spool_id_text = str(lane_status.get("spool_id", "")).strip()
    if not spool_id_text.isdigit():
        return 0
    return int(spool_id_text)


class HelperOptionsOnDevice(NamedTuple):
    subtype_sources: tuple
    spoolman_overrides_tag: bool


def helper_option(config_text, option_name):
    option_prefix = f"{option_name}:"
    stripped_lines = (line.strip() for line in config_text.splitlines())
    option_values = (
        line.removeprefix(option_prefix).strip()
        for line in stripped_lines
        if line.startswith(option_prefix)
    )
    return next(option_values, "")


def _subtype_sources(sources_text):
    return tuple(source.strip() for source in sources_text.split(",") if source.strip())


def helper_options_on_device(config_text):
    overrides_text = helper_option(config_text, "spoolman_overrides_tag").lower()
    return HelperOptionsOnDevice(
        subtype_sources=_subtype_sources(helper_option(config_text, "subtype_sources")),
        spoolman_overrides_tag=overrides_text == "true",
    )
