"""The one behavior that matters: seed only when absent, so a user's later choice is final."""
import asyncio

from afc_ui_defaults import FLUIDD_NAMESPACE, SHOW_FILAMENT_NAME_KEY, AfcUiDefaults


class FakeDatabase:
    def __init__(self, items=None):
        self.items = dict(items or {})
        self.inserts = []

    async def get_item(self, namespace, key, default):
        return self.items.get((namespace, key), default)

    async def insert_item(self, namespace, key, value):
        self.items[(namespace, key)] = value
        self.inserts.append((namespace, key, value))


class FakeServer:
    def __init__(self, database):
        self.database = database

    def lookup_component(self, name, default=None):
        return self.database if name == "database" else default


class FakeConfig:
    def __init__(self, server):
        self._server = server

    def get_server(self):
        return self._server


def _init_component(database):
    component = AfcUiDefaults(FakeConfig(FakeServer(database)))
    asyncio.run(component.component_init())


def test_seeds_show_filament_name_on_when_absent():
    database = FakeDatabase()
    _init_component(database)
    assert database.inserts == [(FLUIDD_NAMESPACE, SHOW_FILAMENT_NAME_KEY, True)]


def test_an_existing_value_is_never_touched():
    # The user turned the row OFF: their false persists across every restart and reinstall.
    database = FakeDatabase({(FLUIDD_NAMESPACE, SHOW_FILAMENT_NAME_KEY): False})
    _init_component(database)
    assert database.inserts == []
    assert database.items[(FLUIDD_NAMESPACE, SHOW_FILAMENT_NAME_KEY)] is False
