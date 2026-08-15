# ruff: noqa: PLR2004  Tests assert against literal spool ids, diameters and densities.
"""Turning a tag Spoolman has nothing for into a spool of their own.

The payload rules are asserted on the pure leaf; the order of the calls and what is never sent are
asserted through a fake Spoolman that answers the way the Moonraker proxy does.
"""
from klipper_fakes import (
    FakePrinter,
    RecordingLogs,
    RecordingWebhooks,
    drain_timers,
    helper_options,
)
from spoolman.spoolman import Spoolman
from spoolman.tag_registration import (
    card_details_payload,
    dimensions_of_material,
    filament_payload,
    matching_filament,
    vendor_id_named,
)
from spoolman.unmatched_tag import (
    REGISTER_DISABLED,
    REGISTER_REFUSED,
    REGISTER_UNMEASURED_MATERIAL,
    REGISTER_UNREACHABLE,
)
from test_spool_resolution import FakeWebRequest, SpoolmanServerWebhooks

TAG = {
    "VENDOR": "ELEGOO", "MAIN_TYPE": "PLA", "SUB_TYPE": "Matte",
    "ARGB_COLOR": "1D6C6AFF", "SKU": "EL-PLA-M-TEAL", "CARD_UID": [0x04, 0x11, 0x22, 0x33],
}


def filament(**fields):
    record = {"id": 7, "material": "PLA", "name": "Matte", "color_hex": "1D6C6A",
              "article_number": "", "diameter": 1.75, "density": 1.24,
              "vendor": {"id": 3, "name": "ELEGOO"}}
    record.update(fields)
    return record


def test_a_filament_they_already_own_is_used_instead_of_a_second_copy_of_it():
    theirs = filament(id=12, article_number="EL-PLA-M-TEAL")
    assert matching_filament(TAG, [filament(id=9, material="PETG"), theirs]) is theirs


def test_a_filament_that_merely_shares_a_blank_article_number_is_not_the_tag():
    other_material = filament(id=9, material="PETG", article_number="")
    assert matching_filament({"MAIN_TYPE": "", "SKU": ""}, [other_material]) is None


def test_the_same_filament_under_another_article_number_is_still_recognised():
    theirs = filament(id=15, article_number="OTHER-CODE")
    assert matching_filament(TAG, [theirs]) is theirs


def test_a_different_colour_of_the_same_filament_is_not_the_same_filament():
    assert matching_filament(TAG, [filament(color_hex="FF0000", article_number="")]) is None


def test_the_diameter_and_density_come_from_their_own_filaments_of_that_material():
    inventory = [filament(diameter=1.75, density=1.24), filament(diameter=1.75, density=1.24),
                 filament(diameter=2.85, density=1.30), filament(material="PETG", density=1.27)]
    assert dimensions_of_material(TAG, inventory) == {"diameter": 1.75, "density": 1.24}


def test_a_material_they_have_never_bought_gives_nothing_to_copy():
    assert dimensions_of_material(TAG, [filament(material="PETG")]) is None
    assert dimensions_of_material(TAG, [filament(diameter=0, density=0)]) is None


def test_a_vendor_they_already_have_is_reused_and_an_unnamed_one_is_never_looked_up():
    assert vendor_id_named("elegoo", [filament()]) == 3
    assert vendor_id_named("", [filament()]) is None
    assert vendor_id_named("Bambu", [filament()]) is None


def test_only_what_the_card_carries_is_written_to_a_filament():
    payload = filament_payload(TAG, 3, {"diameter": 1.75, "density": 1.24})
    assert payload == {
        "material": "PLA", "name": "Matte", "color_hex": "1D6C6A",
        "article_number": "EL-PLA-M-TEAL", "diameter": 1.75, "density": 1.24, "vendor_id": 3,
    }


def test_a_field_the_card_left_blank_is_left_out_rather_than_blanked_in_spoolman():
    assert card_details_payload({"MAIN_TYPE": "PLA", "SUB_TYPE": "", "SKU": ""}) == {
        "material": "PLA"
    }


class SpoolmanCreatesWebhooks(SpoolmanServerWebhooks):
    """A Spoolman that answers reads from a fixture and hands every write a new record id."""

    def __init__(self, printer, spools_by_request, refuses=()):
        super().__init__(printer, spools_by_request)
        self.written = []
        self.refuses = refuses

    def call_remote_method(self, method, **params):
        if params.get("request_method", "GET") == "GET":
            super().call_remote_method(method, **params)
            return
        RecordingWebhooks.call_remote_method(self, method, **params)
        self.written.append((params["request_method"], params["path"], params["body"]))
        self.endpoints[params["cb_endpoint"]](
            FakeWebRequest(params["cb_endpoint"], self._created(params["path"]))
        )

    def _created(self, path):
        return {} if path in self.refuses else {"id": 900 + len(self.written)}


THEIR_INVENTORY = {
    ("/api/v1/filament", ""): [filament(id=12, material="PLA", name="Basic",
                                        color_hex="FF0000", article_number="EL-PLA-RED")],
    ("/api/v1/spool", ""): [],
}


def register_tag(inventory=None, logs=None, refuses=(), asked_by_hand=False, **written_options):
    printer = FakePrinter()
    webhooks = SpoolmanCreatesWebhooks(printer, inventory or THEIR_INVENTORY, refuses)
    printer.objects["webhooks"] = webhooks
    spoolman = Spoolman(printer, logs or RecordingLogs(), helper_options(**written_options))
    outcomes = []
    spoolman.register_tag_as_spool(
        TAG, lambda spool_id, problem, new_spool=None: outcomes.append((spool_id, problem)),
        asked_by_hand=asked_by_hand,
    )
    return outcomes, webhooks


def created_paths(webhooks):
    return [path for method, path, _ in webhooks.written if method == "POST"]


def test_a_tag_nothing_matched_becomes_a_spool_bound_to_its_own_card():
    outcomes, webhooks = register_tag()
    assert created_paths(webhooks) == ["/api/v1/filament", "/api/v1/spool"]
    assert outcomes == [(902, None)]
    assert webhooks.written[-1][:2] == ("PATCH", "/api/v1/spool/902"), "the card is bound"


def test_the_new_filament_hangs_off_the_vendor_they_already_have():
    _, webhooks = register_tag()
    new_filament = [body for _, path, body in webhooks.written if path == "/api/v1/filament"][0]
    assert new_filament["vendor_id"] == 3
    assert new_filament["diameter"] == 1.75, "copied from their own PLA, never invented"
    assert "weight" not in new_filament and "spool_weight" not in new_filament


def test_a_vendor_they_do_not_have_is_created_before_the_filament():
    unknown_vendor = {
        ("/api/v1/filament", ""): [filament(vendor={"id": 3, "name": "Bambu"})],
        ("/api/v1/spool", ""): [],
    }
    _, webhooks = register_tag(inventory=unknown_vendor)
    assert created_paths(webhooks) == ["/api/v1/vendor", "/api/v1/filament", "/api/v1/spool"]


def test_a_tag_that_is_already_one_of_their_filaments_only_gets_a_spool():
    theirs = {
        ("/api/v1/filament", ""): [filament(id=12, article_number="EL-PLA-M-TEAL")],
        ("/api/v1/spool", ""): [],
    }
    outcomes, webhooks = register_tag(inventory=theirs)
    assert [(path, body) for method, path, body in webhooks.written if method == "POST"] == [
        ("/api/v1/spool", {"filament_id": 12}),
    ]
    assert outcomes == [(901, None)]


def test_a_material_they_have_never_bought_creates_nothing_and_says_what_is_missing():
    no_pla = {("/api/v1/filament", ""): [filament(material="PETG", article_number="")],
              ("/api/v1/spool", ""): []}
    outcomes, webhooks = register_tag(inventory=no_pla)
    assert webhooks.written == []
    assert outcomes == [(None, REGISTER_UNMEASURED_MATERIAL)]


def test_nothing_is_created_when_spoolman_did_not_answer():
    printer = FakePrinter()
    printer.objects["webhooks"] = RecordingWebhooks(printer)
    spoolman = Spoolman(printer, RecordingLogs())
    outcomes = []
    spoolman.register_tag_as_spool(
        TAG, lambda spool_id, problem, new_spool=None: outcomes.append(problem))
    assert outcomes == [], "nothing is decided while the answer is still outstanding"
    drain_timers(printer.reactor)
    assert outcomes == [REGISTER_UNREACHABLE], "giving up creates nothing and says why"
    assert [call for call in printer.objects["webhooks"].remote_calls
            if call[1].get("request_method", "GET") != "GET"] == []


def test_a_spoolman_that_refuses_the_spool_is_reported_as_a_refusal():
    outcomes, _ = register_tag(refuses=("/api/v1/spool",))
    assert outcomes == [(None, REGISTER_REFUSED)]


def test_the_config_option_turns_creating_and_updating_off():
    outcomes, webhooks = register_tag(register_from_tag=False)
    assert outcomes == [(None, REGISTER_DISABLED)]
    assert webhooks.written == [] and webhooks.requested == []

    printer = FakePrinter()
    printer.objects["webhooks"] = SpoolmanCreatesWebhooks(printer, THEIR_INVENTORY)
    off = Spoolman(printer, RecordingLogs(), helper_options(register_from_tag=False))
    applied = []
    off.apply_tag_to_spool(TAG, 104, applied.append)
    assert applied == [REGISTER_DISABLED]


def test_a_spool_asked_for_by_hand_is_created_with_the_config_option_off():
    outcomes, webhooks = register_tag(register_from_tag=False, asked_by_hand=True)
    assert outcomes == [(902, None)]
    assert "/api/v1/spool" in created_paths(webhooks)


def apply_tag(spools, logs=None):
    printer = FakePrinter()
    webhooks = SpoolmanCreatesWebhooks(printer, {("/api/v1/spool", ""): spools})
    printer.objects["webhooks"] = webhooks
    spoolman = Spoolman(printer, logs or RecordingLogs())
    applied = []
    spoolman.apply_tag_to_spool(TAG, 104, applied.append)
    return applied, webhooks


def test_a_tag_can_be_written_onto_a_spool_they_picked():
    applied, webhooks = apply_tag([{"id": 104, "filament": {"id": 12}}])
    assert applied == [None]
    assert webhooks.written[0] == ("PATCH", "/api/v1/filament/12", {
        "material": "PLA", "name": "Matte", "color_hex": "1D6C6A",
        "article_number": "EL-PLA-M-TEAL",
    })
    assert webhooks.written[1][1] == "/api/v1/spool/104", "the card is bound to their pick"


def test_writing_onto_a_spool_that_is_not_theirs_changes_nothing():
    applied, webhooks = apply_tag([{"id": 55, "filament": {"id": 12}}])
    assert applied == [REGISTER_REFUSED]
    assert webhooks.written == []


def test_writing_a_tag_when_spoolman_is_silent_changes_nothing():
    printer = FakePrinter()
    printer.objects["webhooks"] = RecordingWebhooks(printer)
    spoolman = Spoolman(printer, RecordingLogs())
    applied = []
    spoolman.apply_tag_to_spool(TAG, 104, applied.append)
    assert applied == []
    assert [call for call in printer.objects["webhooks"].remote_calls
            if call[1].get("request_method", "GET") != "GET"] == []
