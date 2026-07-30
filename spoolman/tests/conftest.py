import sys
import types
from pathlib import Path

# Klipper's own `gcode` module only exists on the printer, and the plugin imports it at module
# level for its CommandError. Stub it here so every test module can import the real plugin code.
_gcode_stub = types.ModuleType("gcode")
_gcode_stub.CommandError = type("CommandError", (Exception,), {})
sys.modules.setdefault("gcode", _gcode_stub)

EXTRAS = (
    Path(__file__).resolve().parent.parent
    / "files" / "klipper" / "klippy" / "extras"
)
sys.path.insert(0, str(EXTRAS / "spoolman"))
# The extras dir itself too, so `spoolman` imports as a PACKAGE (its own modules use relative
# imports); the path above keeps the historical top-level imports (`from card_uids import ...`).
sys.path.insert(0, str(EXTRAS))

MOONRAKER = Path(__file__).resolve().parent.parent / "files" / "moonraker"
sys.path.insert(0, str(MOONRAKER))
