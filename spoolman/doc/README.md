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
  picked) also gets its display name pushed to the AFC lane, where a web interface that reads the
  lane's `filament_name` shows it. The name is the exact filament description the printer already
  publishes for that lane to the slicer, brand, material and sub-type ("SUNLU PETG Basic"), so the
  lane card, Snapmaker Orca's **Device** tab and the preset the slicer matches all say one thing,
  and everything you already do to control that string controls the card too.
- **Color, material, and vendor** always come from the firmware's print task (per physical
  extruder). Both the AFC panel and the touchscreen read them from there. The helper writes a
  resolved Spoolman spool back into the print task in-process, using the firmware's own
  `SET_PRINT_FILAMENT_CONFIG` command (no patches, persisted like a screen edit). A tagged lane
  is included in that write only when **Spoolman pick overrides a tagged lane** is on; off, the
  firmware keeps what the tag filed.

### Scenarios

Each line is: **what you have** then how the name behaves, then how color/material behave.

- **No tags, no Spoolman:** no name; color/material are whatever the print task already holds
  (screen-set or default).
- **RFID-tagged spool:** name from the tag's Spoolman spool, so the AFC card already shows brand,
  material and sub-type. Color/material stay what the firmware filed from the tag, unless
  **Spoolman pick overrides a tagged lane** is on, in which case the Spoolman record is written
  there too (that is what Snapmaker Orca reads).
- **Untagged, set on the screen:** no name (there is no spool); color/material come from the screen.
- **Untagged, picked in Spoolman:** name shows live from the pick; the bridge writes color/material
  so the screen and AFC agree, and makes the picked spool the active/tracked spool. `DETECT_SPOOLS`
  keeps that pick whether you chose it on the printer or in the AFC panel: a lane with no tag is
  not cleared just because the re-read found no RFID, and color and material are written back if
  the firmware re-read blanked them.
- **Untagged, loaded but not picked:** shown as `UNKNOWN` (present but unidentified) rather than
  empty, in the helper's logs and `DUMP_SPOOLS`.
- **Picked or identified mid-print:** name and the active spool update live; only the firmware
  color/material write is deferred to when the print leaves printing/paused. This holds however the
  spool was identified, by hand or by tag.
- **Spool cleared for a tool:** name clears, and the lane's color/material are reset to empty on the
  screen and AFC. `CLEAR_ALL_SPOOLS` clears every lane, including RFID-tagged ones.
- **Untagged filament pulled out:** a manually picked spool whose filament you physically remove is
  released automatically (no RFID event needed) -- name, color/material, and Spoolman location all
  clear.
- **Spoolman server unreachable:** name stays last-known; color/material unchanged; the bridge
  logs and moves on.

### Gotchas

- The bridge never writes an official tagged lane unless **Spoolman pick overrides a tagged lane**
  is on. On, it sends `FORCE=1` so the firmware accepts the Spoolman brand, material and sub-type
  on a tagged lane (without that flag it raises `official filament, not configurable`). Off, the
  tag stays the source of truth for color, material and sub-type; the AFC name still comes from
  Spoolman.
- It acts only on the 4 physical lanes (the tools that map to physical extruders 0 to 3 in the
  U1's virtual-tool table). A virtual tool with no physical mapping is skipped.
- Any spool change while a print is running or paused is **deferred**: the name updates live, and
  the color/material land the moment the print leaves printing/paused. This is by design: the
  printer resets its pressure advance every time it accepts that write, so a write landing
  mid-print would change how the running print extrudes (and the firmware can reject it outright).
  Only the last change per lane is sent when the print ends, and it is dropped if the printer
  already carries it.
- After installing or updating, Moonraker restarts and the AFC frontends' service worker may
  serve stale JavaScript. If a panel looks wrong, hard-refresh the browser.

## Buttons instead of typing

You do not have to type any of this. Every command below that has to do with a spool sits behind a
button in the AFC panel of Fluidd and Mainsail: three at the top of the unit (clear all spools,
detect spools, clear active) and a bar of three under every lane (add spool, update spool data, link
spool). The buttons come with the Bespok3d Fluidd or Mainsail plugin, they only appear on a printer
running this plugin, because they are its commands, and they are the same buttons in the same order
in both. The console commands stay for anyone who prefers them, and for a slicer.

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
- `SH_APPLY_TAG_TO_SPOOL CHANNEL={0..3} SPOOL=<id>` writes what the lane's tag says onto a Spoolman
  spool you picked (brand, material, sub-type, colour, article number) and binds the tag to it. The
  lane starts reporting that spool straight away.
- `SH_ADD_SPOOL_FROM_TAG CHANNEL={0..3}` creates a new Spoolman spool from what the lane's tag says,
  binds the tag to it and puts the lane on it. Asking for one by hand works whether or not
  **Create spools from tags** is on.
- `CLEAR_ACTIVE_SPOOL` clears the current active spool.
- `CLEAR_ALL_SPOOLS` clears the selected spool for every tool, including RFID-tagged lanes.
- `DETECT_SPOOLS` re-detects every loaded spool. It is the last line of defence when something did not
  detect properly: it forces the detection again without you pulling the spool off the printer and
  putting it back on. Tagged lanes resolve from RFID and replace the previous pick. Untagged lanes
  that already have a Spoolman pick, chosen on the printer or in the AFC panel, keep that pick
  (color and material are written back if the firmware re-read blanked them). Untagged lanes with
  no pick are left as they are.
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
- **Make a spool out of a tag Spoolman does not have** (`register_from_tag`, on by default): when a
  tag matches nothing and none of your spools came close, create it in Spoolman from what the card
  carries and bind the card to it. It also covers writing a card onto a spool you picked
  (`SH_APPLY_TAG_TO_SPOOL`). Off, a tag Spoolman does not have is only reported. See
  [A tag Spoolman knows nothing about](#a-tag-spoolman-knows-nothing-about).
- **Track spool location in Spoolman** (off by default): write this printer's name into a loaded
  spool's location field, cleared on unload. See [Where a spool is](#where-a-spool-is-location-tracking).
- **This printer's name (Spoolman location)**: leave empty to use this printer's own name
  automatically; set it to override.
- **Spoolman pick overrides a tagged lane** (off by default): who wins when a lane has both an RFID
  tag and a spool in Spoolman. Off, the tag wins on the firmware config the slicer reads. On, the
  Spoolman record is written there too, so a tagged spool with `variant` Silk is announced as Silk,
  not whatever the firmware had filed. Coming from the extended firmware, if your spool profiles
  depend on Spoolman: turn this on, or a tagged lane goes by what is written on the tag and your
  Spoolman profile is ignored. On sends `FORCE=1`, because the firmware otherwise refuses to change
  a tagged lane.
- **Where the sub-type comes from** (`sub_type,variant,name_inferred` by default): the order the
  three sub-type sources are tried in, first one with a value wins. Reorder them to change which
  wins, drop one to stop reading it at all. See
  [How a hand-picked spool shows up in Snapmaker Orca](#how-a-hand-picked-spool-shows-up-in-snapmaker-orca).

## How a hand-picked spool shows up in Snapmaker Orca

A spool you pick yourself is announced to the slicer as its brand, its material and a sub-type.
Spoolman has no sub-type field, so the sub-type can come from three places,
tried in the order set by **Where the sub-type comes from**. The first one with a value wins:

| Source          | Where it reads from                                                                      |
| --------------- | ---------------------------------------------------------------------------------------- |
| `sub_type`      | a `sub_type` (or `subtype`) field you added in Spoolman, on the spool or on the filament |
| `variant`       | the `variant` field the extended firmware writes in Spoolman                             |
| `name_inferred` | the filament name, which is us working it out instead of you filing it                   |

`name_inferred` reads a sub-type word anywhere in the name, in any capitals: `RAPID PETG Blue` gives
`Rapid`. The words it knows are the standard ones, Basic, Rapid, HF, Matte, Silk, High Speed and the
rest. A Shore hardness counts too, at any value, so `TPU 82a Black` gives `82A` and `Hard TPU 63D`
gives `63D`. When none of the three has anything, the sub-type is `Basic`, the name Snapmaker gives
its own base line, so a spool with nothing filed reads `SUNLU TPU Basic` and your preset needs the
`Basic` too. It is the same fallback a tag with no sub-type already gets.

Snapmaker Orca lists the spool under **Machine Filament** when one of your filament presets is named
exactly what the printer reports, capitals included. The name the printer filed is shown on the slot
in Orca's **Device** tab: read it there and name a preset the same.

Plain OrcaSlicer matches differently, and how it should behave is being worked out with one of its
developers. Everything here is about Snapmaker Orca.

A lane with a tag in it always gets its AFC name from Spoolman. The firmware config the slicer
reads stays what the tag filed, unless **Spoolman pick overrides a tagged lane** is on: then the
same Spoolman sub-type (from `sub_type`, `variant`, or the name) is filed there too, so Fluidd
and Snapmaker Orca stop disagreeing. The RFID Spool Reader's own doc covers naming tags.

A spool you had already picked keeps the name it was filed under until you pick it again, because
the printer only files a spool when the choice changes.

## Limits worth knowing about

Two things here regularly look like bugs and are not. Both sit in parts of the chain this plugin
does not own.

### Snapmaker Orca only matches a preset named exactly right

Snapmaker Orca's **Sync Filament Information** button reads what is loaded in each lane and looks for
a filament preset with that exact name, capitals included, among the ones it ships and the ones you
made yourself. A lane whose name matches nothing is listed nowhere, with no error and nothing under
**Machine Filament** for it.

That is Snapmaker Orca's own behaviour and nothing Bespok3d installs changes it. If a lane comes up
missing, read the name on the **Device** tab and name a preset exactly that.

### A tag Spoolman knows nothing about

Spools come back after a reboot on their own, tagged or not, so this section is only about a tag that
matches nothing in your Spoolman.

On a tagged lane the tag is the source of truth: its data is kept on the printer, read back at
startup and resolved again. While it resolves, the lane comes back with the right spool and there is
nothing to do.

The lane's own bar in the AFC panel is where this is normally sorted out: **update spool data**
writes the tag onto a spool you pick, **link spool** ties the tag to one, and **add spool** makes a
new one. What follows is what happens on the console, and the commands the buttons send.

When it matches nothing, the console says so and then lists what your Spoolman does hold that looks
like the tag, five at most: only spools of the same material are offered, and the same brand and the
same colour are what put one at the top of the list. Archived spools are never offered. If one of
them is the spool in your hand, tell
the printer so, with `SH_APPLY_TAG_TO_SPOOL CHANNEL={0..3} SPOOL=<id>`: what the card says is written
onto that spool and the card is bound to it, so the next tap lands straight on it.

If nothing of yours came close, the spool is made for you. The bridge creates it in Spoolman from
what the card carries (brand, material, sub-type, colour, article number) and binds the card to it,
so the lane starts tracking at once and the next tap resolves directly. Nothing is invented: a
diameter and a density are required by Spoolman and are not on the card, so they are copied from your
own filaments of that material. If you own none of that material yet, nothing is created and the
console says which material to add one of first. Set `register_from_tag: false` in
`[spoolman_helper]` to turn creating and writing off.

The two manual routes still work and still stop a tag from missing in the first place.

- **Fill in the Article Number.** Put the SKU the tag reports into the filament's **Article Number**
  in Spoolman and the SKU step of the chain finds it by itself, on every reboot. This is the route
  for a tag that reports a SKU, which includes Snapmaker's own spools.
- **Bind the tag to the spool.** `SH_BIND_CARD_UID CHANNEL={0..3} SPOOL=<id>` ties the tag currently
  on a lane to one Spoolman spool. The binding is stored on the Spoolman spool, so it survives
  reboots and follows the spool to any lane. It needs the lane to be reporting a stable tag UID, and
  it tells you when there is none.

Whenever a lane looks wrong for any other reason, `DETECT_SPOOLS` is the last line of defence: it
re-reads every lane and resolves the tagged ones again, so you can force the detection without
rebooting and without pulling the spool off the printer and putting it back on. A lane with no tag
keeps the spool you picked by hand, on the printer or in the AFC panel, and a lane with no pick
keeps the color and material already on the screen. It is the retry, not the cure: if Spoolman
genuinely has no filament matching that tag, `DETECT_SPOOLS` fails exactly the same way, and the
two fixes above are what stop it for good.

## Verifying on a real printer

`spoolman/tests_invitro/` is a pytest suite that runs against a live printer and checks the plugin
end to end: the AFC card names, the firmware filament fields the slicer reads, manual picks, the
`spoolman_overrides_tag` switch, and `SH_DETECT_SPOOLS`. It never runs in the repo gate; you point
it at a printer explicitly:

```sh
B3D_HIL_HOST=<printer-address> bash scripts/invitro.sh
```

That runs the read-only tier: it observes the printer and changes nothing. Adding
`B3D_INVITRO_MUTATE=1` also runs the mutating tier, which picks spools, flips the priority option
(editing the installed config over SSH and restarting Klipper), and re-reads tags. Every mutating
test restores what it changed, but only run it on a printer that is idle and yours to disturb; the
suite refuses to mutate a printer that is printing.

The SSH steps use the firmware's stock root login by default; set `B3D_HIL_SSH_USER` and
`B3D_HIL_SSH_PASS` if the printer's credentials differ. The expectations are computed by the
plugin's own composer over the live Spoolman records, so a red test means the printer disagrees
with what the shipped code promises, not with a hardcoded fixture.

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
