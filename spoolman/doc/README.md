# Spoolman Bridge

Real-time tracking of your filament usage, with a bit of automation sprinkled in.
This is regular [Spoolman](https://github.com/Donkie/Spoolman) for the U1's multi-tool
system, wired up so the active spool follows the spool you select (or the tag the printer
reads), with zero patches to Klipper or Moonraker.

## What it does

- Detects RFID-tagged spools (via the RFID Spool Reader plugin) and sets the matching
  spool active when a tool is picked.
- Identifies a spool by its tag's hardware UID, not just a decoded id or SKU, so even tags it
  cannot fully decode are tracked once bound (see [Identifying spools by tag](#identifying-spools-by-tag)).
- Makes a manually picked spool the active/tracked spool, and propagates its color, material and
  name to the printer screen and the AFC panel for untagged lanes (see the scenario table below).
  A spool can be picked from the Spoolman panel, the Spoolman widget, or straight from the **AFC
  panel's own spool selector** -- all three land in the same place. On a multi-tool printer with
  AFC, the active spool always tracks whichever lane is actually mounted on the carrier: the
  switch lands at the toolchange itself (before the prime purge), an eject clears it, and a
  resync that re-touches every lane (e.g. `DETECT_SPOOLS`) never makes an unmounted lane active.
- Spools survive restarts and reboots, tagged or not. A tagged lane's tag data is kept on the
  printer and read back at startup, then resolved again; a manual pick on an untagged lane is kept
  the same way and replayed, re-labelling the screen and AFC panel on its own. A tag that appears on
  an untagged lane in the meantime wins over the older pick.
- Optionally records which printer a spool is on, in Spoolman's location field (see
  [Where a spool is](#where-a-spool-is-location-tracking)).
- Handles the U1's 32 virtual-tool system for jobs that need more than 4 filaments.
- Logs filament length used per print automatically.

## What it does not do

- It does not write your tags. (It can bind a tag's UID to a _Spoolman spool_, which writes a
  field on the spool in Spoolman, but it never alters the tag itself.)
- It does not invent `spool_id`s: a spool must already exist in your Spoolman instance.

## Requirements

### A working Spoolman instance

Spoolman is a web app. It does not run on the printer; host it elsewhere (a NAS, a Pi, a
home server). The easiest path is the official [Docker install](https://github.com/Donkie/Spoolman/wiki/Installation#docker-install);
a [standalone install](https://github.com/Donkie/Spoolman/wiki/Installation#standalone-install)
also works.

### Properly tagged spools

The bridge recognizes a spool by the `spool_id` on its tag. Snapmaker's own tags are
encrypted, so for those it falls back to the `SKU` and matches it against the
**Article Number** field of a filament in your Spoolman instance. Half-kilo and one-kilo
spools of the same color have different SKUs.

![Snapmaker SKUs configured in Spoolman](images/snapmaker_skus.png)

The SKU trick works with any tag the U1 can read that carries such a property.

## Setup

1. Install this plugin from the Bespok3d store. It pulls in the **RFID Spool Reader**
   automatically.
2. When prompted, enter your **Spoolman server address** whole, protocol and all: a name over https
   (`https://spoolman.example.org`) or a host and port over http (`http://192.168.1.50:8000`). If you
   type just the host and port, the app asks which of the two to use. That is the only required
   setting.
3. The bridge starts syncing immediately; no config files to edit.

### Show the Spoolman panel in Fluidd

Open the **3-dot menu** on Fluidd's main page and choose **Adjust dashboard layout**.

![Fluidd 3-dot menu](images/3dots.png)
![Fluidd dashboard layout](images/layout.png)

Find the Spoolman panel and make sure it is enabled. Its position depends on how you have
arranged your dashboard, so look around for it.

![Spoolman panel in Fluidd](images/spoolman.png)

## Identifying spools by tag

A spool is matched to your Spoolman inventory by a chain of identities, tried in order, first
match wins. By default that order is `spool_id, uid, sku`:

- **spool_id**: an explicit Spoolman spool id encoded on the tag.
- **uid**: the tag's hardware UID, bound to a spool in Spoolman. This is how a tag the printer
  cannot fully decode still gets tracked: once its UID is bound to a spool, every later tap
  resolves straight to that spool.
- **sku**: the tag's SKU matched against a filament's **Article Number** in Spoolman.

You can change the order with the **Spool resolution order** setting. A bound UID is stored on the
Spoolman spool in a `card_uids` field (a list, since a spool can carry two tags). That is the same
field a companion mobile app writes, and we read what that app stores however it spelled it (any
letter case, `:` or `-` between the bytes, the bytes spaced apart, a leading `0x`, a list or one
comma-separated value), so a binding made there resolves here. **How tag UIDs are stored in
Spoolman** picks what we write back: `array` (the default, a proper list) or `comma_separated` for
an app on the other side that cannot read a list. A UID we store is always bare lowercase hex, and
either form is read back correctly here. Bindings are
additive and de-duplicated, and a spool's other fields are preserved. Re-randomizing tags
(DESFire) are never used as a key.

- **Auto-bind** (off by default): turn on **Auto-bind tag UID on SKU match** and the first time a
  spool is resolved by SKU its current tag UID is written onto that spool, so the next tap is a
  direct UID match. With it off, UIDs are only ever bound when you ask.
- **Manual bind:** run `SH_BIND_CARD_UID CHANNEL={0..3} SPOOL=<id>` to bind the tag currently on a
  lane to a specific Spoolman spool. Handy to recover from a miss, or to register a tag up front.

Both of these need the lane to be reporting a **stable** tag UID. Most tags have one. A tag that
re-randomizes its UID on every tap does not, and binding it would be binding a number that never
comes back, so `SH_BIND_CARD_UID` refuses with `No stable tag UID on channel <n>` instead. Matching
by **Article Number** works either way (see
[A tag Spoolman knows nothing about](#a-tag-spoolman-knows-nothing-about)).

**Verified on a printer.** On a Snapmaker U1 running stock firmware, with two genuine Snapmaker
spools, the firmware exposes each card's own hardware UID, and binding that UID to a spool in
Spoolman makes the lane resolve by the card itself. With the filament's **Article Number** cleared
in Spoolman, so no SKU match was possible, unloading and reloading the spool still resolved to the
bound spool. Bind with `SH_BIND_CARD_UID CHANNEL=<lane> SPOOL=<spoolman id>`; the lane's own log
line prints the card UID it is holding, so you can read it off there. Two spools on one firmware
were tested: that is what those spools do, not a claim about every Snapmaker spool, and nothing is
claimed about other vendors' tags.

## Where a spool is (location tracking)

Optionally the bridge records which printer a spool is on, in Spoolman's **location** field, so
your Spoolman inventory shows where each spool physically lives.

- Turn on **Track spool location in Spoolman** (off by default).
- Fill **This printer's name** with what you want shown as the spool's location (e.g. `unU1jr`);
  location tracking is inactive until a name is set.

A spool loaded on a lane gets this printer's name written to its location; unloading it clears the
location. This works for both tagged and manually picked spools. A manually picked spool on an
untagged lane is released (and its location cleared) the moment its filament is physically pulled,
driven by the printer's filament sensor, so it never goes stale.

**Per-printer vs shared settings.** In Bespok3d, **This printer's name (Spoolman location)** is a
per-printer setting (manifest `scope: "printer"`): each printer keeps its own value, so every
printer writes its own location. The plugin's other settings (server address, selection mode, log
level, resolution order, the auto-bind toggle, and the location toggle) are shared across all your
printers by default (`scope: "global"`). You can still override any of them for a single printer
from the plugin's Config tab.

## How filament data flows (scenarios and gotchas)

Two different pieces of data take two different paths, and knowing which is which explains
every behavior below.

- **Name** always comes from Spoolman, resolved live from the lane/tool's `spool_id`. It shows
  the instant a spool is picked, with no help from the print task. Every resolved lane (tagged or
  picked) also gets its display name pushed to the AFC panel, and Fluidd 1.37.2+ shows it there
  out of the box.
- **Color, material, and vendor** always come from the firmware's print task (per physical
  extruder). Both the AFC panel and the touchscreen read them from there. The helper writes a
  Spoolman pick back into the print task in-process, using the firmware's own
  `SET_PRINT_FILAMENT_CONFIG` command (no patches, persisted like a screen edit).

### Scenarios

Each line is: **what you have** then how the name behaves, then how color/material behave.

- **No tags, no Spoolman:** no name; color/material are whatever the print task already holds
  (screen-set or default).
- **RFID-tagged spool:** name from the tag's spool; color/material written by the firmware from
  the tag. The bridge never touches a tagged lane.
- **Untagged, set on the screen:** no name (there is no spool); color/material come from the screen.
- **Untagged, picked in Spoolman:** name shows live from the pick; the bridge writes color/material
  so the screen and AFC agree, and makes the picked spool the active/tracked spool.
- **Untagged, loaded but not picked:** shown as `UNKNOWN` (present but unidentified) rather than
  empty, in the helper's logs and `DUMP_SPOOLS`.
- **Untagged, picked mid-print:** name and the active spool update live; only the firmware
  color/material write is deferred to when the print leaves printing/paused (the firmware can
  reject it mid-print).
- **Spool cleared for a tool:** name clears, and the lane's color/material are reset to empty on the
  screen and AFC. `CLEAR_ALL_SPOOLS` clears every lane, including RFID-tagged ones.
- **Untagged filament pulled out:** a manually picked spool whose filament you physically remove is
  released automatically (no RFID event needed) -- name, color/material, and Spoolman location all
  clear.
- **Spoolman server unreachable:** name stays last-known; color/material unchanged; the bridge
  logs and moves on.

### Gotchas

- The bridge only ever writes **untagged** lanes, and without `FORCE`, so it can never override
  an RFID tag. The tag is always the source of truth.
- It acts only on the 4 physical lanes (the tools that map to physical extruders 0 to 3 in the
  U1's virtual-tool table). A virtual tool with no physical mapping is skipped.
- A spool picked while a print is running or paused is **deferred**: the name updates live, and
  the color/material land the moment the print leaves printing/paused. This is deliberate, since
  the firmware can reject the untagged write mid-print.
- After installing or updating, Moonraker restarts and the AFC frontends' service worker may
  serve stale JavaScript. If a panel looks wrong, hard-refresh the browser.

## Macros and commands

These are added by the plugin and can be run from the Fluidd/Mainsail console or your
slicer:

- `SET_ACTIVE_SPOOL TOOL={0..3}` manually sets the selected tool as the active spool. On a
  multi-tool printer with AFC this is rarely needed: the active spool already follows whichever
  tool is mounted automatically (see above).
- `READ_FILAMENT_ID TOOL={0..3}` reads the selected lane's spool tag, if any.
- `GET_FILAMENT_ID TOOL={0..3}` prints the currently read tag for the selected lane.
- `SH_BIND_CARD_UID CHANNEL={0..3} SPOOL=<id>` binds the tag currently on a lane to a Spoolman
  spool, so the next tap of that tag resolves to it directly. Needs the lane to be reporting a stable
  tag UID; it says so when there is none.
- `CLEAR_ACTIVE_SPOOL` clears the current active spool.
- `CLEAR_ALL_SPOOLS` clears the selected spool for every tool, including RFID-tagged lanes.
- `DETECT_SPOOLS` re-detects every loaded spool. It is the last line of defence when something did not
  detect properly: it forces the detection again without you pulling the spool off the printer and
  putting it back on.
- `DUMP_SPOOLS` prints what the bridge knows about each lane (labelled `T0:`..`T3:`; an empty lane
  reads `empty`, a manually assigned one shows the spool).
- `SH_CONFIG MODE=<auto|manual> LOGS=<level>` changes the module's behavior at runtime
  without a restart.
- `SH_DEBUG [SKU=<sku>]` prints the current configuration; with a `SKU` it shows the spool
  that would be tracked for that SKU.

## Configuration

- **Spoolman server address** (required): where your Spoolman instance lives.
- **Selection mode** (`auto` or `manual`): in `auto`, the RFID tag is the source of truth and
  falls back to a manually selected spool; in `manual`, the manual selection drives and tags are
  the fallback.
- **Log level** (`error` < `info` < `warn` < `verbose` < `debug`): how chatty the logs are.
- **Spool resolution order** (default `spool_id,uid,sku`): the identity chain used to match a tag
  to a Spoolman spool, first match wins. See [Identifying spools by tag](#identifying-spools-by-tag).
- **Auto-bind tag UID on SKU match** (off by default): when a spool is found by SKU and has no UID
  yet, write the current tag's UID onto it so the next tap is a direct match.
- **Track spool location in Spoolman** (off by default): write this printer's name into a loaded
  spool's location field, cleared on unload. See [Where a spool is](#where-a-spool-is-location-tracking).
- **This printer's name (Spoolman location)**: leave empty to use this printer's own name
  automatically; set it to override.
- **Show my hand-picked spools in Snapmaker Orca** (on by default): see below.

## Show my hand-picked spools in Snapmaker Orca

A setting of this plugin, on by default, for spools you pick yourself instead of reading them off a
tag. On, the printer tells slicers such a spool is `Generic` plus its material, because those are the
only filament names Snapmaker Orca can match: your lanes then appear under **Machine Filament**
instead of the list coming up empty. The printer's own screen shows `Generic` too.

Everything else keeps the real brand: your spool in Spoolman, the name on the AFC panel's lane, and
what this plugin tracks and reports. Spoolman spools by Snapmaker are never renamed.

A spool you had already picked keeps the brand it was filed under until you pick it again, because
the printer only files a spool when the choice changes.

Off, the brand recorded in Spoolman is sent to the slicer, and Orca lists nothing for anything but
Snapmaker spools.

Change it from the plugin's settings in the Bespok3d app; the printer picks it up on the next Klipper
restart.

## Limits worth knowing about

Two things here regularly look like bugs and are not. Both sit in parts of the chain this plugin
does not own.

### The slicer only syncs to filaments it already ships

Snapmaker Orca and OrcaSlicer have a **Sync Filament Information** button that reads what is loaded
in each lane and picks a slicer filament to match. It reads the brand, the material and the colour
from the printer. What it is allowed to pick from is the slicer's own **built-in** filament list: it
will never select a filament you created yourself, however you name it.

So a lane loaded with something the slicer ships (any Snapmaker filament, the Polymaker PLA family,
and the generics) syncs to that exact entry. Anything else lands on `Generic <material>` with the
right colour, because that is the nearest thing the slicer has. A lane it cannot place at all keeps
whatever filament the project already had, and only its colour is updated.

That is the slicer's own behaviour, on the slicer's side, and nothing Bespok3d installs changes it.
If you want your own filament used, pick it by hand after syncing, or build your custom filament on
top of the matching built-in one so the print settings are right either way.

### A tag Spoolman knows nothing about

Spools come back after a reboot on their own, tagged or not, so this section is only about a tag that
matches nothing in your Spoolman.

On a tagged lane the tag is the source of truth: its data is kept on the printer, read back at
startup and resolved again. While it resolves, the lane comes back with the right spool and there is
nothing to do. When it resolves to nothing, the lane comes back showing what the tag itself says but
with no Spoolman spool behind it, so nothing is tracked against it. A spool you pick by hand for that
lane is not kept either, because on a tagged lane the tag is what persists, so you pick it again
after the next reboot.

Give the tag something to land on, once, and it stops. Two routes, either of which is enough.

- **Fill in the Article Number.** Put the SKU the tag reports into the filament's **Article Number**
  in Spoolman and the SKU step of the chain finds it by itself, on every reboot. This is the route
  for a tag that reports a SKU, which includes Snapmaker's own spools.
- **Bind the tag to the spool.** `SH_BIND_CARD_UID CHANNEL={0..3} SPOOL=<id>` ties the tag currently
  on a lane to one Spoolman spool. The binding is stored on the Spoolman spool, so it survives
  reboots and follows the spool to any lane. It needs the lane to be reporting a stable tag UID, and
  it tells you when there is none.

Whenever a lane looks wrong for any other reason, `DETECT_SPOOLS` is the last line of defence: it
re-reads every lane and resolves them again, so you can force the detection without rebooting and
without pulling the spool off the printer and putting it back on. It is the retry, not the cure: if
Spoolman genuinely has no filament matching that tag, `DETECT_SPOOLS` fails exactly the same way, and
the two fixes above are what stop it for good.

## Troubleshooting

- **Spool never goes active:** check the server address and that the printer can reach the
  Spoolman host and port.
- **No usage logged:** usage is recorded per print; confirm a spool is active before the
  print starts.
- **`filament lookup error for sku <n>: null` / `Cannot find spools for sku: <n>`:** Spoolman
  answered, and it has no filament whose **Article Number** is that SKU. Fill that SKU into the
  filament's **Article Number** in Spoolman. (`SH_BIND_CARD_UID` is the alternative.) See
  [A tag Spoolman knows nothing about](#a-tag-spoolman-knows-nothing-about).
- **`Tool T<n> loaded with UNKNOWN filament (unassigned, no tag)`:** that lane has filament and no
  tag, and nothing has been picked for it yet. Pick a spool for it in the Spoolman panel; that pick
  is remembered across reboots.
- **`Clearing spool from extruder <n>`:** that lane is empty. Expected.
- `SH_DEBUG SKU=<sku>` shows what a given SKU resolves to right now.
