import sys
from pathlib import Path

EXTRAS = (
    Path(__file__).resolve().parent.parent
    / "files" / "klipper" / "klippy" / "extras"
)
sys.path.insert(0, str(EXTRAS / "spoolman"))

MOONRAKER = Path(__file__).resolve().parent.parent / "files" / "moonraker"
sys.path.insert(0, str(MOONRAKER))
