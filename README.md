# zap

**A terminal-native, agent-friendly app uninstaller.** Like AppZapper — it finds
an application *and every leftover file it scattered* (preferences, caches,
application support, containers, launch agents, saved state, logs, receipts,
login items) — but built for the command line, scriptable by AI agents, and
cross-platform (macOS + Linux). Zero runtime dependencies; pure Python stdlib.

```
$ zap scan Slack
Scan: Slack
  com.tinyspeck.slackmacgap  v4.36.0  /Applications/Slack.app
  plan_id 8f3c1e77a1b2c3d4

  [x] a1b2c3d4e5f60718     412.0 MB  app_bundle            exact       normal
        /Applications/Slack.app
  [x] 4f5e6d7c8b9a0b1c      88.4 MB  caches                exact       safe
        /Users/me/Library/Caches/com.tinyspeck.slackmacgap
  [x] 0a1b2c3d4e5f6071      1.2 KB   preferences           exact       normal
        /Users/me/Library/Preferences/com.tinyspeck.slackmacgap.plist
  [ ] 9182736455647382      512.0 MB application_support   heuristic   normal
        /Users/me/Library/Application Support/Slack

  3 selected of 4 (501.6 MB of 1.0 GB)

$ zap uninstall Slack            # dry-run preview — nothing is removed
$ zap uninstall Slack --yes      # move everything selected to the Trash
$ zap undo 20260824-120000-8f3c1e77   # bring it all back
```

## Why another uninstaller

Dragging an app to the Trash leaves its data behind. Existing GUI cleaners fix
that but can't be scripted or driven by an agent, and they run on trust. `zap`
is designed around three ideas:

- **Recoverable by default.** Removal is a *dry-run* unless you pass `--yes`, and
  even then everything goes to the **Trash** (not `rm`) and is journaled so
  `zap undo` can restore it. Permanent deletion needs an explicit `--purge`.
- **Safe against deleting the wrong thing.** Bundle ids and app names come from
  files the app controls, so `zap` treats them as untrusted: every candidate
  path must resolve *inside* an allow-listed root (parents are canonicalized so
  a symlinked directory can't escape it), only full-bundle-id **exact** matches
  are ever auto-selected, and sibling apps that merely share a name prefix
  (Chrome vs Chrome Canary) are never swept up.
- **First-class for agents.** Every command speaks JSON with a versioned schema,
  stable error codes mapped 1:1 to exit codes, content-addressed artifact ids,
  and a `scan → review → apply` split so an agent can approve exactly what it
  wants before anything is touched.

## Install

```
pipx install zap-uninstaller      # recommended
# or
pip install --user zap-uninstaller
# or from a checkout
pip install -e .
```

Requires Python 3.9+. No third-party dependencies.

## Commands

| Command | What it does |
| --- | --- |
| `zap list` | List discovered installed apps. `--all` includes protected system apps. |
| `zap scan <app>` | Find the app and everything attributed to it. Read-only. |
| `zap uninstall <app>` | Remove the app (dry-run unless `--yes`; Trash unless `--purge`). |
| `zap orphans` | Find bundle-id-named leftovers whose owning app is gone. |
| `zap history` | List past uninstall operations. |
| `zap undo <id>` | Restore a past operation from the Trash. |
| `zap capabilities` | Print schema/feature introspection (for agents). |

`<app>` may be a name (`Slack`), a bundle id (`com.tinyspeck.slackmacgap`), or a
path to the `.app`. Ambiguous names return the candidate list and exit `8`.

### Selecting what to remove

By default only high-confidence artifacts are acted on. Widen or narrow with:

- `--aggressive` — also act on *derived* matches (bundle-id prefixes, vendor
  folders like `Application Support/Google/Chrome`, embedded helpers).
- `--include-low` — also act on *name-only* (heuristic) matches.
- `--only <ids>` / `--except <ids>` — act on exactly these artifact ids
  (comma-separated; ids come from `scan`).
- `--include-system` — also act on system-level artifacts that need root.

## Using zap from an agent

```bash
# 1. Scan and capture the plan.
zap --json scan "com.acme.Widget" > plan.json

# 2. Inspect plan.json: each artifact has a stable `id`, `path`, `category`,
#    `match.confidence`, `risk`, `size_bytes`, `shared`, `reversible`,
#    `requires_root`, and `auto_selected`.

# 3. Apply exactly the artifacts you approve.
zap --json uninstall --plan plan.json --only "a1b2c3d4e5f60718,4f5e6d7c8b9a0b1c" --yes
```

**Envelope.** Every `--json` call prints exactly one object to stdout:

```json
{
  "zap_version": "0.1.0",
  "schema_version": 1,
  "command": "scan",
  "ok": true,
  "data": { "...": "..." },
  "warnings": [ { "code": "root_required", "message": "..." } ],
  "error": null
}
```

**Guarantees for automation.** Under `--json`, `zap` never prompts and never
reads stdin, arrays are totally ordered, byte counts are integers, and
timestamps are RFC3339 UTC. A saved plan is re-validated at apply time: every
path is re-checked by the safety guard and re-fingerprinted, and anything that
changed since the scan is reported `drifted` rather than deleted.

**Exit codes** (also `error.code`, 1:1):

| Code | Meaning |
| --- | --- |
| 0 | ok |
| 1 | error (internal) |
| 2 | usage error / unknown artifact ids |
| 3 | app not found |
| 4 | nothing to remove |
| 5 | partial failure (some artifacts failed) |
| 6 | blocked by safety (protected app) |
| 7 | confirmation required |
| 8 | ambiguous selector (see `data.candidates`) |
| 9 | locked (another zap operation is running) |

## How attribution works

Each candidate is classified by how strongly it belongs to the app:

- **exact** — a full bundle-id (or receipt) match at a bundle-id-keyed location.
  Only these are auto-selected.
- **derived** — a dot-boundary bundle-id prefix, a team-id-prefixed cache, an
  embedded helper id, or a vendor/product folder derived from the bundle-id
  domain. Shown, but selected only with `--aggressive`.
- **heuristic** — a display/product-name match. Shown, selected only with
  `--include-low`.

Shared resources (Group Containers, and vendor updaters like Google Keystone,
Sparkle, Microsoft AutoUpdate) are attributed but **never auto-selected**, since
removing them can break other apps. Kernel/system extensions are **detected but
never removed**.

## Safety model

- Removal is a dry-run unless `--yes`; Trash unless `--purge`; `--purge` is
  refused for shared and system artifacts.
- The Trash lives on the *same volume* as each item (XDG `.Trash-<uid>` /
  macOS `.Trashes`), so moves are atomic; cross-device moves copy-verify-then
  -delete and never unlink the source before the copy is durable.
- Every removal is journaled incrementally (flushed + fsync'd per item) so a
  crash still leaves everything recoverable.
- A path is removed only if, after canonicalizing its parent, it sits strictly
  inside an allow-listed root — the same roots the catalog searches.

## Platform support

- **macOS** — reads `Info.plist` (including binary plists) for bundle ids, walks
  embedded LoginItems/XPCServices/PlugIns/Helpers/Frameworks to find helper and
  updater ids, and searches the full `~/Library` + `/Library` catalog plus
  installer receipts.
- **Linux** — discovers apps from `.desktop` entries, Flatpak, and Snap; searches
  `~/.config`, `~/.cache`, `~/.local/share`, `~/.local/state`, `~/.var/app`
  (Flatpak), and `~/snap`, with reserved session/desktop directories protected
  from name matching.

## Development

```
python -m unittest discover -s tests
```

The whole engine is built around an injected `Environment` (filesystem roots,
clock, device map), so macOS behavior is fully unit-tested on Linux against
throwaway directory trees.

## License

MIT. See [LICENSE](LICENSE).
