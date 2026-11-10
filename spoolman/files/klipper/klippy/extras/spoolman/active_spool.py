"""The active-spool rule: which Spoolman spool is actually consuming filament right now.

Three rules, recomputed from current state (never edge-triggered): the mounted lane's spool is
active; no lane mounted means no active spool; no spool loaded anywhere also means no active
spool. The spool is the LANE's, never a tool's: Snapmaker's virtual tooling can feed any logical
tool from any lane and several tools from one, so what a tool number holds says nothing about a
lane until the tool-to-extruder map is read. The caller keeps one spool per lane, filled through
that map.
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


def resolve_active_spool(current_lane, spool_by_lane):
    physical_extruder = physical_extruder_from_lane(current_lane)
    if physical_extruder is None:
        return None
    return coerce_spool_id(spool_by_lane.get(physical_extruder))


def lane_label(extruder_map_table, physical_extruder):
    tools = tools_for_physical_extruder(extruder_map_table, physical_extruder)
    tool_names = ", ".join(f"T{tool_index}" for tool_index in tools)
    return f"E{physical_extruder} ({tool_names})" if tool_names else f"E{physical_extruder}"
