# SPDX-FileCopyrightText: Copyright (C) 2026 unlucio and the Bespok3d contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Moonraker config this plugin writes must carry the address as the person gave it.

A Spoolman published over https, or behind a name on the standard web port, is a whole address.
Pinning a protocol in the template sent Moonraker to the wrong place for every one of those.
"""

from pathlib import Path

MOONRAKER_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "files" / "cfg" / "moonraker" / "spoolman.cfg.tmpl"
)


def test_the_server_address_is_written_exactly_as_it_was_given() -> None:
    template = MOONRAKER_TEMPLATE.read_text()

    assert "server: $SPOOLMAN_SERVER" in template
    assert "://" not in template


def test_both_moonraker_sections_take_the_same_address() -> None:
    template = MOONRAKER_TEMPLATE.read_text()

    assert template.count("server: $SPOOLMAN_SERVER") == 2
