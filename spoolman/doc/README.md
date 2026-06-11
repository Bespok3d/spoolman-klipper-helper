# Spoolman Bridge

Real-time tracking of your filament usage, with a bit of automation sprinkled in.
This is regular [Spoolman](https://github.com/Donkie/Spoolman) for the U1's multi-tool
system, wired up so the active spool follows the tool that is printing, with zero patches
to Klipper or Moonraker.

## What it does

- Detects RFID-tagged spools (via the RFID Spool Reader plugin) and sets the matching
  spool active when a tool is picked.
- Handles the U1's 32 virtual-tool system for jobs that need more than 4 filaments.
- Logs filament length used per print automatically.
- Propagates a manually picked spool's color and material to the printer screen and the AFC
  panel for untagged lanes (see the scenario table below).

## What it does not do

- It does not write tags.
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
2. When prompted, enter your **Spoolman server address** (`host` or `host:port`; the port
   defaults to `7912` if you omit it). That is the only required setting.
3. The bridge starts syncing immediately; no config files to edit.

### Show the Spoolman panel in Fluidd

Open the **3-dot menu** on Fluidd's main page and choose **Adjust dashboard layout**.

![Fluidd 3-dot menu](images/3dots.png)
![Fluidd dashboard layout](images/layout.png)

Find the Spoolman panel and make sure it is enabled. Its position depends on how you have
arranged your dashboard, so look around for it.

![Spoolman panel in Fluidd](images/spoolman.png)

## How filament data flows (scenarios and gotchas)

Two different pieces of data take two different paths, and knowing which is which explains
every behavior below.

- **Name** always comes from Spoolman, resolved live from the lane/tool's `spool_id`. It shows
  the instant a spool is picked, with no help from the print task.
- **Color, material, and vendor** always come from the firmware's print task (per physical
  extruder). Both the AFC panel and the touchscreen read them from there. A passive Moonraker
  observer is the only thing that writes a Spoolman pick back into the print task, using the
  firmware's own `SET_PRINT_FILAMENT_CONFIG` command (no patches, persisted like a screen edit).

### Scenarios

Each line is: **what you have** then how the name behaves, then how color/material behave.

- **No tags, no Spoolman:** no name; color/material are whatever the print task already holds
  (screen-set or default).
- **RFID-tagged spool:** name from the tag's spool; color/material written by the firmware from
  the tag. The bridge never touches a tagged lane.
- **Untagged, set on the screen:** no name (there is no spool); color/material come from the screen.
- **Untagged, picked in Spoolman:** name shows live from the pick; the bridge writes color/material
  so the screen and AFC agree.
- **Untagged, picked mid-print:** name shows live; color/material are applied when the print ends.
- **Spool cleared for a tool:** name clears; color/material are left as-is (the bridge does not
  wipe the print task).
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

- `SET_ACTIVE_SPOOL TOOL={0..3}` sets the selected tool as the active spool.
- `READ_FILAMENT_ID TOOL={0..3}` reads the selected lane's spool tag, if any.
- `GET_FILAMENT_ID TOOL={0..3}` prints the currently read tag for the selected lane.
- `CLEAR_ACTIVE_SPOOL` clears the current active spool.
- `CLEAR_ALL_SPOOLS` clears the selected spool for every tool.
- `DETECT_SPOOLS` re-detects the loaded spools. A handy reset when spool state looks wrong.
- `DUMP_SPOOLS` prints what the bridge knows about the loaded spools (detail depends on the
  log level).
- `SH_CONFIG MODE=<auto|manual> LOGS=<level>` changes the module's behavior at runtime
  without a restart.
- `SH_DEBUG [SKU=<sku>]` prints the current configuration; with a `SKU` it shows the spool
  that would be tracked for that SKU.

## Configuration

- **Spoolman server address** (required): where your Spoolman instance lives.
- **mode** (`auto` or `manual`): in `auto`, the RFID tag is the source of truth and falls
  back to a manually selected spool; in `manual`, the manual selection drives and tags are
  the fallback.
- **logging** (`error` < `info` < `warn` < `verbose` < `debug`): how chatty the logs are.

## Troubleshooting

- **Spool never goes active:** check the server address and that the printer can reach the
  Spoolman host and port.
- **No usage logged:** usage is recorded per print; confirm a spool is active before the
  print starts.
