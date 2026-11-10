"""The active-spool rule: which Spoolman spool is actually consuming filament right now.

Three rules, recomputed from current state (never edge-triggered): the mounted lane's resolved
spool is active; no lane mounted means no active spool; no spool loaded anywhere also means no
active spool. Ground truth for a physical extruder is its OWN native tool index (tool N's home
extruder is N, how DETECT_SPOOLS populates it before any print-specific remap). Snapmaker's
virtual tooling can route OTHER logical tools onto an already-used extruder, but a borrowed
tool's spool_id belongs to ITS OWN native channel and is stale noise here: the home tool always
wins when it has a resolved spool, and borrowed claimants are only considered when the home tool
is empty AND they agree on a single distinct value (a genuine tie is unknown, never a guess).
"""


def coerce_spool_id(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None


# afc-lite's static lane->extruder convention (AFC_lane E{n} -> physical extruder n), the same
# one the afc helper module uses to look up `AFC_lane E{channel}`.
def physical_extruder_from_lane(lane):
    if not isinstance(lane, str) or not lane.startswith("E"):
        return None
    try:
        return int(lane[1:])
    except ValueError:
        return None


def tools_for_physical_extruder(extruder_map_table, physical_extruder):
    return [
        tool_index for tool_index, mapped in enumerate(extruder_map_table)
        if mapped == physical_extruder
    ]


def physical_extruder_for_tool(extruder_map_table, tool_index):
    mapped = (
        extruder_map_table[tool_index]
        if 0 <= tool_index < len(extruder_map_table) else None
    )
    return mapped if isinstance(mapped, int) else None


def resolve_active_spool(current_lane, spool_by_tool, extruder_map_table):
    physical_extruder = physical_extruder_from_lane(current_lane)
    if physical_extruder is None:
        return None
    home_spool = coerce_spool_id(spool_by_tool.get(physical_extruder))
    if home_spool is not None:
        return home_spool
    claimants = tools_for_physical_extruder(extruder_map_table, physical_extruder)
    resolved_spools = {coerce_spool_id(spool_by_tool.get(tool_index)) for tool_index in claimants}
    resolved_spools.discard(None)
    return resolved_spools.pop() if len(resolved_spools) == 1 else None
