"""Seed Fluidd's AFC-card "show filament name" option ON, once.

The spoolman helper pushes a Spoolman display name onto every resolved AFC lane, but Fluidd
(1.37.2+) ships the card's name row hidden behind ``uiSettings.afc.showFilamentName`` (default
false), so a fresh install never sees the names it paid for. Fluidd persists that option in
Moonraker's database, merged over its local defaults at connect; writing the key ONCE flips the
default for every browser with no bundle patching. Seeded ONLY when the key is absent: a user
who turns the row off afterwards writes false to the same key, which is then never touched.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from moonraker.confighelper import ConfigHelper

FLUIDD_NAMESPACE = "fluidd"
SHOW_FILAMENT_NAME_KEY = "uiSettings.afc.showFilamentName"
_ABSENT = object()


class AfcUiDefaults:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()

    async def component_init(self) -> None:
        database = self.server.lookup_component("database")
        current = await database.get_item(FLUIDD_NAMESPACE, SHOW_FILAMENT_NAME_KEY, _ABSENT)
        if current is not _ABSENT:
            return
        await database.insert_item(FLUIDD_NAMESPACE, SHOW_FILAMENT_NAME_KEY, True)
        logging.info("AfcUiDefaults: seeded fluidd AFC show-filament-name ON")


def load_component(config: ConfigHelper) -> AfcUiDefaults:
    return AfcUiDefaults(config)
