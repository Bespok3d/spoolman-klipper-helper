"""Mirror a resolved Spoolman spool id onto the matching AFC lane.

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
