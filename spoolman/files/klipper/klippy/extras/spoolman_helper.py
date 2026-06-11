import json

from .spoolman.afc import push_spool_to_afc
from .spoolman.commands import Commands
from .spoolman.logs import Logs
from .spoolman.macros import Macros
from .spoolman.print_lifecycle import PrintLifecycle
from .spoolman.spoolman import Spoolman
from .spoolman.u1_tools import U1Tools

EXTRUDERS_COUNT = 4
MAX_TOOLS_COUNT = 32
RFID_DATA_FILE = "/oem/printer_data/config/bespok3d/data/rfid_data.json"


def filament_info_to_string(filament_info, level="info"):
    if not filament_info:
        return "- Missing Filament Info! -"

    vendor = filament_info.get("VENDOR")
    main = filament_info.get("MAIN_TYPE")
    sub = filament_info.get("SUB_TYPE")
    colour = filament_info.get("ARGB_COLOR")
    spool_id = filament_info.get("SPOOL_ID")
    sku = filament_info.get("SKU")

    base = f"{vendor} {main} {sub} (colour: #{colour}, Spoolman id: {spool_id}, sku: {sku})"

    if level != "debug":
        return base

    known_keys = {"VENDOR", "MAIN_TYPE", "SUB_TYPE", "ARGB_COLOR", "SPOOL_ID", "SKU"}
    extras = [f"{k}->{v}" for k, v in filament_info.items() if k not in known_keys]
    if not extras:
        return base
    return base + "\nadditional filament info: " + ", ".join(extras)


def update_objects_list(original, updates):
    count = min(len(original), len(updates))
    for i in range(count):
        update = updates[i]
        if not update:
            continue
        for key, value in update.items():
            if value is not None:
                if original[i]:
                    original[i][key] = value
    return original


def is_untagged_filament(filament_info):
    if not filament_info:
        return True
    vendor = filament_info.get("VENDOR")
    return vendor in ("NONE", "Generic")


class SpoolmanHelper:
    possible_modes = ["manual", "auto"]

    def __init__(self, config):
        self.printer = config.get_printer() if hasattr(config, "get_printer") else config
        self.gcode = self.printer.lookup_object("gcode")

        self.logging = config.get("logging", "info") if hasattr(config, "get") else "info"
        mode = config.get("mode", "auto") if hasattr(config, "get") else "auto"
        if mode not in self.possible_modes:
            mode = "auto"
        self.mode = mode

        self.logs = Logs(self.printer, self)
        self.u1_tools = U1Tools(config, self.logs)
        self.spoolman = Spoolman(self.printer, self.logs)
        self.macros = Macros(self.printer, self.logs)
        self.commands = Commands(self.printer, self.logs, self)
        self.lifecycle = PrintLifecycle(self.printer, self.logs, self)

        self.spool_holders = [None] * EXTRUDERS_COUNT
        self.spools_by_id = {}

        self.u1_tools.update_map()
        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def _on_ready(self):
        self.logs.log(f"Loaded! mode: {self.mode}, logs level: {self.logging}")

        rfid = self.printer.lookup_object('bespok3d_rfid', None)
        if rfid is not None:
            rfid.register_spool_notify(self._on_filament_update)
            self.logs.log("Registered spool notify via bespok3d_rfid")
        else:
            detector = self.printer.lookup_object('filament_detect', None)
            if detector is not None:
                detector.register_cb_2_update_filament_info(self._on_filament_update)
                self.logs.log("Registered filament update hook (direct, no rfid module)")
            else:
                self.logs.warn("No notification source available, spool tracking disabled")

        self.detect_spools()

    def _on_filament_update(self, channel, info, is_clear):
        if is_clear:
            self.clear_spool_for_channel(channel)
        else:
            self.set_spool_for_channel(channel, info)

    def _load_rfid_data(self):
        try:
            with open(RFID_DATA_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _push_spool_to_afc(self, channel, spool_id):
        push_spool_to_afc(self.printer, channel, spool_id, EXTRUDERS_COUNT)

    def set_spool_for_channel(self, channel, filament_info):
        self.logs.verbose(f"Received spool for extruder {channel}")
        if not (0 <= channel < EXTRUDERS_COUNT):
            self.logs.error(f"Extruder must be 0..{EXTRUDERS_COUNT - 1}")
            return
        self.spool_holders[channel] = filament_info
        if not is_untagged_filament(filament_info):
            self.apply_spool_for_extruder(channel)

    def clear_spool_for_channel(self, channel):
        self.logs.log(f"Clearing spool from extruder {channel}")
        if channel is None:
            return
        tool = f"T{channel}"
        self.macros.set_spool_id_for_tool(tool, None)
        self._push_spool_to_afc(channel, None)
        holder = self.spool_holders[channel]
        if holder is not None:
            spool_id = holder.get("SPOOL_ID")
            if spool_id and spool_id in self.spools_by_id:
                del self.spools_by_id[spool_id]
        self.spool_holders[channel] = None

    def find_spool_for_tool(self, tool_id):
        macro_spool = self.get_spool_for_tool(tool_id)
        mapped_spool = self.get_mapped_spool_for_tool(tool_id)
        self.logs.verbose(f"Possible spools: macro->{macro_spool}, mapped->{mapped_spool}")
        if self.mode == 'manual':
            return macro_spool or mapped_spool
        else:
            return mapped_spool if mapped_spool and "SPOOL_ID" in mapped_spool else macro_spool

    def _bind_resolved_spool(self, extruder, spool, spool_id):
        self.spools_by_id[spool_id] = spool
        self.logs.verbose(f"Got spool_id: {spool_id}")
        tool = f"T{extruder}"
        self.logs.log(f"Tool {tool} is using: {filament_info_to_string(spool, self.logging)}")
        self.macros.set_spool_id_for_tool(tool, spool_id)
        self._push_spool_to_afc(extruder, spool_id)

    def apply_spool_for_extruder(self, extruder):
        self.logs.verbose(f"Trying to bind spool to extruder {extruder}")
        spool = self.spool_holders[extruder]
        if not spool:
            self.logs.warn(f"No filament info for extruder {extruder}. Normal if no RFID tag.")
            return

        self.logs.verbose(
            f"Resolving filament info {filament_info_to_string(spool, self.logging)} "
            f"for extruder {extruder}"
        )

        def on_resolve_spool(resolved, spool=spool):
            if resolved:
                if not spool.get("SPOOL_ID") and resolved.get("id"):
                    spool["SPOOL_ID"] = resolved["id"]
                spool_id = spool.get("SPOOL_ID") or resolved.get("id")
            else:
                spool_id = spool.get("SPOOL_ID")

            if not spool_id:
                self.logs.warn(
                    f"Unable to resolve spool id for extruder {extruder} and filament "
                    f"{filament_info_to_string(spool, self.logging)}"
                )
                return

            self._bind_resolved_spool(extruder, spool, spool_id)

        self.spoolman.resolve_spool(spool, on_resolve_spool)

    def get_spool_for_tool(self, tool_id):
        spool_id = self.macros.get_spool_id_for_tool(tool_id)
        if spool_id:
            return self.spools_by_id.get(spool_id, {"SPOOL_ID": spool_id})

    def get_mapped_spool_for_tool(self, tool_id):
        self.logs.verbose(f"Resolving extruder for T{tool_id}")
        extruder = self.u1_tools.extruder_for_tool(tool_id)
        if extruder is None:
            self.logs.warn(f"Cannot find mapped extruder for T{tool_id}")
            return None
        spool = self.spool_holders[extruder]
        if is_untagged_filament(spool):
            self.logs.verbose(f"Filament for T{extruder} is untagged, falling back to manual")
            spool = self.get_spool_for_tool(extruder)
        if spool is None:
            self.logs.warn(f"Cannot find filament info for T{tool_id} on extruder {extruder}")
            return None
        self.logs.verbose(
            f"Found filament for T{tool_id} on extruder {extruder}: "
            f"{filament_info_to_string(spool, self.logging)}"
        )
        return spool

    def set_active_tool(self, tool_id):
        spool = self.find_spool_for_tool(tool_id)
        self.logs.verbose(f"Spool for requested tool: {spool}")
        if not (spool and spool.get("SPOOL_ID")):
            self.logs.warn(f"Cannot set active spool for T{tool_id}: unable to resolve spool id")
            return
        self.logs.log(f"Tracking: {filament_info_to_string(spool, self.logging)}")
        self.spoolman.set_active_spool(spool["SPOOL_ID"])

    def sync_spools_tools(self):
        if self.mode != 'manual':
            self.u1_tools.update_map()
            return
        for tool_id in range(MAX_TOOLS_COUNT):
            self._sync_manual_tool(tool_id)

    def _sync_manual_tool(self, tool_id):
        spool_id = self.macros.get_spool_id_for_tool(tool_id)
        if not spool_id:
            return

        def on_spool(spool, sid=spool_id):
            self.spools_by_id[sid] = spool
        self.spoolman.resolve_spool({"SPOOL_ID": spool_id}, on_spool)
        extruder = self.u1_tools.extruder_for_tool(tool_id)
        if extruder is not None:
            self._push_spool_to_afc(extruder, spool_id)

    def detect_spools(self):
        spools = self.u1_tools.get_spools_config()
        self.logs.debug(f"detect_spools spools: {spools}")
        update_objects_list(self.spool_holders, spools)

        rfid_data = self._load_rfid_data()
        for ch_str, info in rfid_data.items():
            extruder = int(ch_str)
            if 0 <= extruder < len(self.spool_holders) and not is_untagged_filament(info):
                self.spool_holders[extruder] = info
                self.logs.verbose(f"Restored rfid data for extruder {extruder} from rfid_data.json")

        for extruder in range(len(spools)):
            self.macros.detect_spool(extruder)
            self.apply_spool_for_extruder(extruder)

    def dump(self, raw):
        if raw:
            self.logs.log(
                f"\nspool_holders: {json.dumps(self.spool_holders, indent=2)}\n"
                f"spools_by_id: {self.spools_by_id}"
            )
            return
        self.logs.log("Dumping Spool Holders:")
        for spool in self.spool_holders:
            self.logs.log(filament_info_to_string(spool, self.logging))

    def clear_spool_ids(self):
        self.spools_by_id = {}


def load_config(config):
    return SpoolmanHelper(config)
