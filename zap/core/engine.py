"""The orchestration layer: discover, scan→plan, select, execute, orphans.

This is the only module that both reads *and* writes the filesystem, and it is
deliberately paranoid on the write path: every artifact is re-checked by the
safety guard and re-fingerprinted for drift immediately before it is touched,
the whole operation holds an advisory lock, and each removal is journaled the
instant it happens.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .. import SCHEMA_VERSION, __version__
from ..environment import Environment
from ..platforms import get_backend
from ..platforms.base import ResolveResult
from . import fsutil
from . import identifiers as I
from .catalog import ID_PATH, ID_SCAN
from .history import (
    ManifestRecord,
    ManifestWriter,
    exclusive_lock,
    make_operation_id,
)
from .models import (
    CAT_APP_BUNDLE,
    CAT_LAUNCH_DAEMON,
    CAT_SYSTEM_EXTENSION,
    CONFIDENCE_DERIVED,
    CONFIDENCE_EXACT,
    CONFIDENCE_HEURISTIC,
    RISK_ELEVATED,
    RISK_NORMAL,
    STATUS_BLOCKED,
    STATUS_DRIFTED,
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_REMOVED,
    STATUS_SKIPPED,
    ActionResult,
    App,
    Artifact,
    Fingerprint,
    Match,
    ORPHANS_IDENTIFIER,
    Plan,
    artifact_id_for,
)
from .safety import SafetyGuard, build_allowed_roots
from .scanner import Scanner
from .trash import get_trash


# Exact-confidence match keys that may be removed without review. Everything
# else (prefix, name, vendor folder, embedded, group container, iCloud) is
# shown but must be opted into with --aggressive / --include-low / --only.
AUTO_EXACT_KEYS = frozenset({"bundle_id", "byhost", "receipt", "plugin_bundle_id"})


def _perm_reason(env, art, path: Path) -> str:
    """Turn a bare EPERM/EACCES into an actionable message."""
    if art.requires_root:
        return "permission denied (needs root — rerun with sudo)"
    try:
        owner = os.lstat(path).st_uid
    except OSError:
        owner = env.uid
    if owner != env.uid and env.uid != 0:
        who = "root" if owner == 0 else f"uid {owner}"
        return (f"permission denied (owned by {who}"
                + (" — App Store install; rerun with sudo or delete via Finder)" if owner == 0
                   else " — rerun with sudo)"))
    if sys.platform == "darwin":
        home = str(env.home)
        p = str(path)
        if p.startswith(os.path.join(home, "Library") + os.sep):
            return ("permission denied (macOS privacy protection — grant your terminal "
                    "Full Disk Access in System Settings > Privacy & Security, then retry)")
    return "permission denied"


def _identity(path: Path) -> Optional[Tuple[int, int]]:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def iso_now(env: Environment) -> str:
    return env.now().strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ExecutionReport:
    operation_id: Optional[str]
    dry_run: bool
    method: str
    results: List[ActionResult] = field(default_factory=list)

    def summary(self) -> dict:
        counts: dict = {}
        reclaimed = 0
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
            if r.status in (STATUS_REMOVED, STATUS_DRY_RUN):
                reclaimed += r.size_bytes
        return {
            "operation_id": self.operation_id,
            "dry_run": self.dry_run,
            "method": self.method,
            "counts": counts,
            "bytes_reclaimed": reclaimed,
        }

    def to_dict(self, *, summary_only: bool = False, compact: bool = False) -> dict:
        """``summary_only`` keeps counts plus only the results that need a
        human's attention (anything not removed / previewed)."""
        results = self.results
        if summary_only:
            results = [r for r in results if r.status not in (STATUS_REMOVED, STATUS_DRY_RUN)]
        return {
            **self.summary(),
            "results": [r.to_dict(compact=compact) for r in results],
            "results_omitted": len(self.results) - len(results),
        }

    def has_failures(self) -> bool:
        return any(r.status == STATUS_FAILED for r in self.results)

    def removed_any(self) -> bool:
        return any(r.status == STATUS_REMOVED for r in self.results)


class Engine:
    def __init__(self, env: Environment):
        self.env = env
        self.backend = get_backend(env)
        self._apps: Optional[List[App]] = None

    # -- discovery ---------------------------------------------------------

    def list_apps(self) -> List[App]:
        if self._apps is None:
            apps = self.backend.discover_apps()
            guard = self._guard()
            for app in apps:
                if guard.is_protected_app(app.path, app.bundle_id, app.name):
                    app.protected = True
            self._apps = apps
        return self._apps

    def resolve(self, selector: str) -> ResolveResult:
        return self.backend.resolve(self.list_apps(), selector)

    def _guard(self, app: Optional[App] = None) -> SafetyGuard:
        roots = build_allowed_roots(self.env, self.backend.all_roots())
        if app is not None and self._app_in_application_root(app):
            # Allow the app bundle's own directory (e.g. a subfolder of
            # /Applications) so the bundle itself can be removed. The parent is
            # only admitted when the app already sits under an application
            # root — otherwise a crafted plan could name any directory as an
            # "app" and turn its parent into an allow-listed root.
            roots.append(app.path.parent)
        return SafetyGuard(self.env, roots)

    def _app_in_application_root(self, app: App) -> bool:
        app_roots = build_allowed_roots(self.env, self.backend.application_roots())
        probe = SafetyGuard(self.env, app_roots)
        ok, _reason = probe.check(app.path)
        return ok

    # -- scan / plan -------------------------------------------------------

    def scan(self, app: App, *, aggressive: bool = False, include_low: bool = False) -> Plan:
        guard = self._guard(app)
        scanner = Scanner(self.env, guard)
        scan_input = self.backend.make_scan_input(app, self.list_apps())
        out = scanner.scan(scan_input, self.backend.locations())

        artifacts = out.artifacts
        # The app bundle / binary itself.
        bundle_artifact = self._bundle_artifact(app, guard)
        if bundle_artifact is not None:
            artifacts = [bundle_artifact] + [
                a for a in artifacts if a.id != bundle_artifact.id
            ]

        self._apply_selection_tiers(artifacts, aggressive=aggressive, include_low=include_low)
        artifacts.sort(key=lambda a: str(a.path))

        warnings = list(out.warnings)
        if out.dropped:
            warnings.append(
                {
                    "code": "dropped_by_safety",
                    "message": f"{len(out.dropped)} candidate path(s) were excluded by the safety guard.",
                    "items": out.dropped,
                }
            )
        warnings.extend(self._app_warnings(app, artifacts))

        return Plan(
            app=app,
            artifacts=artifacts,
            created_at=iso_now(self.env),
            schema_version=SCHEMA_VERSION,
            zap_version=__version__,
            warnings=warnings,
        )

    def _bundle_artifact(self, app: App, guard: SafetyGuard) -> Optional[Artifact]:
        path = app.path
        if not path.exists():
            return None
        ok, _reason = guard.check(path)
        if not ok:
            return None
        size, count = fsutil.tree_size(path)
        managed = app.managed_by in ("dpkg", "rpm", "flatpak", "snap", "system")
        notes = []
        if managed:
            notes.append(
                f"Installed via {app.managed_by}; removing the bundle here may not "
                "fully uninstall it — use the package manager for the binary."
            )
        return Artifact(
            path=path,
            category=CAT_APP_BUNDLE,
            match=Match("app_path", str(path), CONFIDENCE_EXACT),
            risk=self._bundle_risk(app),
            size_bytes=size,
            item_count=count,
            shared=False,
            reversible=True,
            requires_root=self._bundle_requires_root(app),
            auto_selected=False,  # set by _apply_selection_tiers
            fingerprint=Fingerprint.of(path),
            notes=notes,
        )

    def _bundle_risk(self, app: App):
        return RISK_ELEVATED if self._bundle_requires_root(app) else RISK_NORMAL

    def _bundle_requires_root(self, app: App) -> bool:
        """On Linux, binaries under the system prefixes are root-owned."""
        if not self.env.is_linux:
            return False
        p = os.path.normpath(os.path.abspath(str(app.path)))
        for sub in ("opt", "usr"):
            root = os.path.normpath(str(self.env.resolve("sys", sub)))
            if p == root or p.startswith(root + os.sep):
                return True
        return False

    def _apply_selection_tiers(self, artifacts, *, aggressive: bool, include_low: bool) -> None:
        """Set ``auto_selected`` on the default action set for a plan.

        This is the ONLY place auto-selection is decided (``--include-low``
        implies ``--aggressive``). The table:

        ======================  =======  ============  =============
        artifact                default  --aggressive  --include-low
        ======================  =======  ============  =============
        app bundle (non-root)   yes      yes           yes
        system extension        never    never         never
        shared / requires root  no       no            no
        exact, AUTO_EXACT_KEYS  yes      yes           yes
        exact, other key        no       yes           yes
        derived                 no       yes           yes
        heuristic               no       no            yes
        ======================  =======  ============  =============
        """
        # --include-low is the widest tier and implies --aggressive.
        aggressive = aggressive or include_low
        for a in artifacts:
            a.auto_selected = self._auto_select(a, aggressive=aggressive, include_low=include_low)

    @staticmethod
    def _auto_select(a: Artifact, *, aggressive: bool, include_low: bool) -> bool:
        if a.category == CAT_SYSTEM_EXTENSION:
            return False
        if a.category == CAT_APP_BUNDLE:
            return not a.requires_root
        if a.shared or a.requires_root:
            return False
        conf = a.match.confidence
        if conf == CONFIDENCE_EXACT:
            return a.match.key in AUTO_EXACT_KEYS or bool(aggressive)
        if conf == CONFIDENCE_DERIVED:
            return bool(aggressive)
        if conf == CONFIDENCE_HEURISTIC:
            return bool(include_low)
        return False

    def _app_warnings(self, app: App, artifacts) -> List[dict]:
        out: List[dict] = []
        if app.degraded:
            out.append(
                {
                    "code": "degraded_identifier",
                    "message": "This app has no usable bundle identifier; matching "
                    "falls back to its name and nothing is auto-selected. Review carefully.",
                }
            )
        if app.managed_by:
            out.append(
                {
                    "code": "managed_install",
                    "message": f"Managed by {app.managed_by}; use the package manager "
                    "to remove the program itself. zap can still clear user-level leftovers.",
                }
            )
        if any(a.requires_root for a in artifacts) and not self.env.is_root:
            out.append(
                {
                    "code": "root_required",
                    "message": "Some artifacts live in system locations and need root "
                    "(sudo) plus --include-system to remove.",
                }
            )
        if any(a.category == CAT_LAUNCH_DAEMON for a in artifacts):
            out.append(
                {
                    "code": "launchd_bootout",
                    "message": "A launch daemon was found. Boot it out "
                    "(sudo launchctl bootout system/<label>) before removing its plist.",
                }
            )
        return out

    # -- selection for execution ------------------------------------------

    def select(
        self,
        plan: Plan,
        *,
        only: Optional[Set[str]] = None,
        exclude: Optional[Set[str]] = None,
        include_system: bool = False,
        method: str = "trash",
    ) -> Tuple[List[Artifact], List[str], List[dict]]:
        """Return (selected, unknown_ids, notes).

        The default action set is the plan's ``auto_selected`` artifacts (whose
        tiers were fixed at scan time by --aggressive/--include-low). ``only``
        overrides that with an explicit id set; ``exclude`` subtracts ids.
        """
        by_id = {a.id: a for a in plan.artifacts}
        unknown: List[str] = []
        notes: List[dict] = []

        if only:
            selected = []
            for i in only:
                if i in by_id:
                    selected.append(by_id[i])
                else:
                    unknown.append(i)
        else:
            selected = [a for a in plan.artifacts if a.auto_selected]

        if exclude:
            selected = [a for a in selected if a.id not in exclude]

        final: List[Artifact] = []
        for a in selected:
            if a.category == CAT_SYSTEM_EXTENSION:
                notes.append({"code": "skipped_system_extension", "path": str(a.path)})
                continue
            if a.requires_root and not include_system:
                notes.append({"code": "skipped_requires_system", "path": str(a.path)})
                continue
            if method == "purge" and (a.shared or a.requires_root):
                notes.append({"code": "purge_refused_shared_or_system", "path": str(a.path)})
                continue
            final.append(a)
        return final, unknown, notes

    # -- execution ---------------------------------------------------------

    def execute(
        self,
        plan: Plan,
        selected: List[Artifact],
        *,
        method: str = "trash",
        dry_run: bool = True,
    ) -> ExecutionReport:
        if dry_run:
            return self._execute_inner(plan, selected, method=method, dry_run=True, writer=None)
        with exclusive_lock(self.env):  # raises LockBusy if another run holds it
            created_at = iso_now(self.env)
            operation_id = make_operation_id(self.env, plan.app.identifier, created_at)
            header = {
                "app": plan.app.identifier,
                "app_name": plan.app.name,
                "created_at": created_at,
                "method": method,
                "zap_version": __version__,
            }
            with ManifestWriter(self.env, operation_id, header) as writer:
                report = self._execute_inner(
                    plan, selected, method=method, dry_run=False, writer=writer
                )
            report.operation_id = operation_id if report.removed_any() else None
            return report

    def _execute_inner(self, plan, selected, *, method, dry_run, writer) -> ExecutionReport:
        guard = self._guard(plan.app)
        trash = get_trash(self.env)
        report = ExecutionReport(operation_id=None, dry_run=dry_run, method=method)
        foreign_apps = self._other_app_paths(plan.app)

        for art in sorted(selected, key=lambda a: str(a.path)):
            path = art.path
            # 1) never-remove categories
            if art.category == CAT_SYSTEM_EXTENSION:
                report.results.append(self._res(art, STATUS_BLOCKED, "system extension is never removed", method))
                continue
            # 2) re-run the safety guard at execute time
            ok, reason = guard.check(path)
            if not ok:
                report.results.append(self._res(art, STATUS_BLOCKED, reason, method))
                continue
            # 2b) never touch another installed app's bundle (or anything in it)
            owner = self._owning_app(path, foreign_apps)
            if owner is not None:
                report.results.append(
                    self._res(art, STATUS_BLOCKED, f"belongs to another installed app ({owner})", method)
                )
                continue
            inside = self._contained_app(path, foreign_apps)
            if inside is not None:
                report.results.append(
                    self._res(art, STATUS_BLOCKED, f"contains another installed app ({inside})", method)
                )
                continue
            # 3) existence
            if not os.path.lexists(path):
                report.results.append(self._res(art, STATUS_SKIPPED, "already gone", method))
                continue
            # 4) drift detection
            current = Fingerprint.of(path)
            if art.fingerprint is not None and not art.fingerprint.matches(current):
                report.results.append(self._res(art, STATUS_DRIFTED, "changed since scan", method))
                continue
            # 5) dry-run stops here (faithful preview of the above preflight)
            if dry_run:
                report.results.append(self._res(art, STATUS_DRY_RUN, None, method))
                continue
            # 6) act
            try:
                if method == "purge":
                    fsutil.remove_tree(path)
                    self._journal(writer, art, "", "purge", reversible=False)
                    report.results.append(
                        ActionResult(art.id, path, STATUS_REMOVED, reversible=False, method="purge", size_bytes=art.size_bytes)
                    )
                else:
                    item = trash.trash(path)
                    self._journal(writer, art, item.location, "trash", reversible=True,
                                  info=item.info, ident=_identity(Path(item.location)))
                    report.results.append(
                        ActionResult(art.id, path, STATUS_REMOVED, reversible=True, method="trash", trash_path=item.location, size_bytes=art.size_bytes)
                    )
            except PermissionError:
                report.results.append(self._res(art, STATUS_FAILED, _perm_reason(self.env, art, path), method))
            except Exception as exc:  # noqa: BLE001 - report, continue with the rest
                report.results.append(self._res(art, STATUS_FAILED, str(exc), method))
        return report

    def _other_app_paths(self, app: App) -> List[Tuple[str, str]]:
        """(normalized bundle path, identifier) of every installed app except ``app``."""
        mine = self._casekey(os.path.normpath(os.path.abspath(str(app.path))))
        out: List[Tuple[str, str]] = []
        for other in self.list_apps():
            p = self._casekey(os.path.normpath(os.path.abspath(str(other.path))))
            if p != mine:
                out.append((p, other.identifier))
        return out

    def _owning_app(self, path: Path, foreign: List[Tuple[str, str]]) -> Optional[str]:
        p = self._casekey(os.path.normpath(os.path.abspath(str(path))))
        for bundle, ident in foreign:
            if p == bundle or p.startswith(bundle + os.sep):
                return ident
        return None

    def _contained_app(self, path: Path, foreign: List[Tuple[str, str]]) -> Optional[str]:
        """Identifier of an installed app that lives *beneath* ``path``."""
        p = self._casekey(os.path.normpath(os.path.abspath(str(path))))
        for bundle, ident in foreign:
            if bundle.startswith(p + os.sep):
                return ident
        return None

    def _casekey(self, s: str) -> str:
        # APFS/HFS+ are case- and normalization-insensitive: NFC + casefold.
        return I.normalize_casefold(s) if self.env.is_darwin else s

    def _journal(self, writer, art, location, method, reversible, info=None, ident=None) -> None:
        if writer is not None:
            dev, ino = ident if ident else (None, None)
            writer.record(
                ManifestRecord(
                    artifact_id=art.id,
                    original=str(art.path),
                    location=location,
                    method=method,
                    reversible=reversible,
                    info=info,
                    dev=dev,
                    ino=ino,
                )
            )

    def _res(self, art: Artifact, status: str, reason, method: str) -> ActionResult:
        return ActionResult(
            id=art.id,
            path=art.path,
            status=status,
            reversible=(method != "purge"),
            method=method if status == STATUS_REMOVED else "",
            size_bytes=art.size_bytes,
            reason=reason,
        )

    # -- orphans -----------------------------------------------------------

    def orphans(self) -> Plan:
        """Bundle-id-named leftovers in id-keyed locations with no installed owner.

        Conservative by construction: only valid reverse-DNS names in
        bundle-id-keyed locations, excluding Apple/shared components and any id
        that matches an installed app or one of its embedded ids.
        """
        guard = self._guard()
        owned: Set[str] = set()
        for app in self.list_apps():
            if app.bundle_id:
                owned.add(I.normalize_casefold(app.bundle_id))
            for eid in app.embedded_bundle_ids:
                owned.add(I.normalize_casefold(eid))

        artifacts: List[Artifact] = []
        seen: Set[str] = set()
        warnings: List[dict] = []
        unreadable: List[str] = []
        for loc in self.backend.locations():
            if loc.strategy not in (ID_SCAN, ID_PATH):
                continue
            if loc.requires_root:
                continue  # don't rummage system dirs for orphans
            container = self.env.resolve(loc.base, *[s for s in loc.container.split("/") if s])
            if not container.is_dir():
                continue
            try:
                children = sorted(os.listdir(container))
            except PermissionError:
                # TCC-protected on macOS (Cookies, Mail, ...) without Full Disk Access.
                unreadable.append(str(container))
                continue
            except OSError:
                continue
            for child in children:
                name = child
                for suf in loc.suffixes or ():
                    if suf and name.endswith(suf):
                        name = name[: -len(suf)]
                        break
                if not I.is_valid_bundle_id(name):
                    continue
                low = I.normalize_casefold(name)
                if low in owned:
                    continue
                if I.is_apple_system_id(name) or I.is_shared_component_id(name):
                    continue
                if I.is_template_bundle_id(name):
                    continue
                path = container / child
                ok, _r = guard.check(path)
                if not ok:
                    continue
                aid = artifact_id_for(path)
                if aid in seen:
                    continue
                seen.add(aid)
                size, count = fsutil.tree_size(path)
                artifacts.append(
                    Artifact(
                        path=path,
                        category=loc.category,
                        match=Match("orphan_bundle_id", name, CONFIDENCE_HEURISTIC),
                        risk=loc.risk_level(),
                        size_bytes=size,
                        item_count=count,
                        shared=False,
                        reversible=True,
                        requires_root=False,
                        auto_selected=False,  # orphans are never auto-removed
                        fingerprint=Fingerprint.of(path),
                        notes=["No installed app claims this bundle id."],
                    )
                )
        artifacts.sort(key=lambda a: str(a.path))
        if unreadable:
            warnings.append(
                {
                    "code": "permission_denied",
                    "message": f"{len(unreadable)} location(s) could not be read (on macOS, grant the "
                    "terminal Full Disk Access in System Settings > Privacy & Security).",
                    "items": unreadable,
                }
            )
        app = App(name=ORPHANS_IDENTIFIER, identifier=ORPHANS_IDENTIFIER, path=self.env.home)
        return Plan(
            app=app,
            artifacts=artifacts,
            created_at=iso_now(self.env),
            schema_version=SCHEMA_VERSION,
            zap_version=__version__,
            warnings=warnings,
        )
