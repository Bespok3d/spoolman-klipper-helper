# ruff: noqa: PLR2004  Tests assert against literal spool ids.
"""The active-spool rule, ported device-verified from the retired Moonraker bridge."""
from active_spool import (
    coerce_spool_id,
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
    assert resolve_active_spool("E2", {2: 104}, NATIVE_MAP) == 104


def test_no_lane_mounted_resolves_none():
    assert resolve_active_spool(None, {2: 104}, NATIVE_MAP) is None


def test_mounted_lane_with_no_spool_anywhere_resolves_none():
    assert resolve_active_spool("E2", {0: None, 1: None, 2: None, 3: None}, NATIVE_MAP) is None


def test_home_tool_wins_over_a_borrowed_tools_stale_spool():
    # Snapmaker virtual tooling (map_table=[[0,2],[1,3]]): T1 borrowed onto E3 carries its OWN
    # native channel's stale spool 24; E3's home tool T3 resolved 55. Home wins outright.
    remapped = [2, 3, 2, 3]
    assert resolve_active_spool("E3", {1: 24, 3: 55}, remapped) == 55


def test_borrowers_resolve_only_when_home_is_empty_and_unanimous():
    remapped = [3, 3, 2, 3]
    assert resolve_active_spool("E3", {0: 80, 1: 80, 2: 57, 3: None}, remapped) == 80


def test_disagreeing_borrowers_resolve_to_unknown_not_a_guess():
    remapped = [3, 3, 2, 3]
    assert resolve_active_spool("E3", {0: 10, 1: 20, 2: 57, 3: None}, remapped) is None
