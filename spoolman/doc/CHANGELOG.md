# Changelog

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
