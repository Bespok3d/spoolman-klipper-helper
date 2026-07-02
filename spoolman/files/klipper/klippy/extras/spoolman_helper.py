"""The [spoolman_helper] extra: composition root and facade of the spoolman helper system.

Wires the concern modules under spoolman/ together and routes the RFID substrate's filament
updates into them; every collaborator reaches its peers through this facade's small public
surface. The two data-file paths below are deployment facts of the Bespok3d U1 install (a
stock Klipper machine would keep these files elsewhere), injected here so no concern module
hardcodes them.
"""
from .spoolman.afc import push_spool_to_afc
from .spoolman.carrier_watch import CarrierWatch
from .spoolman.commands import Commands
from .spoolman.helper_options import HelperOptions
from .spoolman.lane_report import LaneReport
from .spoolman.logs import Logs
from .spoolman.macros import Macros
from .spoolman.manual_restore import ManualSpoolRestore
from .spoolman.print_lifecycle import PrintLifecycle
from .spoolman.print_task_writer import PrintTaskWriter
from .spoolman.spool_detection import SpoolDetection
from .spoolman.spool_holders import SpoolHolders
from .spoolman.spool_resolution import SpoolResolution
from .spoolman.spoolman import Spoolman
from .spoolman.tracking import SpoolTracking
from .spoolman.u1_tools import EXTRUDERS_COUNT, U1Tools

RFID_DATA_FILE = "/oem/printer_data/config/bespok3d/data/rfid_data.json"
MANUAL_SPOOLS_FILE = "/oem/printer_data/config/bespok3d/data/manual_spools.json"


class SpoolmanHelper:
    def __init__(self, config):
        self.printer = config.get_printer() if hasattr(config, "get_printer") else config

        options = HelperOptions(config)
        self.mode = options.mode
        self.logging = options.logging

        self.logs = Logs(self.printer, self)
        self.u1_tools = U1Tools(config, self.logs)
        self.spoolman = Spoolman(
            self.printer, self.logs, options.card_uids_strategy, options.card_uids_auto_register
        )
        self.macros = Macros(self.printer, self.logs)
        self.commands = Commands(self.printer, self.logs, self)
        self.lifecycle = PrintLifecycle(self.printer, self.logs, self)
        self.writer = PrintTaskWriter(self.printer, self.logs, self.macros)
        self.tracking = SpoolTracking(
            self,
            track_location=options.track_location,
            location=options.location,
        )

        self.holders = SpoolHolders(
            self.logs, self.macros, self.push_spool_to_afc, self._lane_has_filament
        )
        self.resolution = SpoolResolution(self)
        self.detection = SpoolDetection(self, RFID_DATA_FILE)
        self.manual_restore = ManualSpoolRestore(self, MANUAL_SPOOLS_FILE)
        self.report = LaneReport(self)

        self.u1_tools.update_map()
        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def _on_ready(self):
        self.logs.log(f"Loaded! mode: {self.mode}, logs level: {self.logging}")
        self.spoolman.define_card_uids_field()
        self._register_filament_update_source()
        self.detect_spools()
        self.manual_restore.restore_all()
        CarrierWatch(self.printer, self.tracking).start()

    # Preferred source is the rfid-ntag substrate; the stock firmware's filament_detect is the
    # direct fallback. Without either, tags never reach the helper (manual picks still work).
    def _register_filament_update_source(self):
        rfid = self.printer.lookup_object('bespok3d_rfid', None)
        if rfid is not None:
            rfid.register_spool_notify(self._on_filament_update)
            self.logs.log("Registered spool notify via bespok3d_rfid")
            return
        detector = self.printer.lookup_object('filament_detect', None)
        if detector is not None:
            detector.register_cb_2_update_filament_info(self._on_filament_update)
            self.logs.log("Registered filament update hook (direct, no rfid module)")
            return
        self.logs.warn("No notification source available, spool tracking disabled")

    def _on_filament_update(self, channel, info, is_clear):
        if is_clear:
            self.clear_spool_for_channel(channel)
        else:
            self.set_spool_for_channel(channel, info)

    def push_spool_to_afc(self, channel, spool_id):
        push_spool_to_afc(self.printer, channel, spool_id, EXTRUDERS_COUNT)

    @property
    def spool_holders(self):
        return self.holders.spool_holders

    @property
    def spools_by_id(self):
        return self.holders.spools_by_id

    def lane_is_tagged(self, channel):
        return self.holders.lane_is_tagged(channel)

    def remember_manual_spool(self, tool_index, spool_id):
        self.manual_restore.remember(tool_index, spool_id)

    def set_spool_for_channel(self, channel, filament_info):
        if self.holders.store_channel_report(channel, filament_info):
            self.apply_spool_for_extruder(channel)

    def _lane_has_filament(self, channel):
        task = self.printer.lookup_object("print_task_config", None)
        config = getattr(task, "print_task_config", None)
        exist = config.get("filament_exist") if isinstance(config, dict) else None
        if not isinstance(exist, list) or not (0 <= channel < len(exist)):
            return True  # no reliable presence signal: keep reporting it
        return bool(exist[channel])

    def clear_spool_for_channel(self, channel, force=False):
        self.holders.clear_channel(channel, force)

    def clear_all_spools(self):
        self.clear_spool_ids()
        for channel in range(EXTRUDERS_COUNT):
            self.clear_spool_for_channel(channel, force=True)
        self.tracking.clear_active()

    def apply_spool_for_extruder(self, extruder):
        self.resolution.apply_spool_for_extruder(extruder)

    def set_active_tool(self, tool_id):
        self.resolution.set_active_tool(tool_id)

    def bind_channel_card_uid(self, channel, spool_id):
        self.resolution.bind_channel_card_uid(channel, spool_id)

    def sync_spools_tools(self):
        self.detection.sync_spools_tools()

    def detect_spools(self):
        self.detection.detect_spools()

    def dump(self, raw):
        self.report.dump(raw)

    def clear_spool_ids(self):
        self.holders.clear_ids()


def load_config(config):
    return SpoolmanHelper(config)
