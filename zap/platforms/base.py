"""Backend interface and the platform-agnostic pieces of app handling."""

from __future__ import annotations

import os
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
    fuzzy: bool = False  # matched only by substring, never exact

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
            return ResolveResult(subs[0], [], fuzzy=True)
        if len(subs) > 1:
            return ResolveResult(None, sorted(subs, key=lambda a: str(a.path)))

        return ResolveResult(None, [])

    def make_scan_input(self, app: App, apps: List[App]) -> ScanInput:
        usable = I.usable_bundle_id(app.bundle_id)
        # The executable name is deliberately NOT a name key: a launcher whose
        # Exec= is "firefox" must not claim Firefox's profile.
        names = matching.build_name_set(app.name, app.display_name, _bundle_name(app))
        execs = {
            I.normalize_casefold(x)
            for x in (app.executable, app.name)
            if x and I.is_safe_name_component(x)
        }
        same_path = os.path.normpath(os.path.abspath(str(app.path)))
        others_apps = [
            a for a in apps
            if a is not app and os.path.normpath(os.path.abspath(str(a.path))) != same_path
        ]
        others = {a.bundle_id for a in others_apps if a.bundle_id and I.is_valid_bundle_id(a.bundle_id)}
        other_names = set()
        for a in others_apps:
            other_names |= matching.build_name_set(a.name, a.display_name)
        other_names -= names  # a second copy of *this* app shares its names
        other_vendor_dirs = set()
        for a in others_apps:
            ob = I.usable_bundle_id(a.bundle_id)
            vp = I.vendor_product_from_bundle_id(ob) if ob else None
            if vp:
                other_vendor_dirs.add((I.normalize_casefold(vp[0]), I.normalize_casefold(vp[1])))
        keys = matching.AppKeys(
            bundle_id=usable,
            team_id=app.team_id if I.is_valid_team_id(app.team_id) else None,
            names=names,
            executables=execs,
            other_bundle_ids=others,
            other_names=other_names,
            other_vendor_dirs=other_vendor_dirs,
            case_insensitive=self.env.is_darwin,
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
            # An embedded id that is another installed app (or lives in its
            # id subtree) is that app's, not ours.
            if matching.embedded_belongs_to_other(eid, keys):
                continue
            shared = I.is_shared_component_id(eid) or eid in app.shared_embedded_ids
            embedded.append(EmbeddedSource(bundle_id=eid, shared=shared))
        return ScanInput(keys=keys, embedded=embedded)


def _bundle_name(app: App) -> Optional[str]:
    # CFBundleName is often stored separately from the display name; the App
    # model folds it into display_name when present, so nothing extra here.
    return None
