# Changelog

## 0.1.31

- Fix: a tag bound by another app that writes the same `card_uids` field now resolves here however
  that app spelled the UID: any letter case, `:` or `-` between the bytes, the bytes spaced apart, a
  leading `0x`, a list or one comma-separated value. Every spelling is read as the one tag it is, so
  the same physical tag no longer reads as two different tags and binding it again adds no duplicate.
  A UID we write back is always stored bare and lowercase, whatever it looked like before.
- New setting, **How tag UIDs are stored in Spoolman**: `array` (the default, a proper list) or
  `comma_separated`, for when another app reading this field cannot handle a list. Either form is
  read back correctly here, so the choice only affects what other readers see.

## 0.1.30

- Every lane line now also prints the card's own UID, in hex, next to the spool it resolved to, so you
  can read the UID straight off the console and bind it with
  `SH_BIND_CARD_UID CHANNEL={0..3} SPOOL=<id>` instead of digging it out of the firmware. A lane whose
  tag has no usable UID (none at all, or one that changes on every tap) says nothing extra, because
  there is nothing there to bind.
- Doc: the page now records what was checked on a real printer (Snapmaker U1 on stock firmware, two
  genuine Snapmaker spools: binding the card UID makes the lane resolve by the card, with the Article
  Number cleared so no SKU match was possible), and adds a "Limits worth knowing about" section
  covering the slicer's own sync behaviour and what to do about a tag Spoolman knows nothing about.

## 0.1.29

- Fix: a manual tool change while the printer is idle no longer prints a phantom "Cannot resolve
  extruder for T{n}" error. The tool-to-extruder map had two sources of truth: the tracker read
  the live firmware map (always current), while the tool lookup read a separate cached copy that
  was only filled during a print and wiped when a print ended. Outside a print (and permanently in
  manual mode) the cache was empty, so the lookup failed and logged the error even though the
  tracker resolved the same tool fine. The cache is gone; both now read the one live map.
- Internal: removed the unused print-resume handler that only refreshed that cache.

## 0.1.28

- Internal: the helper coordinator was decomposed into one-concern modules (options parsing,
  holder state, resolution, detection/sync, manual-pick restore, DUMP report); no behavior
  change. U1-specific facts (tooling constants, print-task reads, AFC lane access) are
  consolidated behind the device modules, groundwork for a future mainline-Klipper submission.

## 0.1.27

- Fix: manual-spool restore actually restores. 0.1.26's restore checked filament presence at
  klippy-ready, when the firmware's filament_exist still holds its all-False defaults, so every
  pick was deleted as "filament gone". Presence is no longer checked at restore (a genuinely
  pulled filament is released by the removal watcher once the firmware reports it); only a tag
  that appeared while powered off supersedes a saved pick.
- Fix: a tagged spool whose SPOOL_ID arrives as a string no longer logs as "Spoolman spool N":
  the label cache lookup tolerates both key types.

## 0.1.26

- Add: manually picked spools survive restarts and reboots. Picks persist to
  `manual_spools.json` beside `rfid_data.json` (written atomically on every change) and are
  replayed at klippy-ready through the normal pick cascade, so the screen, AFC lane label,
  Spoolman widget and location all come back on their own. A tag that appeared on the lane
  while powered off wins; a lane whose filament left while powered off is released instead.
- Fix: a manually picked spool now logs like a tagged one ("Vendor Material Name (colour: ...,
  Spoolman id: N, sku: ...)") instead of "Tracking: Spoolman spool N" -- the spool data the pick
  already fetches for the screen write doubles as label data.

## 0.1.25

- Fix (review find): every explicit active-spool clear (print end, cancel, CLEAR_ALL_SPOOLS,
  SH_CLEAR_ACTIVE_SPOOL) now goes through the tracking owner instead of straight to Moonraker.
  A bypass left the tracker's ground truth stale, so the NEXT print starting with the same tool
  was equal-skipped: no consumption tracked until its first toolchange. Also: a tool still
  mounted when a print ends is no longer re-applied (a finished print consumes nothing), and
  the orphaned direct-clear helper was removed.

## 0.1.24

- Fix: no more "Tracking: no active spool" in the middle of an idle toolchange (the 57 -> none
  -> 57 flap). The park->pick gap is transient regardless of print state; a carrier-empty now
  clears the active spool only when it PERSISTS past a 5-second settle window (a real eject).
  Print end/cancel and pulling the last spool still clear immediately: those are settled facts.
  Exactly one Tracking line per toolchange, at macro time.

## 0.1.23

- Add: Fluidd's AFC card shows filament names OUT OF THE BOX. Fluidd 1.37.2 ships the card's
  name row hidden behind a UI option (uiSettings.afc.showFilamentName, default off); a new tiny
  Moonraker component (afc_ui_defaults) seeds that option ON once, in Moonraker's database,
  where Fluidd persists it anyway. No bundle patching, and only when the key is absent: turning
  the row off in the UI afterwards is final.

## 0.1.22

- Fix: every RESOLVED lane now gets its Spoolman display name pushed to the AFC panel
  (SET_LANE_FILAMENT_NAME), not just manual picks. An RFID-detected lane previously showed no
  filament name in the panel: the panel only displays a name the helper pushed, and the tag
  resolve path never pushed one.

## 0.1.21

- Fix: the toolchange macro trigger applies IMMEDIATELY again, restoring the original
  semantics: SET_ACTIVE_SPOOL is the first line of every T-macro, so the active spool lands
  slightly BEFORE physical pickup and the prime-tower purge is attributed to the incoming
  filament. (An earlier guard deferred the macro to the park-detector signal, which pushed the
  switch seconds past pickup; re-probed with verbose logs, the park sequence does NOT invoke the
  T0 macro on the current firmware, so the deferral protected against nothing. If a spurious
  macro claim ever does happen, the detector recompute corrects it within one sample.)
- Fix: the carrier watcher samples at 4 Hz instead of 1 Hz (a sample is a handful of in-process
  attribute reads), tightening eject/settle and panel-pick reaction times.

## 0.1.20

- Add: a spool chosen in the AFC panel's own spool-selection dialog now flows everywhere. The
  panel's SET_SPOOL_ID lands on a lane attribute nothing else reads; the carrier watcher picks
  it up and routes it onto the lane's home tool, so the ONE existing pick cascade runs: printer
  screen (color/material), Spoolman widget, location stamp, active-spool recompute. A lane with
  an RFID-identified spool is left alone (the tag stays the source of truth), and the echo of
  the helper's own push-back to AFC is recognised and never loops.

## 0.1.19

- Architecture: spool tracking is BACK IN THE HELPER, whole. Over several iterations the
  Moonraker `print_task_bridge` component had quietly grown from its original narrow job (mirror
  a manual pick's color/material onto the screen) into a parallel reimplementation of the
  helper's core duty: it owned the active spool, the virtual-tool resolution, location tracking,
  and faked the helper's "SH [INFO]" console voice by remotely running `RESPOND` gcodes (each
  line printed twice: command echo + output). The bridge is DELETED. Everything it did now runs
  inside the Klipper-side helper, in-process: screen/lane writes (`print_task_writer`), the
  active-spool rule (`active_spool`), pick/mount/removal detection (`carrier_watch`, a 1s
  reactor poll: no gcode interception, no cross-process races), and the coordinator
  (`tracking`). All console output goes through the helper's own Logs library again
  ("Tracking: ..."), and the toolchange macros call SET_ACTIVE_SPOOL again: the macro trigger
  and the carrier recompute feed ONE idempotent apply, so they cannot fight (the race that
  originally justified the takeover is structurally gone). Everything device-verified is
  retained: home-tool-wins virtual tooling, mid-print write deferral, official-channel guard,
  park-gap suppression, location stamping, UNKNOWN labels, colour naming, manual-pick lane
  labels. Only `spoolman_proxy` (dumb HTTP transport) remains Moonraker-side.

## 0.1.17

- Fix: UID binding (`SH_BIND_CARD_UID` and auto-register) never actually reached Spoolman -- the
  server rejected every write with 400. Spoolman validates a text extra field's VALUE the same
  way as its default (`json.loads` of it must be a string), and the value was sent as a bare
  JSON array. The value is now JSON-encoded twice like the field default, and the read side
  understands that wire form (plus every legacy form) so a binding written by the mobile
  companion app reads back correctly. This path had been broken since the feature was born.
- Fix: `SH_DEBUG SKU=...` raised a TypeError before sending any request -- `lookup_spoolman` had
  grown an unused middle parameter while its command-layer caller kept the original two-argument
  call. Broken since the repo's first commit; the command layer now has its own regression
  tests driving the real resolver.
- Fix: no more transient "Active spool -> none" on every mid-print toolchange. The carrier
  necessarily passes through an unmounted instant between parking one tool and mounting the
  next; nothing extrudes while parked, so that blip was pure churn (a misleading console line
  and an extra Spoolman call per toolchange). A mid-print carrier-empty is no longer pushed;
  a settled carrier-empty (eject, unload, print end) still clears the active spool immediately.
- Change: UID auto-register is now off by default at every layer (the config default already
  was; the internal default matched it). Explicit `SH_BIND_CARD_UID` is unaffected.

## 0.1.16

- Add: every active-spool change (set or clear) is now announced in the Fluidd/Mainsail console
  (`RESPOND MSG="Active spool -> ..."`, matching the existing "SH [INFO]" style the RFID relay
  uses), not just tracked in the daemon's own log. Consumption tracking that only shows up in a
  server-side log file is invisible day-to-day and easy to distrust even when it is working
  correctly; this makes every toolchange's tracking decision visible where the user is actually
  looking.

## 0.1.15

- Fix: 0.1.14's tie-break ("exactly one distinct real spool among claimants, else unknown") was
  still wrong for Snapmaker's own "virtual tooling" (a print's `map_table` option routes its own
  T0/T1 onto whichever physical extruders are actually populated, e.g. `map_table=[[0,2],[1,3]]`
  when only 2 of 4 lanes are in use). Device-confirmed on `unU1jr` running exactly that: E2's
  tracking happened to work (its only borrower, T0, had no spool of its own), but E3 did not --
  its borrower, T1, carries spool 24, which is T1's own STALE identity from T1's native channel
  (1), nothing to do with what is actually on E3. 0.1.14 treated that stale value as a second,
  differing claim and gave up as "ambiguous". Root fix: a physical extruder's OWN tool index
  (tool N's native extruder is N -- how DETECT_SPOOLS populates it before any print-specific
  remap) is that channel's ground truth and now always wins outright when it has a resolved
  spool; a borrowed tool's spool_id is only even considered when the home tool itself has never
  resolved one.

## 0.1.14

- Fix: `resolve_active_spool` picked the FIRST logical tool mapped to the physical extruder in
  `AFC.current_lane`, with no way to tell an unclaimed tie-mate from the one actually carrying
  the physically mounted spool. Device-confirmed on `unU1jr` with a deliberately remapped table
  (T0 and T1 both pointed at the same physical extruder as T3): the carrier had a real,
  RFID-tagged red spool on that extruder, but T0 -- the first claimant, with no spool of its own
  -- won every time, so Spoolman tracked nothing. Now every claimant of a physical extruder is
  considered (`tools_for_physical_extruder`), and only the DISTINCT non-empty spools among them
  count: exactly one is unambiguous and wins even past empty tie-mates; two or more claimants
  disagreeing on a real, different spool is genuinely unresolvable from `current_lane` +
  `spool_id` alone, so that case now resolves to no active spool rather than silently guessing
  and risking a wrong material getting credited for the consumption.

## 0.1.13

- Fix: 0.1.12's active-spool recompute (`_reconcile_active_spool`) skipped itself entirely while
  a print was `printing` or `paused`, so a toolchange mid-print never updated the active spool at
  all -- Spoolman stopped tracking consumption for the whole rest of the print the moment it
  started, recovering only once the print ended. Device-confirmed on `unU1jr`: dry-dock tool
  selects tracked correctly, but the active spool went stale within seconds of a print start and
  stayed that way for the print's whole duration. Root cause: this recompute was written to mirror
  the (correct) deferral used for the `print_task_config` firmware color/material write, but that
  deferral exists only because writing firmware config mid-print can race the hardware's own RFID
  re-read (see 0.1.12's `_should_write_filament_config` fix). Setting the active spool is just a
  Spoolman webhook call with no such race, so it never needed to wait -- it now recomputes on every
  status update regardless of print state, matching the whole point of tracking consumption in the
  first place: while the print is actually running.

## 0.1.12

- Fix: auto-binding a tag UID on SKU match (`card_uids_auto_register`) always failed
  (`Bind failed for spool <id>: HTTP 400`) for every official/SKU-tracked spool (e.g. genuine
  Snapmaker filament). The startup call that defines the `card_uids` Spoolman field
  (`define_card_uids_field`) was itself silently rejected by Spoolman ("Default value is not valid:
  Value is not a string"): a `text` field's `default_value` must decode to a string, but the
  empty-list default was only JSON-encoded once (`"[]"`, which decodes to a list). It is now encoded
  twice so it decodes to the string `"[]"`, matching how every other text-field default (e.g. the
  built-in `nfc_id` field) is shaped. Device-confirmed against a live Spoolman instance.
- Redesign: the active spool is now governed by exactly three rules -- a mounted tool's resolved
  filament is the active spool; no tool on the carrier means no active spool; no spool loaded
  anywhere also means no active spool -- computed by ONE pure, idempotent recompute
  (`resolve_active_spool`, from AFC's `current_lane` plus every tool's last-seen `spool_id`) run
  after every status update that could move either input. This replaces an earlier design where
  "set on mount" and "clear on unmount" were two separately edge-triggered mechanisms, one of them
  a Klipper-side toolchange macro (`base_tools.cfg`'s `SET_ACTIVE_SPOOL` call, now removed) entirely
  independent of this bridge. The two could each miss the other's update, producing a chain of
  symptoms as each was patched in isolation: a `DETECT_SPOOLS` resync (which touches every lane's
  `spool_id` as routine housekeeping, not a deliberate pick) could promote an unmounted lane to
  active; a carrier going back to empty could leave the previous tool's spool stuck active; and
  -- the final and most direct regression -- switching from one mounted tool to another could
  strand the active spool empty entirely, because the new tool's spool_id does not necessarily
  CHANGE at the mount instant (DETECT_SPOOLS may have resolved it long before), so nothing
  re-triggered a "set" once a reliable "clear" was added. Recomputing from current state instead of
  reacting to whichever edge fired closes all of these at once: the result is the same regardless of
  which input (the mount or the spool_id) the printer happens to report first or last. A printer
  without afc-lite (no carrier-mount concept reported at all) is unaffected: every pick stays
  eligible, matching a single-extruder printer where "selected" and "mounted" are the same thing.
  Skipped (not deferred) mid-print, caught up via the existing pending-pick drain when the print ends.
- Fix: `DETECT_SPOOLS` could trip the firmware's own `[print_task_config] filament_config, official
  filament, not configurable!` exception (Klipper exception id 522), which the touchscreen surfaces
  as a scary "System Anomaly" popup -- not a real hardware fault. Root cause: a time-of-check/
  time-of-use race. `DETECT_SPOOLS` kicks off a fresh hardware RFID re-read of the SAME channel it
  is also resolving against Spoolman; the bridge decided a channel was safe to write into using its
  subscription cache (`self.print_task`), which can be stale by up to a poll interval, then queued
  the write -- but by the time that queued write actually reached Klipper, the firmware's own
  RFID re-read had already flipped the channel "official" in the meantime, and the unguarded write
  was rejected. The bridge now re-checks with a FRESH, uncached query immediately before every
  firmware-mutating write, closing the race down to the (much smaller) gap between that query and
  the write landing. Device-confirmed: the exact gcode line Klipper rejected
  (`SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=0 VENDOR="eSun" ...`, no `FORCE`) matched the bridge's
  own `_apply_spool` output byte-for-byte in the printer's `klippy.log`. Also hardened as defense in
  depth: `_run_gcode` no longer lets a klippy-side command rejection raise and abort the rest of
  that tick's reconciliation (other tools, the lane-name push) -- it is caught (via klippy_apis'
  `default` parameter), logged, and the rest of the batch proceeds.

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
  Spoolman spool through a `card_uids` extra field, so even tags we can't fully decode are tracked
  once bound. The field name is `card_uids` on purpose: it is the same field a companion mobile app
  writes, and we read what that app stores however it spelled it, so a binding made there resolves
  here (see 0.1.31). The reverse is not guaranteed: our value is a JSON array inside the JSON string
  Spoolman requires of a text field, which a reader that only splits on commas does not decode, and
  0.1.31 adds a setting for that reader. A spool has two sides / two tags, so
  `card_uids` is a LIST of UIDs; binding appends a UID only if it is not already assigned to any
  spool (no duplicates), and a lookup that hits more than one spool takes the first. A configurable
  resolution order (`card_uids_strategy`, default `spool_id,uid,sku`, first match wins) picks which
  identity wins, and `card_uids_auto_register` (opt-in, OFF by default) adds the current UID to a
  spool the moment it is resolved by SKU, so the next tap is a direct UID match; with it off, tags
  are only ever bound on explicit request (`SH_BIND_CARD_UID`). Binds use GET-merge-PATCH so a
  spool's other extra fields (and its other tag UIDs) are preserved. Re-randomized (DESFire) UIDs
  are never used as a key.
- New `SH_BIND_CARD_UID CHANNEL=<n> SPOOL=<id>` g-code command to bind a channel's current tag UID
  to a spool by hand (recovers from any miss; also what an app-side bind affordance would drive
  later). The `card_uids` Spoolman field is defined automatically on startup.
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
