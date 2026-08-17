# b3-builder

[![licence](https://img.shields.io/badge/licence-AGPL--3.0-blue)](LICENSE)
[![release](https://img.shields.io/github/v/release/Bespok3d/b3-builder)](https://github.com/Bespok3d/b3-builder/releases)
[![version](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FBespok3d%2Fb3-builder%2Fmain%2Fpackage.json&query=%24.version&label=version&color=blue)](package.json)
![built with](https://img.shields.io/badge/built%20with-Node.js-informational)
![stock firmware](https://img.shields.io/badge/stock%20firmware-no%20flashing-brightgreen)

b3-builder turns plugin source directories into installable Bespok3d packages. You point it at a
plugin (or a whole repo of plugins) and it produces a `.b3` archive ready to install on a printer,
plus the catalog metadata a plugin index needs to list it.

One build engine, two faces:

- a **CLI** (`b3-builder build`) for building on your own machine
- a reusable **GitHub Action** that gives a plugin repo its whole release pipeline from one `uses:`

The tool is organization-agnostic: your publisher identity (repo slugs, list names, tokens) is
always passed in. Nothing Bespok3d-specific is baked into the builder, so anyone can publish
plugins with it.

**Writing a plugin?** Start at [doc/README.md](doc/README.md). That is the full plugin
documentation: what a plugin is made of, the six kinds, signing, the release Action, channels,
local testing and publishing. [doc/plugin-zero-to-hero.md](doc/plugin-zero-to-hero.md) walks one
plugin from an empty directory to a published release.

## What a plugin looks like

```text
my-plugin/
  manifest.json      what it is, what it needs, how it installs
  files/             the payload, mirroring where files land on the printer
  doc/README.md      optional user-facing docs, linked from the catalog entry
  tests/run.sh       optional test script the GitHub Action runs before releasing
```

The builder computes every file checksum and mode at pack time. You never hand-write a file list.
The full manifest contract (fields, install classes, services) is documented in the Bespok3d
package format guide, which publishes alongside the rest of the Bespok3d docs; until then the
quick start manifest below is a complete working reference.

## Plugin kinds

| Kind | You ship | You declare | Example |
| --- | --- | --- | --- |
| Config files and patches | Klipper/Moonraker configs, macros, patches, static files | nothing, just `files/` | cpu-temp |
| Python | Python code with pip dependencies | `requirements.txt` and/or `klipper_requirements.txt` at the plugin root | spoolman |
| Go binary | a Go program cross-compiled for the printer (arm64) | a `"class": "go"` bake entry | prometheus-exporter |
| Prebuilt download | an upstream release repackaged (sha256-pinned) | a `"class": "download"` bake entry | tailscale |
| Native C program | C source cross-built in Docker (arm64) | a `"class": "docker-c"` bake entry | u1-hw-camera |
| Kernel module | a `.ko` built for the exact printer kernel | a `"class": "docker-ko"` bake entry | tun-module |

The examples are Bespok3d-published plugins ("U1" is the Snapmaker U1, the first printer Bespok3d
supports).

Only the first kind needs nothing beyond its files. Python plugins declare their dependencies in
requirements files, and the last four kinds declare how their payload is built from source in the
manifest's `bake` list (see the bake reference below). Every kind past the first is produced with
the `--bake` flag.

## Quick start

You have some config files and want them on your printer as a proper plugin.

**1. Install the builder** (needs Node.js 20 or newer). In any directory, an empty one is fine:

```sh
npm install github:Bespok3d/b3-builder
```

This puts the `b3-builder` command in `./node_modules/.bin`, where `npx b3-builder` finds it. Do
not use `npm install -g` with the git URL: npm has a long-standing bug where a global install
from git skips the package's build step and the command never appears. A proper global install
(`npm install -g @bespok3d/builder`) arrives when the package is published to npm at the public
release. While the repo is private, both this install and the `npx` form require GitHub access to
`Bespok3d/b3-builder`.

**2. Lay out the plugin:**

```text
my-macros/
  manifest.json
  files/cfg/klipper/my-macros.cfg
```

A minimal working `manifest.json`:

```json
{
  "name": "my-macros",
  "title": "My Macros",
  "version": "0.1.0",
  "description": "My favorite Klipper macros as an installable plugin.",
  "tagline": "My favorite macros, one install away.",
  "category": "tuning",
  "channel": "stable",
  "printer_specific": false,
  "source": "https://github.com/you/my-macros",
  "publisher": "PLACEHOLDER",
  "author": "you",
  "requires": { "capabilities": ["klipper-generic"], "variables": [] },
  "permissions": ["klipper-config", "restart"],
  "install": {
    "place": [{ "class": "klipper-config", "src": "files/cfg/klipper/my-macros.cfg" }],
    "restart": ["klipper"]
  }
}
```

No checksums, no file list, no real publisher: the builder fills those in. Leave `publisher` as the
literal string `PLACEHOLDER`. You cannot know here which key will sign your release, so a signed build
overwrites it in the packed manifest (and in the catalog entry) with the fingerprint of the key it
signed with, before signing those bytes. An unsigned build leaves your placeholder as it found it.

`author` is your display name (a person or an organization). It is separate from `publisher` on
purpose: `publisher` is the signing-key fingerprint that PROVES who shipped the package, `author` is
just a name shown next to it, and the two may differ (you author a plugin, an org key signs the
release). Only the signature is proof; `author` is shown, never trusted.

If your plugin packages an external project, add `sw_version` with that project's version (for example
`"sw_version": "1.37.2"` for a Fluidd wrapper). The store then shows the upstream version a user is
installing as the primary one, with your plugin's own `version` in brackets (`Fluidd v1.37.2 (plugin
v0.1.4)`). Omit `sw_version` for a plugin that wraps nothing.

**3. Build it:**

```sh
npx b3-builder build --source ./my-macros --out dist --atom-repo you/my-macros
```

(For a one-off build you can skip step 1 entirely: `npx github:Bespok3d/b3-builder build ...`
downloads, builds, and runs the tool in one go.)

`--atom-repo` is your GitHub `owner/repo` slug; the catalog entry's documentation link points at
it. The result:

```text
dist/my-macros-0.1.0.b3       the installable package
dist/my-macros.atom.json      its catalog entry
```

**4. Install it:** sideload the `.b3` by dragging it onto the Bespok3d desktop app window, or
publish it through the GitHub Action below.

## CLI reference

```sh
b3-builder build [flags]
```

| Flag | Meaning | Default |
| --- | --- | --- |
| `--source <dir>` | what to build: one plugin dir, or a repo of plugin dirs | current dir |
| `--out <dir>` | where the built artifacts go | `./dist` |
| `--atom-repo <owner/repo>` | publisher identity; each catalog entry's doc link points at this repo (required) | none |
| `--unit plugin\|repo` | build one plugin, or every plugin dir in the source dir | auto-detected |
| `--list-name <name>` | display name of the assembled plugin list (repo unit, required) | none |
| `--list-publisher <name>` | publisher of the assembled plugin list (repo unit, required) | none |
| `--list-author <name>` | author display name stamped on the assembled list's own entry (repo unit) | none |
| `--exclude <dir>` | skip this immediate subdir even if it holds a manifest (repeatable; repo unit) | none |
| `--bake` | produce each plugin's payload from source via its declared bake steps | off |
| `--skip-unchanged` | reuse an existing `.b3` whose content is unchanged instead of repacking | off |
| `--sign <key-file\|key-id>` | sign with this key: a file holding an armored private key, or a key id in your GnuPG keyring | unsigned |

Unit auto-detection: a source dir that itself holds a `manifest.json` is one plugin; otherwise it
is treated as a repo of plugin dirs. An explicit `--unit` wins.

Outputs: every plugin yields `<name>-<version>.b3` plus `<name>.atom.json`, its atom: the catalog
entry a plugin index aggregates. A repo build also
assembles `index.json`, a self-contained plugin list (with dependencies resolved across the repo's
plugins) that an index of lists can reference. Two plugins in one repo may not share the same
name and version.

Exit behavior: success prints `Built N package(s) into <out>` and exits 0; any failure prints
`b3-builder build failed: <reason>` and exits 1. Any subcommand other than `build` or `init` prints
the usage line and exits 2.

### `init`: scaffold the one file you hand-author

```sh
b3-builder init plugin    # scaffold a manifest.json in the current directory
b3-builder init list      # scaffold a list reference (an index's lists[] entry)
```

Like `npm init`: `init` asks a short series of questions and writes the one JSON document a publisher
authors by hand, then the `build` pipeline generates everything else (the checksums, the file list,
the atom, the assembled sub-list). Each prompt shows a default in brackets; press Enter to take it. A
blank answer to `sw_version` omits the field, so a plugin that wraps nothing carries no `sw_version`.
`init` never overwrites an existing file: it refuses and exits 1, so it is safe to run in a populated
directory.

- `init plugin` writes `manifest.json`, defaulting `name` to the current directory's name, `publisher`
  to `PLACEHOLDER` (a signed build stamps the real fingerprint), and `author` to your display name.
- `init list` writes `<list-name>.json`, the `{ name, url, author, publisher }` reference an index's
  `lists[]` holds. `author` (display name) and `publisher` (signing-key fingerprint, `PLACEHOLDER`
  until the referenced list is signed) are kept separate because the two may name different parties.

## Signing what you publish

A build signs nothing unless you give it a key, and an unsigned package is one a printer cannot
tell apart from anyone else's. Two ways to hand a key over, for the two places builds happen.

**On your own machine, name a key with `--sign`.** Either the path to a file holding an armored
private key, or a key id or fingerprint held in your local GnuPG keyring:

```sh
b3-builder build --source ./my-macros --out dist --atom-repo you/my-macros --sign ~/keys/my-publishing-key.asc
b3-builder build --source ./my-macros --out dist --atom-repo you/my-macros --sign 3AA5C34371567BD2
```

A key id is exported through `gpg` in batch mode. The key must not be passphrase-protected: GnuPG
exports it still wrapped in its passphrase, and a wrapped key cannot sign, so the build stops and
says so. A reference that resolves to no secret key fails the build too; it never falls through to
packing unsigned in silence.

**In CI, put the armored key itself in the `B3D_SIGNING_KEY` environment variable** (the GitHub
Action reads it from a repository secret). The key material never travels as a flag value:
anything in a command line is readable by every other process on the machine. `--sign` carries
only a reference to a key, which is why it is safe there. An explicit `--sign` wins over
`B3D_SIGNING_KEY` when both are present.

Signing also decides who the package says it comes from: the packed manifest and the catalog entry
both get the fingerprint of the signing key in place of the source's `PLACEHOLDER` publisher.

If you use the Bespok3d desktop app to create and label your publishing keys, referring to one of
those keys by its label here is a planned convenience, not something this version does yet.

## Bake reference

Plugins whose payload is a build output declare how to produce it in the manifest's `bake` list.
All bakes target the printer's platform: arm64 Linux, the hardware Bespok3d currently supports
(the Snapmaker U1 first).
Each entry names a `class`; a plugin may declare several steps. Baking only runs with `--bake`
(or the Action's `bake: 'true'`): a build over an already-baked tree skips it. The `bake` field is
build-time only and is stripped from the shipped `.b3`. A plugin that needs a bake but has not
been baked fails the build's final gate instead of packing empty.

**Python dependencies** are not a bake entry: put a `requirements.txt` (deps for the plugin's own
virtualenv, shipped as wheels) and/or `klipper_requirements.txt` (packages unpacked for a
Klipper/Moonraker extra) at the plugin root. With `--bake` the builder downloads them for the
printer's platform (aarch64, CPython 3.11); a dependency with no arm64 wheel fails the build
loudly instead of shipping a package the printer cannot install. Needs `python3` with pip on the
build machine.

**`"class": "go"`**: clone, check out, and cross-compile a Go program (static arm64 binary).
Needs the Go toolchain and git.

| Field | Meaning | Default |
| --- | --- | --- |
| `source` | git URL of the Go project | required |
| `commit` | exact commit to build | required |
| `package` | package path inside the project | `.` |
| `output` | where the binary lands, relative to the plugin dir | required |

**`"class": "download"`**: fetch upstream release artifacts, verify them, and stage files out of
them. Needs `curl`, `tar`, and `ar` (for `.deb`).

| Field | Meaning | Default |
| --- | --- | --- |
| `fetch[].url` | artifact URL | required |
| `fetch[].sha256` | checksum the download must match | required |
| `fetch[].archive` | `deb`, `tar.xz`, or `tar.gz` | required |
| `fetch[].members[]` | `{path, dest, mode}`: file inside the archive, destination relative to the plugin dir, file mode | mode `0755` |
| `include[]` | `{src, dest, mode}`: local files staged alongside (launcher scripts etc.) | mode `0755` |

**`"class": "docker-c"`**: build C source in Docker for arm64 (QEMU on x86 runners) and stage the
produced artifacts. Needs Docker.

`members[]` is the EXHAUSTIVE list of what the build produces, not a spot-check: `out` must hold
exactly these and nothing else. An artifact you did not declare fails the build instead of quietly
shipping to a printer, so a Dockerfile change that starts leaving something extra in `out` is caught
at build time. Same contract the `download` class carries.

| Field | Meaning | Default |
| --- | --- | --- |
| `dockerfile` | Dockerfile that builds the program | required |
| `context` | Docker build context, relative to the plugin dir | `.` |
| `platform` | target platform | `linux/arm64` |
| `out` | dir inside the image holding the build output | `/out` |
| `members[]` | `{path, dest, mode}`: artifact inside `out`, destination relative to the plugin dir, file mode | mode `0755` |

**`"class": "docker-ko"`**: build a kernel module against the exact printer kernel. The built
module's vermagic is checked against the declared one; on mismatch the build refuses to ship the
`.ko`. Needs Docker.

| Field | Meaning | Default |
| --- | --- | --- |
| `dockerfile` | Dockerfile that builds the module | required |
| `context` | Docker build context, relative to the plugin dir | `.` |
| `module` | module filename the build produces | required |
| `out` | dir inside the image holding the build output | `/out` |
| `kernel.release` | target kernel release string | required |
| `kernel.vermagic` | vermagic string the target kernel accepts | required |
| `variant_dest` | where the `.ko` lands, relative to the plugin dir | required |

## GitHub Action reference

The composite Action gives a plugin repo its whole release pipeline from one `uses:`. A run
builds (and bakes) every plugin, runs each plugin's `tests/run.sh` (a failing test aborts before
anything is released), cuts a GitHub release with the `.b3` asset per plugin, rewrites the
assembled `index.json` so each entry's download URL points at its real release asset, uploads that
signed `index.json` as an asset of the same releases, and optionally registers the list in an
index-of-lists repo. That full pipeline is the `repo` unit (the default); with `unit: plugin` the
Action only builds the artifacts: no tests, no release, no list asset, no registration.

A release writes nothing back into the plugin repo. The list ships the way the `.b3` files ship, as
a release asset, so readers fetch it at
`https://github.com/<owner>/<repo>/releases/latest/download/index.json`, an address that does not
change when the next release lands.

A complete `release.yml`:

```yaml
name: release
on:
  push:
    branches: [main]

permissions:
  contents: write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Bespok3d/b3-builder@main
        with:
          unit: repo
          bake: 'true'
          atom-repo: ${{ github.repository }}
          list-name: My Plugins
          list-publisher: my-org
          list-ref-name: My Plugins
          main-index-repo: my-org/main-index
          main-index-token: ${{ secrets.MAIN_INDEX_TOKEN }}
```

| Input | Meaning | Default |
| --- | --- | --- |
| `unit` | `repo` (a repo of plugin dirs, full pipeline) or `plugin` (one plugin dir, build only) | `repo` |
| `source` | source dir to build, relative to the checkout | `.` |
| `out` | output dir for the `.b3` set and the built index | `dist` |
| `atom-repo` | `owner/repo` slug the catalog doc links point at (required) | none |
| `list-name` | display name of the assembled plugin list (repo unit) | none |
| `list-publisher` | publisher of the assembled plugin list (repo unit) | none |
| `list-ref-name` | name the list is registered under in the index-of-lists (repo unit) | none |
| `main-index-repo` | `owner/repo` of the index-of-lists to register into (repo unit) | none |
| `main-index-token` | token with contents write on the index-of-lists repo; empty skips registration | empty |
| `exclude-dirs` | space-separated subdirs that must never publish (dev-only variants) | empty |
| `bake` | produce each plugin's payload from source via its bake steps | `'false'` |
| `skip-unchanged` | reuse an existing `.b3` whose content is unchanged | `'false'` |
| `node-version` | Node.js version the pipeline runs on | `'20'` |

Tokens: the releases and the list asset use the workflow's own `github.token`, which needs
`permissions: contents: write` (as in the example). Registering into a separate index-of-lists repo
needs its own token (`main-index-token`) with contents write on that repo; leave it empty and the
register step is skipped, everything else still runs. Registration writes `lists/<your-repo>.json`
into the index-of-lists repo, pointing at your repo's latest-release `index.json` asset. Docker builds
(`docker-c` / `docker-ko` bakes) are cached through the Actions layer cache automatically.

## Development

```sh
npm ci
npm run check   # typecheck, lint, tests, and the repo's guards
```

Contributor conventions and the internal architecture notes live in [CLAUDE.md](CLAUDE.md).

## Licence

Copyright (C) 2026 unlucio and the Bespok3d contributors

This program is free software: you can redistribute it and/or modify it under the terms of the GNU
Affero General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero
General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program. If
not, see <https://www.gnu.org/licenses/>. The full text is in [LICENSE](LICENSE).

Bespok3d is a project of the Bespok3d Organisation, which is not a legal entity. Copyright is held by
the individual authors named above.

## Support this project

Bespok3d is built and maintained in the open, on stock printer firmware. If it saved you an
afternoon, you can [buy me a coffee](https://buymeacoffee.com/unlucio).
