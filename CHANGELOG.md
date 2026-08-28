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
- Plans written with `-o` are always full; compact plans are refused by
  `--plan`.

### Tooling

- `pip install -e ".[dev]"`, `make check` (ruff, mypy, pytest), GitHub Actions
  matrix on macOS/Ubuntu × Python 3.9/3.12.

## 0.1.0

Initial release.
