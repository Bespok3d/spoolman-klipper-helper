"""All AFC lane object access: mirror resolved spool ids out, read pushed labels back.

The AFC panel resolves a lane's filament NAME from the Spoolman spool found by the lane's
`spool_id`; the lane carries no name field. So when both plugins are installed, the helper hands
afc-lite the id it resolved. A no-op when afc-lite is absent.
"""
from typing import Any


def push_spool_to_afc(printer: Any, channel: int, spool_id: Any, extruders_count: int) -> None:
    if not 0 <= channel < extruders_count:
        return
    if printer.lookup_object("AFC", None) is None:
        return
    lane = printer.lookup_object(f"AFC_lane E{channel}", None)
    if lane is not None:
        lane.spool_id = spool_id


# The filament description ("<brand> <material> <sub-type>") the helper pushed onto the lane via
# SET_LANE_FILAMENT_NAME, read back. Empty when AFC is absent or no name was pushed.
def lane_filament_name(printer: Any, channel: int) -> str:
    lane = printer.lookup_object(f"AFC_lane E{channel}", None)
    name = getattr(lane, "filament_name", "") if lane is not None else ""
    return name.strip() if isinstance(name, str) else ""
