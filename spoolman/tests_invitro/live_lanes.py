"""Live lane views: the wire composed with the pure lane views."""
import expected_lane
import lane_state


def lane_spool_id(printer, physical_extruder):
    return lane_state.bound_spool_id(printer.lane_status(physical_extruder))


def lane_fields(printer, physical_extruder):
    return lane_state.firmware_lane_fields(printer.print_task_config(), physical_extruder)


def published_view(printer, physical_extruder):
    return expected_lane.published_write_view(lane_fields(printer, physical_extruder))


def bound_lane_extruders(printer):
    return [
        extruder for extruder in printer.lane_extruders() if lane_spool_id(printer, extruder)
    ]


def official_lane_extruders(printer):
    print_task = printer.print_task_config()
    return [
        extruder
        for extruder in bound_lane_extruders(printer)
        if lane_state.firmware_lane_fields(print_task, extruder)["official"]
    ]


def manual_writable_extruders(printer):
    print_task = printer.print_task_config()

    def plugin_may_write(extruder):
        fields = lane_state.firmware_lane_fields(print_task, extruder)
        return bool(fields["edit"]) and not fields["official"]

    return [extruder for extruder in bound_lane_extruders(printer) if plugin_may_write(extruder)]
