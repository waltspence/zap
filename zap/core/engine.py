"""The orchestration layer: discover, scan→plan, select, execute, orphans.

This is the only module that both reads *and* writes the filesystem, and it is
deliberately paranoid on the write path: every artifact is re-checked by the
safety guard and re-fingerprinted for drift immediately before it is touched,
the whole operation holds an advisory lock, and each removal is journaled the
instant it happens.
"""

from __future__ import annotations

import os
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
    LockBusy,
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
    Plan,
    artifact_id_for,
)
from .safety import SafetyGuard, build_allowed_roots
from .scanner import Scanner
from .trash import get_trash


def iso_now(env: Environment) -> str:
    return env.now().strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ExecutionReport:
    operation_id: Optional[str]
    dry_run: bool
    method: str
    results: List[ActionResult] = field(default_factory=list)

    def summary(self) -> dict:
        counts = {}
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

    def to_dict(self) -> dict:
        return {
            **self.summary(),
            "results": [r.to_dict() for r in self.results],
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
            self._apps = self.backend.discover_apps()
        return self._apps

    def resolve(self, selector: str) -> ResolveResult:
        return self.backend.resolve(self.list_apps(), selector)

    def _guard(self, app: Optional[App] = None) -> SafetyGuard:
        roots = build_allowed_roots(self.env, self.backend.all_roots())
        if app is not None:
            # Allow the app bundle/binary's own directory as a root so the
            # bundle itself can be removed even from an unusual location.
            roots.append(app.path.parent)
        return SafetyGuard(self.env, roots)

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
        s = str(app.path)
        if self.env.is_linux and (s.startswith("/opt") or s.startswith("/usr")):
            return RISK_ELEVATED
        return RISK_NORMAL

    def _bundle_requires_root(self, app: App) -> bool:
        if self.env.is_linux:
            s = str(app.path)
            return s.startswith("/opt") or s.startswith("/usr")
        return False

    def _apply_selection_tiers(self, artifacts, *, aggressive: bool, include_low: bool) -> None:
        """Set ``auto_selected`` on the default action set for a plan.

        Default: exact bundle-id, non-shared, non-root, removable.
        --aggressive: also derived-confidence (prefix / vendor / embedded).
        --include-low: also heuristic (name-only).
        The app bundle is always in the default set (it is the whole point).
        """
        for a in artifacts:
            if a.category == CAT_SYSTEM_EXTENSION:
                a.auto_selected = False
                continue
            if a.category == CAT_APP_BUNDLE and not a.requires_root:
                a.auto_selected = True
                continue
            if a.shared or a.requires_root:
                a.auto_selected = False
                continue
            conf = a.match.confidence
            if conf == CONFIDENCE_EXACT:
                a.auto_selected = True
            elif conf == CONFIDENCE_DERIVED:
                a.auto_selected = bool(aggressive)
            elif conf == CONFIDENCE_HEURISTIC:
                a.auto_selected = bool(include_low)
            else:
                a.auto_selected = False

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
                    self._journal(writer, art, item.location, "trash", reversible=True)
                    report.results.append(
                        ActionResult(art.id, path, STATUS_REMOVED, reversible=True, method="trash", trash_path=item.location, size_bytes=art.size_bytes)
                    )
            except PermissionError:
                reason = "permission denied" + (" (needs root)" if art.requires_root else "")
                report.results.append(self._res(art, STATUS_FAILED, reason, method))
            except Exception as exc:  # noqa: BLE001 - report, continue with the rest
                report.results.append(self._res(art, STATUS_FAILED, str(exc), method))
        return report

    def _journal(self, writer, art, location, method, reversible) -> None:
        if writer is not None:
            writer.record(
                ManifestRecord(
                    artifact_id=art.id,
                    original=str(art.path),
                    location=location,
                    method=method,
                    reversible=reversible,
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
        for loc in self.backend.locations():
            if loc.strategy not in (ID_SCAN, ID_PATH):
                continue
            if loc.requires_root:
                continue  # don't rummage system dirs for orphans
            container = self.env.resolve(loc.base, *[s for s in loc.container.split("/") if s])
            if not container.is_dir():
                continue
            for child in sorted(os.listdir(container)):
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
        app = App(name="orphans", identifier="orphans", path=self.env.home)
        return Plan(
            app=app,
            artifacts=artifacts,
            created_at=iso_now(self.env),
            schema_version=SCHEMA_VERSION,
            zap_version=__version__,
            warnings=[],
        )
