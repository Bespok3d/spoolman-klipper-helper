import json

from .spoolman.afc import push_spool_to_afc
from .spoolman.commands import Commands
from .spoolman.filament_info import filament_info_to_string, is_untagged_filament
from .spoolman.logs import Logs
from .spoolman.macros import Macros
from .spoolman.nfc_tracking import parse_strategy_chain, trackable_uid
from .spoolman.print_lifecycle import PrintLifecycle
from .spoolman.spoolman import Spoolman
from .spoolman.u1_tools import U1Tools

TRUTHY = ("true", "1", "on", "yes")

EXTRUDERS_COUNT = 4
MAX_TOOLS_COUNT = 32
RFID_DATA_FILE = "/oem/printer_data/config/bespok3d/data/rfid_data.json"


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

        strategy_chain = self._read_nfc_config(config)

        self.logs = Logs(self.printer, self)
        self.u1_tools = U1Tools(config, self.logs)
        self.spoolman = Spoolman(self.printer, self.logs, strategy_chain, self.nfc_auto_register)
        self.macros = Macros(self.printer, self.logs)
        self.commands = Commands(self.printer, self.logs, self)
        self.lifecycle = PrintLifecycle(self.printer, self.logs, self)

        self.spool_holders = [None] * EXTRUDERS_COUNT
        self.spools_by_id = {}

        self.u1_tools.update_map()
        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def _read_nfc_config(self, config):
        nfc_strategy = config.get("nfc_strategy", "") if hasattr(config, "get") else ""
        auto_register = (
            config.get("nfc_auto_register", "false") if hasattr(config, "get") else "false"
        )
        self.nfc_auto_register = str(auto_register).strip().lower() in TRUTHY
        return parse_strategy_chain(nfc_strategy)

    def _on_ready(self):
        self.logs.log(f"Loaded! mode: {self.mode}, logs level: {self.logging}")
        self.spoolman.define_nfc_id_field()

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
        if is_untagged_filament(filament_info):
            self._note_untagged_lane(channel, filament_info)
            return
        self.spool_holders[channel] = filament_info
        self.apply_spool_for_extruder(channel)

    # An untagged report is a "loaded but unidentified" spool only when filament is actually present.
    # DETECT_SPOOLS re-reads every channel, so a bare lane reports untagged too -- that is empty, not
    # UNKNOWN. The firmware's filament_exist flag is the presence signal.
    def _note_untagged_lane(self, channel, filament_info):
        if self._lane_has_filament(channel):
            self.spool_holders[channel] = filament_info
            self.logs.log(f"Tool T{channel} loaded with UNKNOWN filament (unassigned, no tag)")
        else:
            self.spool_holders[channel] = None
            self.logs.verbose(f"Tool T{channel} is empty")

    def _lane_has_filament(self, channel):
        task = self.printer.lookup_object("print_task_config", None)
        config = getattr(task, "print_task_config", None)
        exist = config.get("filament_exist") if isinstance(config, dict) else None
        if not isinstance(exist, list) or not (0 <= channel < len(exist)):
            return True  # no reliable presence signal: keep reporting it
        return bool(exist[channel])

    def clear_spool_for_channel(self, channel, force=False):
        self.logs.log(f"Clearing spool from extruder {channel}")
        if channel is None:
            return
        tool = f"T{channel}"
        self.macros.set_spool_id_for_tool(tool, None)
        self._push_spool_to_afc(channel, None)
        if force:
            self.macros.clear_print_task_config(channel)
        holder = self.spool_holders[channel]
        if holder is not None:
            spool_id = holder.get("SPOOL_ID")
            if spool_id and spool_id in self.spools_by_id:
                del self.spools_by_id[spool_id]
        self.spool_holders[channel] = None

    def clear_all_spools(self):
        self.clear_spool_ids()
        for channel in range(EXTRUDERS_COUNT):
            self.clear_spool_for_channel(channel, force=True)
        self.spoolman.clear_active_spool()

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

    def bind_channel_nfc(self, channel, spool_id):
        if not (0 <= channel < EXTRUDERS_COUNT):
            self.logs.error(f"Channel must be 0..{EXTRUDERS_COUNT - 1}")
            return
        info = self.spool_holders[channel]
        uid = trackable_uid(info.get("CARD_UID")) if info else None
        if not uid:
            self.logs.warn(f"No stable tag UID on channel {channel} to bind to spool {spool_id}")
            return
        self.logs.log(f"Binding channel {channel} UID {uid} -> spool {spool_id}")
        self.spoolman.bind_uid(spool_id, uid)

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
        for channel in range(EXTRUDERS_COUNT):
            self.logs.log(f"T{channel}: {self._lane_summary(channel)}")

    # The spool a lane effectively carries: a real detected (tagged/resolved) spool wins; otherwise a
    # hand-assigned Spoolman spool_id; otherwise a loaded-but-unidentified spool is UNKNOWN and a bare
    # lane is empty. Keeps DUMP honest about a manually picked spool instead of showing UNKNOWN.
    def _lane_summary(self, channel):
        holder = self.spool_holders[channel]
        if holder and not is_untagged_filament(holder):
            return filament_info_to_string(holder, self.logging)
        assigned = self.macros.get_spool_id_for_tool(channel)
        if assigned:
            resolved = self.spools_by_id.get(assigned)
            if resolved:
                return filament_info_to_string(resolved, self.logging)
            label = self._assigned_lane_label(channel)
            return f"Spoolman spool {assigned} (manually assigned){label}"
        if holder:
            return "UNKNOWN (loaded, no tag)"
        return "empty"

    # "<vendor> <filament name>" (e.g. "ELEGOO Matte Teal Green") that the bridge already pushed onto
    # the AFC lane via SET_LANE_FILAMENT_NAME; read it back so DUMP names a manual pick. Empty if AFC
    # is absent or no name was pushed.
    def _assigned_lane_label(self, channel):
        lane = self.printer.lookup_object(f"AFC_lane E{channel}", None)
        name = getattr(lane, "filament_name", "") if lane is not None else ""
        name = name.strip() if isinstance(name, str) else ""
        return f": {name}" if name else ""

    def clear_spool_ids(self):
        self.spools_by_id = {}


def load_config(config):
    return SpoolmanHelper(config)
