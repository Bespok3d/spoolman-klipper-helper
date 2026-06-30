# Changelog

## 0.1.11

- Optional spool-location tracking: turn on "Track spool location in Spoolman" and every spool
  loaded on the printer gets this printer's name written to its Spoolman `location` field (cleared
  when the spool is unloaded), so the Spoolman inventory shows which printer a spool is on. Opt-in
  (off by default). The location name auto-defaults to this printer's own display name (the instance
  name from the frontend, read from Moonraker's database), so it needs no config; set the name field
  to override. Works for every loaded lane (tagged and manually picked), driven by the bridge which
  already sees every tool spool_id change.
- A manually picked spool on an untagged lane is now RELEASED when its filament is physically pulled.
  Such a lane fires no RFID event on removal, so its tool spool_id (and therefore its Spoolman
  location, the screen slot, and the AFC lane name) used to stay stale -- just like fluidd/mainsail.
  The bridge now watches the firmware's `filament_exist` flag and clears the lane's spool_id when its
  filament leaves, cascading through the usual cleanup. Skipped mid-print (a runout is the firmware's
  to handle).

- Identify a spool by its tag's hardware UID, not just its decoded SKU or an explicit spool id.
  Every NTAG (and any keyless proprietary tag the reader surfaces by UID) can be bound to a
  Spoolman spool through a `nfc_id` extra field, so even tags we can't fully decode are tracked
  once bound. A spool has two sides / two tags, so `nfc_id` is a LIST of UIDs (also writable by a
  mobile app); binding appends a UID only if it is not already assigned to any spool (no
  duplicates), and a lookup that hits more than one spool takes the first. A configurable
  resolution order (`nfc_strategy`, default `spool_id,uid,sku`, first match wins) picks which
  identity wins, and `nfc_auto_register` (opt-in, OFF by default) adds the current UID to a spool the
  moment it is resolved by SKU, so the next tap is a direct UID match; with it off, tags are only
  ever bound on explicit request (`SH_BIND_NFC`). Binds use GET-merge-PATCH so a spool's
  other extra fields (and its other tag UIDs) are preserved. Re-randomized (DESFire) UIDs are
  never used as a key.
- New `SH_BIND_NFC CHANNEL=<n> SPOOL=<id>` g-code command to bind a channel's current tag UID to a
  spool by hand (recovers from any miss; also what an app-side bind affordance would drive later).
  The `nfc_id` Spoolman field is defined automatically on startup.
- Picking a spool for a lane (its tool `spool_id` set to a real id, e.g. from the Spoolman panel)
  now makes it Spoolman's active spool, so tracking activates even when that tool is not mounted on
  the carrier. The active spool follows the SELECTED spool, not the carrier-mount: the previous
  "no tool mounted -> clear" rule (which wrongly wiped a loaded-but-unmounted selection) is gone. An
  unknown lane (no `spool_id`) is never in the active-spool path; pulling the last spool off the
  machine still clears it.
- `DUMP_SPOOLS` is now honest and readable: each line is labelled by tool (`T0:`..`T3:`), an empty
  lane reads `empty` (instead of an inconsistent `UNKNOWN`/`Missing Filament Info`), and a
  hand-assigned spool reads `Spoolman spool <id> (manually assigned): <vendor> <filament name>`
  (e.g. `ELEGOO Matte Teal Green`) instead of `UNKNOWN`. The bridge now always pushes the lane's
  vendor+name label on a pick (not only when the print_task_config write changes), so a re-pick
  after a restart re-labels the lane.
- An empty lane is no longer mislabelled `loaded with UNKNOWN filament` during `DETECT_SPOOLS` (the
  detector re-reads every channel, and a bare lane reports untagged just like a real loaded-untagged
  one). The firmware's `filament_exist` flag now distinguishes the two, so only a lane that actually
  holds filament is reported/held as UNKNOWN; a bare lane stays empty.

## 0.1.10

- Fix: a picked spool stopped reflecting to the screen/AFC after a Klipper restart. The bridge
  subscribed to spool-id changes only once, but Moonraker drops every subscription callback on a
  Klipper disconnect, so after the first `firmware_restart` the bridge went deaf. It now
  re-subscribes on every `klippy_ready` and reconciles the current spools on (re)start, so a spool
  set before the restart is re-applied.
- Clearing a lane now mirrors too: when a non-tagged lane's spool is removed, the bridge resets
  that slot to the firmware's empty defaults and blanks the AFC lane name (it used to leave the
  removed spool's color/material on the screen).
- `CLEAR_ALL_SPOOLS` now clears every lane, including RFID-tagged/"official" ones (it issues the
  firmware's `FORCE=1` reset that those lanes require) and pushes the clear to AFC. It also no
  longer spams an error for every non-existent tool macro (tools with no `spool_id` are skipped).
- Spoolman's active spool is now cleared when the last spool is pulled off the machine (every
  tool's spool_id is gone) and no print is running, instead of only at print end. (See 0.1.11 for
  the active spool also following a manual pick.)
- A loaded-but-unassigned spool (untagged, no Spoolman spool picked) now reads just "UNKNOWN" in
  the helper's logs/labels instead of "NONE" -- present but unidentified, not absent -- and drops
  the meaningless firmware-default colour/spool-id/sku it used to carry. This is only our own
  labelling; the firmware's print_task_config keeps "NONE" (its sentinel for "not edited", which
  drives the runout / sensor / flow-calibration prompts), so SM behaviour is untouched.
- Filament colours in the helper logs now read as "#RRGGBB (name)" (e.g. "#1D6C6A (teal)") instead
  of a raw ARGB integer like "#4294967295".

## 0.1.9

- When mirroring an untagged spool, the bridge now also pushes a composed `"<vendor> <name>"`
  label ("ZIRO Silk Gold") to the AFC lane via `SET_LANE_FILAMENT_NAME`, alongside the existing
  color/material write. The AFC panel cannot compose vendor+name itself (it shows the Spoolman
  filament name alone), so this gives an untagged channel the richer label; RFID-tagged channels
  stay untouched.

## 0.1.8

- Fix: the color/material write was rejected by the firmware ("incomplete parameters").
  `SET_PRINT_FILAMENT_CONFIG` requires the vendor and sub-type whenever a material is set, so
  the bridge now always sends them together (Spoolman has no sub-type, so it is left empty).

## 0.1.7

- Untagged spool color and material now reach the printer touchscreen and the AFC panel.
  When you pick a Spoolman spool for a lane that has no RFID tag, a passive Moonraker
  observer writes that spool's color, material, and vendor into the printer's print task,
  so every view agrees without touching the screen. RFID-tagged lanes are left alone
  (the tag stays the source of truth). A pick made mid-print is applied when the print ends.

## 0.1.6

- Manual Spoolman spool selection reflects on AFC: when a tool's spool id is set
  by hand (no RFID), the per-tool sync pushes it to the matching AFC lane so the
  panel shows the filament name.

## 0.1.2

- Selection **mode** (auto/manual) and **log level** are now configurable from the plugin
  detail page, and can be changed on an installed printer without a reinstall.

## 0.1.1

- Internal cleanup of the Klipper module (clearer structure, full test coverage). No change
  in behaviour.

## 0.1.0

- First release: syncs the active spool to your Spoolman server, with optional RFID
  auto-detection via the RFID Spool Reader. Handles the U1's 32 virtual-tool system.
