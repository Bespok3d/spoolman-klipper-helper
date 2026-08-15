"""The [spoolman_helper] section the daemon writes onto the printer.

A placeholder the manifest does not declare is never substituted, so the printer would read the
literal $NAME as the option's value. This is the guard for that.
"""
import json
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
KLIPPER_TEMPLATE = PLUGIN_DIR / "files" / "cfg" / "klipper" / "spoolman.cfg.tmpl"
MANIFEST = PLUGIN_DIR / "manifest.json"


def declared_option_keys() -> set:
    manifest = json.loads(MANIFEST.read_text())
    return {option["key"] for option in manifest.get("config", [])}


def test_every_placeholder_in_the_section_is_an_option_the_manifest_declares() -> None:
    placeholders = set(re.findall(r"\$([A-Z0-9_]+)", KLIPPER_TEMPLATE.read_text()))
    assert placeholders <= declared_option_keys()


def test_making_a_spool_out_of_a_tag_reaches_the_printer_as_a_written_option() -> None:
    assert "register_from_tag: $SPOOLMAN_REGISTER_FROM_TAG" in KLIPPER_TEMPLATE.read_text()
    assert "SPOOLMAN_REGISTER_FROM_TAG" in declared_option_keys()
