# Changelog

## 0.2.0 — 2026-08-28

Hardening release. The JSON schema stays at version 1; every change to the
default output is additive.

### Safety

- **`--plan` can no longer widen the safety roots.** A plan naming an "app" in
  an arbitrary directory used to make that directory's parent an allow-listed
  root. The parent is now admitted only when the app already sits under an
  application root, and `--plan` re-resolves the app from discovery (same
  identifier *and* path; `plan_app_mismatch`, exit 6).
- **Artifacts are bound to the app.** Anything inside another installed app's
  bundle is blocked at execute time, so a plan for app A cannot remove app B —
  including protected `com.apple.*` apps.
- **Symlinked catalog roots.** A path under `$HOME` must now also *resolve*
  under the real `$HOME`; a `~/Library/Logs` symlinked elsewhere is no longer a
  way out.
- `--purge --yes` requires `--confirm-purge` (`confirmation_required`, exit 7).
- Protected-app detection (`SafetyGuard.is_protected_app`) is applied to every
  discovered app, not only Apple bundle ids.

### Red-team audit (46 reproduced findings, all addressed except two noted below)

- **A plan is a selection, not a source of paths.** `uninstall --plan` re-scans
  the app and acts only on artifacts the fresh scan attributes to it *and*
  the plan selected. Saved fingerprints still detect drift; stale ids are
  reported in `selection_notes`; plan `warnings` are never echoed; compact
  plans replay; `orphans -o` plans replay; `--aggressive/--include-low` with
  `--plan` is a usage error.
- Artifacts that *contain* another installed app are blocked (ancestor
  directories); NFC-normalized comparisons on macOS; `.app` discovery to depth 6
  including dot-directories.
- Sibling guards on `.ShipIt` names, ByHost dot-children, embedded ids that are
  (or live under) another app's id, and display names shared with another app;
  `Contents/Frameworks` ids are shared, never auto-selected; the Linux `Exec=`
  basename is no longer a name key; bundle ids compare case-sensitively on
  case-sensitive filesystems; more reserved XDG names.
- `undo`: operation ids validated (no path traversal), the run lock is held,
  a torn manifest line no longer hides the operation, same-second operations
  get distinct ids, and a trashed item that was replaced by a different object
  is refused rather than "restored".
- Exit-code contract: everything blocked → 6; drifted/failed/blocked alongside
  removals → 5; non-zero exits always carry `error`; argparse errors under
  `--json` are envelopes; `--only ""` and unknown `--except` ids are errors;
  fuzzy substring selectors are refused with `--yes`; `selection_notes` sorted.
- zap's own state directory can never be a target; a leaf symlink to another
  volume no longer causes a trash directory to be created inside its target.
- Known, documented, not fixed: a cross-device move whose final `rmtree(src)`
  fails partway is reported `failed` with the copy left in the trash (no data
  loss, but not journaled); two installed copies of the same bundle id share
  id-keyed data, so uninstalling one by path removes the other's preferences —
  review the plan when `zap list` shows duplicate ids.

### Swarm review follow-ups

- `zap orphans` no longer crashes on a Mac without Full Disk Access; unreadable
  locations are reported in a `permission_denied` warning.
- Vendor/product folders shared by two installed apps (`com.acme.Widget` and
  `net.acme.Widget` → `Acme/Widget`) are marked shared, never auto-selected.
- Exit codes: a run where every item failed or drifted is 5, not 4.
- Linux: root-owned prefixes are derived from the system root (no more
  `/optional` matching `/opt`); case-variant identifiers are distinct apps.
- `--plan` with `--aggressive/--include-low` is rejected before the re-scan.

### Correctness

- Case-variant vendor folders (`Google/Chrome` vs `google/Chrome` on APFS) no
  longer produce two artifacts; candidates are deduplicated by inode.
- `undo` on Linux removes the `.trashinfo` it wrote (the manifest now records
  it). Manifests from 0.1.0 still restore.
- `exclusive_lock` no longer swallows an `ImportError` raised inside it.
- Crash reports match on the longest known process name, so `Widget-Pro-2026….ips`
  is attributed to *Widget-Pro*.
- Auto-selection is decided in exactly one place (`Engine._apply_selection_tiers`);
  `--include-low` implies `--aggressive`. Dead helpers removed from `matching`.
- Under `sudo`, per-volume trash directories and history use the invoking
  user's uid/gid.

### Reach

- **Team ID** is read from the Mach-O code signature (pure Python, no
  `codesign`), so `TEAMID.<bundle-id>` caches, Application Scripts and Group
  Containers are found. Verified against `codesign -dv` on every app in
  `/Applications` of the development machine (19/19).
- Bundled `Contents/Library/LaunchAgents|LaunchDaemons` labels count as
  embedded ids; `~/Library/Preferences`, `~/Library/LaunchAgents`,
  `/Library/LaunchAgents|LaunchDaemons` are scanned by id prefix, catching
  helper plists.
- Linux: `~/.config`, `~/.cache`, `~/.local/share` are also matched by
  reverse-DNS id and by vendor/product folder; Flatpak overrides are found;
  Flatpak and Snap app directories are application roots.

### Agent surface

- `--json --compact` (≈45% fewer bytes per artifact), `uninstall --summary`
  (counts + only results needing attention), `dropped_by_safety.items` capped
  at 20 on stdout (`items_truncated`), `ambiguous_selector.candidates` is a
  list of `{identifier, path}`.
- Plans written with `-o` are always full; compact plans replay too (they are
  selections), with drift then measured only against the fresh scan.

### Tooling

- `pip install -e ".[dev]"`, `make check` (ruff, mypy, pytest), GitHub Actions
  matrix on macOS/Ubuntu × Python 3.9/3.12.

## 0.1.0

Initial release.
