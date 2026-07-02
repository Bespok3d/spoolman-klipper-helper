import sys
from pathlib import Path

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
