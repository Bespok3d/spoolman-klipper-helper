"""Persistence for manually picked spools, which have no tag to re-read after a restart.

Mirrors rfid_data.json: one JSON file in the bespok3d data dir mapping tool index -> Spoolman
spool id, rewritten on every manual pick change and replayed at klippy-ready. Only untagged
lanes ever land here (a tagged lane's identity comes back from its tag). Written atomically so
a power loss mid-write cannot corrupt the file into losing every pick.
"""
import json
import os


def load_manual_spools(path):
    try:
        with open(path) as manual_file:
            raw = json.load(manual_file)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = (_coerced_pick(tool_key, spool_id) for tool_key, spool_id in raw.items())
    return dict(pick for pick in entries if pick is not None)


def _coerced_pick(tool_key, spool_id):
    try:
        return int(tool_key), int(spool_id)
    except (TypeError, ValueError):
        return None


def store_manual_spool(path, tool_index, spool_id):
    picks = load_manual_spools(path)
    if spool_id is None:
        if tool_index not in picks:
            return
        del picks[tool_index]
    else:
        if picks.get(tool_index) == spool_id:
            return
        picks[tool_index] = spool_id
    _write_atomic(path, picks)


def _write_atomic(path, picks):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    staging_path = f"{path}.tmp"
    with open(staging_path, "w") as staging_file:
        json.dump({str(tool): spool for tool, spool in picks.items()}, staging_file)
    os.replace(staging_path, path)
