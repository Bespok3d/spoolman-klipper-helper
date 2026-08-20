"""Invitro suite: drives a REAL printer and verifies the installed spoolman plugin end to end.

The printer address arrives through B3D_HIL_HOST; without it every test skips. Run the suite
with scripts/invitro.sh: the read-only tier runs by default, and tests marked `mutating`
(spool picks, helper config edits, Klipper restarts) run only with B3D_INVITRO_MUTATE=1.
Every mutating test restores what it changed before it ends.
"""
import os
import sys
import types
from pathlib import Path

import lane_state
import printer_wire
import pytest

_gcode_stub = types.ModuleType("gcode")
_gcode_stub.CommandError = type("CommandError", (Exception,), {})
sys.modules.setdefault("gcode", _gcode_stub)

EXTRAS = Path(__file__).resolve().parent.parent / "files" / "klipper" / "klippy" / "extras"
sys.path.insert(0, str(EXTRAS / "spoolman"))
sys.path.insert(0, str(EXTRAS))

BUSY_PRINT_STATES = ("printing", "paused")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "mutating: changes printer state; run via scripts/invitro.sh with B3D_INVITRO_MUTATE=1",
    )


@pytest.fixture(scope="session")
def printer():
    printer_address = os.environ.get("B3D_HIL_HOST", "")
    if not printer_address:
        pytest.skip("set B3D_HIL_HOST to the printer address; this suite drives a real printer")
    return printer_wire.PrinterWire(printer_address)


@pytest.fixture(scope="session")
def spoolman_records(printer):
    return printer_wire.SpoolmanRecords(printer.spoolman_server_url())


@pytest.fixture(scope="session")
def helper_options(printer):
    config_text = printer.config_file_text(printer_wire.HELPER_CONFIG_MOONRAKER_PATH)
    return lane_state.helper_options_on_device(config_text)


@pytest.fixture
def idle_printer(printer):
    if printer.print_state() in BUSY_PRINT_STATES:
        pytest.skip("the printer is printing; mutating tests need an idle machine")
    return printer
