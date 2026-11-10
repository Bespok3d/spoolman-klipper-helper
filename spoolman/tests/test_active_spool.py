# ruff: noqa: PLR2004  Tests assert against literal spool ids.
"""The active-spool rule, ported device-verified from the retired Moonraker bridge."""
from active_spool import (
    coerce_spool_id,
    lane_label,
    physical_extruder_for_tool,
    physical_extruder_from_lane,
    resolve_active_spool,
    tools_for_physical_extruder,
)

NATIVE_MAP = [0, 1, 2, 3]


def test_coerce_spool_id():
    assert coerce_spool_id(42) == 42
    assert coerce_spool_id("42") == 42
    assert coerce_spool_id(0) is None
    assert coerce_spool_id("") is None
    assert coerce_spool_id(None) is None
    assert coerce_spool_id("nope") is None


def test_physical_extruder_from_lane():
    assert physical_extruder_from_lane("E2") == 2
    assert physical_extruder_from_lane(None) is None
    assert physical_extruder_from_lane("nozzle") is None
    assert physical_extruder_from_lane("Ex") is None


def test_physical_extruder_for_tool():
    assert physical_extruder_for_tool([2, 3, 2, 3], 0) == 2
    assert physical_extruder_for_tool([2, 3], 5) is None
    assert physical_extruder_for_tool([], 0) is None


def test_tools_for_physical_extruder_returns_every_claimant():
    assert tools_for_physical_extruder([2, 3, 2, 3], 2) == [0, 2]
    assert tools_for_physical_extruder(NATIVE_MAP, 1) == [1]
    assert tools_for_physical_extruder(NATIVE_MAP, 7) == []


def test_mounted_lane_resolves_its_own_spool():
    assert resolve_active_spool("E2", {2: 104}) == 104


def test_no_lane_mounted_resolves_none():
    assert resolve_active_spool(None, {2: 104}) is None


def test_mounted_lane_with_no_spool_anywhere_resolves_none():
    assert resolve_active_spool("E2", {0: None, 1: None, 2: None, 3: None}) is None


def test_lane_label_names_every_tool_the_map_points_at_the_lane():
    remapped = [2, 0, None, None, None, 2]
    assert lane_label(remapped, 2) == "E2 (T0, T5)"
    assert lane_label(remapped, 0) == "E0 (T1)"
    assert lane_label(remapped, 3) == "E3"
