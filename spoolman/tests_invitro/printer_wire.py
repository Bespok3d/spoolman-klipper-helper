"""The wire to a real printer: Moonraker HTTP, gcode, and stock SSH.

Everything here is stdlib only, so the invitro suite adds no dependency beyond the gate's
own pytest. The printer address always arrives through B3D_HIL_HOST; a real LAN address
never lives in the tree.
"""
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

MOONRAKER_PORT = 7125
HTTP_TIMEOUT_SECONDS = 10.0
GCODE_TIMEOUT_SECONDS = 60.0
SSH_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 2.0
# The stock firmware's factory login, printed in Snapmaker's own documentation; not a secret.
STOCK_SSH_USER = "root"
STOCK_SSH_PASSWORD = "snapmaker"
HELPER_CONFIG_MOONRAKER_PATH = "bespok3d/klipper/spoolman.cfg"
MANUAL_SPOOLS_MOONRAKER_PATH = "bespok3d/data/manual_spools.json"
KLIPPY_LOG_TAIL_BYTES = 400_000


class PrinterWire:
    def __init__(self, printer_address):
        self.printer_address = printer_address

    def _moonraker_url(self, url_path):
        return f"http://{self.printer_address}:{MOONRAKER_PORT}{url_path}"

    def _moonraker_get(self, url_path, timeout_seconds=HTTP_TIMEOUT_SECONDS):
        request_url = self._moonraker_url(url_path)
        with urllib.request.urlopen(request_url, timeout=timeout_seconds) as reply:
            return json.load(reply)["result"]

    def _moonraker_post(self, url_path, timeout_seconds=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(self._moonraker_url(url_path), method="POST")
        with urllib.request.urlopen(request, timeout=timeout_seconds) as reply:
            return json.load(reply)["result"]

    def objects_status(self, *object_names):
        object_query = "&".join(urllib.parse.quote(name) for name in object_names)
        return self._moonraker_get(f"/printer/objects/query?{object_query}")["status"]

    def lane_status(self, physical_extruder):
        lane_object = f"AFC_lane E{physical_extruder}"
        return self.objects_status(lane_object).get(lane_object, {})

    def lane_extruders(self):
        printer_objects = self._moonraker_get("/printer/objects/list")["objects"]
        lane_names = (name for name in printer_objects if name.startswith("AFC_lane E"))
        return sorted(int(name.removeprefix("AFC_lane E")) for name in lane_names)

    def print_task_config(self):
        return self.objects_status("print_task_config").get("print_task_config", {})

    def print_state(self):
        return self.objects_status("print_stats").get("print_stats", {}).get("state", "")

    def klippy_state(self):
        try:
            return self._moonraker_get("/printer/info")["state"]
        except OSError:
            return "unreachable"

    def run_gcode(self, gcode_script):
        script_query = urllib.parse.quote(gcode_script)
        return self._moonraker_post(
            f"/printer/gcode/script?script={script_query}",
            timeout_seconds=GCODE_TIMEOUT_SECONDS,
        )

    def restart_klippy(self):
        # RESTART drops the HTTP connection while klippy reloads; the hangup IS the reply.
        try:
            self.run_gcode("RESTART")
        except OSError:
            pass

    def spoolman_connected(self):
        return bool(self._moonraker_get("/server/spoolman/status").get("spoolman_connected"))

    def spoolman_server_url(self):
        server_config = self._moonraker_get("/server/config")["config"]
        return server_config["spoolman"]["server"].rstrip("/")

    def config_file_text(self, config_relative_path):
        quoted_path = urllib.parse.quote(config_relative_path)
        request_url = self._moonraker_url(f"/server/files/config/{quoted_path}")
        with urllib.request.urlopen(request_url, timeout=HTTP_TIMEOUT_SECONDS) as reply:
            return reply.read().decode("utf-8")

    def config_root_path(self):
        file_roots = self._moonraker_get("/server/files/roots")
        return next(root["path"] for root in file_roots if root["name"] == "config")

    def klippy_log_tail(self):
        request = urllib.request.Request(
            self._moonraker_url("/server/files/klippy.log"),
            headers={"Range": f"bytes=-{KLIPPY_LOG_TAIL_BYTES}"},
        )
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as reply:
            return reply.read().decode("utf-8", errors="replace")

    def ssh(self, remote_command):
        ssh_user = os.environ.get("B3D_HIL_SSH_USER", STOCK_SSH_USER)
        ssh_password = os.environ.get("B3D_HIL_SSH_PASS", STOCK_SSH_PASSWORD)
        completed = subprocess.run(
            [
                "sshpass", "-p", ssh_password,
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                f"{ssh_user}@{self.printer_address}",
                remote_command,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
        return completed.stdout


class SpoolmanRecords:
    def __init__(self, server_url):
        self.server_url = server_url

    def spool(self, spool_id):
        record_url = f"{self.server_url}/api/v1/spool/{spool_id}"
        with urllib.request.urlopen(record_url, timeout=HTTP_TIMEOUT_SECONDS) as reply:
            return json.load(reply)


def wait_until(
    read_state: Callable[[], bool],
    waiting_for: str,
    timeout_seconds: float,
    poll_seconds: float = POLL_INTERVAL_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if read_state():
            return
        time.sleep(poll_seconds)
    raise TimeoutError(f"gave up after {timeout_seconds}s waiting for {waiting_for}")
