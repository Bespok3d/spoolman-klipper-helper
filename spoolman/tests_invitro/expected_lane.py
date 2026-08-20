"""What a lane SHOULD look like, computed by the same composer the plugin ships.

Importing the plugin package is safe here: this module is imported by test modules only,
after conftest has put the shipped extras on sys.path. Never import it from conftest.
"""
from spoolman.print_task_writer import (
    DEFAULT_SUBTYPE_SOURCES,
    filament_config_args_from_spool,
    normalize_color_rgba,
    slicer_filament_description,
)

WRITE_ARG_BY_VIEW_NAME = {
    "vendor": "VENDOR",
    "material": "FILAMENT_TYPE",
    "sub_type": "FILAMENT_SUBTYPE",
    "color_rgba": "FILAMENT_COLOR_RGBA",
}


def active_subtype_sources(device_options):
    return device_options.subtype_sources or tuple(DEFAULT_SUBTYPE_SOURCES)


def expected_card_name(spool_record, device_options):
    return slicer_filament_description(spool_record, active_subtype_sources(device_options))


def expected_write_view(spool_record, physical_extruder, device_options):
    config_args = filament_config_args_from_spool(
        spool_record, physical_extruder, active_subtype_sources(device_options)
    )
    return {
        view_name: config_args.get(arg_name, "")
        for view_name, arg_name in WRITE_ARG_BY_VIEW_NAME.items()
    }


def published_write_view(firmware_fields):
    return {
        "vendor": firmware_fields["vendor"] or "",
        "material": firmware_fields["material"] or "",
        "sub_type": firmware_fields["sub_type"] or "",
        "color_rgba": normalize_color_rgba(firmware_fields["color_rgba"] or ""),
    }
