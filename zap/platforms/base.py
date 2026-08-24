"""Backend interface and the platform-agnostic pieces of app handling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..core import catalog as C
from ..core import identifiers as I
from ..core import matching
from ..core.models import App
from ..core.scanner import EmbeddedSource, ScanInput
from ..environment import Environment


@dataclass
class ResolveResult:
    app: Optional[App]
    candidates: List[App]  # populated when the selector is ambiguous

    @property
    def ambiguous(self) -> bool:
        return self.app is None and len(self.candidates) > 1


class Backend:
    system = "generic"

    def __init__(self, env: Environment):
        self.env = env

    # -- to be implemented by subclasses ----------------------------------

    def locations(self) -> List[C.Location]:
        raise NotImplementedError

    def discover_apps(self) -> List[App]:
        raise NotImplementedError

    # -- shared -----------------------------------------------------------

    def application_roots(self):
        return C.application_roots(self.system)

    def all_roots(self):
        return C.roots_for(self.locations(), self.system)

    def resolve(self, apps: List[App], selector: str) -> ResolveResult:
        sel = selector.strip()
        sel_fold = I.normalize_casefold(sel)

        # 1) exact bundle id
        by_bid = [a for a in apps if a.bundle_id and I.normalize_casefold(a.bundle_id) == sel_fold]
        if len(by_bid) == 1:
            return ResolveResult(by_bid[0], [])
        if len(by_bid) > 1:
            return ResolveResult(None, sorted(by_bid, key=lambda a: str(a.path)))

        # 2) path to the app bundle / binary
        if "/" in sel:
            selpath = str(Path(sel).expanduser())
            import os

            selnorm = os.path.normpath(os.path.abspath(selpath))
            by_path = [a for a in apps if os.path.normpath(os.path.abspath(str(a.path))) == selnorm]
            if len(by_path) == 1:
                return ResolveResult(by_path[0], [])

        # 3) exact name (with or without a trailing ".app")
        name_target = sel[:-4] if sel.lower().endswith(".app") else sel
        nt = I.normalize_casefold(name_target)
        by_name = [a for a in apps if I.normalize_casefold(a.name) == nt]
        if len(by_name) == 1:
            return ResolveResult(by_name[0], [])
        if len(by_name) > 1:
            return ResolveResult(None, sorted(by_name, key=lambda a: str(a.path)))

        # 4) fuzzy substring on name / display name
        subs = [
            a
            for a in apps
            if nt in I.normalize_casefold(a.name)
            or (a.display_name and nt in I.normalize_casefold(a.display_name))
        ]
        if len(subs) == 1:
            return ResolveResult(subs[0], [])
        if len(subs) > 1:
            return ResolveResult(None, sorted(subs, key=lambda a: str(a.path)))

        return ResolveResult(None, [])

    def make_scan_input(self, app: App, apps: List[App]) -> ScanInput:
        usable = I.usable_bundle_id(app.bundle_id)
        names = matching.build_name_set(
            app.name, app.display_name, app.executable, _bundle_name(app)
        )
        execs = {
            I.normalize_casefold(x)
            for x in (app.executable, app.name)
            if x and I.is_safe_name_component(x)
        }
        others = {
            a.bundle_id
            for a in apps
            if a.bundle_id and a is not app and I.is_valid_bundle_id(a.bundle_id)
        }
        keys = matching.AppKeys(
            bundle_id=usable,
            team_id=app.team_id if I.is_valid_team_id(app.team_id) else None,
            names=names,
            executables=execs,
            other_bundle_ids=others,
        )
        embedded = []
        seen = set()
        for eid in app.embedded_bundle_ids:
            if not I.is_valid_bundle_id(eid):
                continue
            if usable and I.normalize_casefold(eid) == I.normalize_casefold(usable):
                continue
            key = I.normalize_casefold(eid)
            if key in seen:
                continue
            seen.add(key)
            embedded.append(EmbeddedSource(bundle_id=eid, shared=I.is_shared_component_id(eid)))
        return ScanInput(keys=keys, embedded=embedded)


def _bundle_name(app: App) -> Optional[str]:
    # CFBundleName is often stored separately from the display name; the App
    # model folds it into display_name when present, so nothing extra here.
    return None
