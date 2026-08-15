# ruff: noqa: PLR2004  Tests assert against literal spool ids and list lengths.
"""Which of the user's own spools are offered for a tag that matched nothing, and in what order."""
from spoolman.spool_candidates import MAX_CANDIDATES, candidate_spools

TEAL_TAG = {"VENDOR": "ACME", "MAIN_TYPE": "PLA", "SUB_TYPE": "Silk", "ARGB_COLOR": "1D6C6AFF"}


def spool(spool_id, vendor="ACME", material="PLA", colour="1D6C6A", archived=False):
    return {
        "id": spool_id,
        "archived": archived,
        "filament": {"name": "Silk", "material": material, "color_hex": colour,
                     "vendor": {"name": vendor}},
    }


def spool_ids(candidates):
    return [candidate["id"] for candidate in candidates]


def test_the_closest_spool_is_offered_first():
    exact_match = spool(11)
    same_family_colour = spool(12, colour="156F70")
    material_only = spool(13, vendor="OTHERCO", colour="DC2828")
    offered = candidate_spools(TEAL_TAG, [material_only, same_family_colour, exact_match])
    assert spool_ids(offered) == [11, 12, 13]


def test_a_spool_of_another_material_is_never_offered():
    offered = candidate_spools(TEAL_TAG, [spool(21, material="PETG"), spool(22)])
    assert spool_ids(offered) == [22]


def test_an_archived_spool_is_never_offered():
    offered = candidate_spools(TEAL_TAG, [spool(31, archived=True), spool(32)])
    assert spool_ids(offered) == [32]


def test_the_shortlist_stays_short_enough_to_read():
    offered = candidate_spools(TEAL_TAG, [spool(40 + number) for number in range(9)])
    assert len(offered) == MAX_CANDIDATES


def test_spools_that_match_equally_well_are_offered_in_spool_number_order():
    offered = candidate_spools(TEAL_TAG, [spool(53), spool(51), spool(52)])
    assert spool_ids(offered) == [51, 52, 53]


def test_an_empty_inventory_offers_nothing():
    assert candidate_spools(TEAL_TAG, []) == []
