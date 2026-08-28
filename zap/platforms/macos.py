"""macOS backend: discover .app bundles and everything embedded in them."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from ..core import catalog as C
from ..core import identifiers as I
from ..core import codesign, plist
from ..core.models import App
from .base import Backend

# Subpaths inside a .app bundle that hold *their own* bundle ids — the source
# of "leftovers from an app you thought you deleted" (Keystone, AutoUpdate,
# Sparkle, XPC/appex containers).
_EMBEDDED_GLOBS = [
    ("Contents/Library/LoginItems", (".app",)),
    ("Contents/XPCServices", (".xpc",)),
    ("Contents/PlugIns", (".appex", ".pluginkit")),
    ("Contents/Helpers", ("",)),
    ("Contents/Library/LaunchServices", ("",)),
    ("Contents/Frameworks", (".framework",)),
]

# launchd plists shipped inside the bundle (SMAppService / SMJobBless helpers).
# Their ``Label`` is the id the installed copy in ~/Library/LaunchAgents or
# /Library/LaunchDaemons is named by.
_EMBEDDED_LAUNCHD = ("Contents/Library/LaunchAgents", "Contents/Library/LaunchDaemons")


class MacBackend(Backend):
    system = "darwin"

    def locations(self) -> List[C.Location]:
        return C.mac_locations()

    def discover_apps(self) -> List[App]:
        apps: List[App] = []
        seen: set = set()
        for base, sub in self.application_roots():
            root = self.env.resolve(base, *[s for s in sub.split("/") if s])
            for app_path in self._find_bundles(root):
                key = os.path.normpath(str(app_path))
                if key in seen:
                    continue
                seen.add(key)
                app = self._read_app(app_path)
                if app is not None:
                    apps.append(app)
        apps.sort(key=lambda a: a.name.lower())
        return apps

    # -- helpers ----------------------------------------------------------

    def _find_bundles(self, root: Path, max_depth: int = 2) -> List[Path]:
        """Collect ``*.app`` bundles under ``root`` without descending into them."""
        out: List[Path] = []
        if not root.is_dir():
            return out
        stack = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                entries = sorted(os.scandir(current), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                if not _is_dir_nofollow(entry):
                    continue
                if entry.name.endswith(".app"):
                    out.append(Path(entry.path))
                    continue  # never descend into a bundle
                if depth < max_depth and not entry.name.startswith("."):
                    stack.append((Path(entry.path), depth + 1))
        return out

    def _read_app(self, app_path: Path) -> Optional[App]:
        info = plist.read_bundle_info(app_path)
        name = app_path.name[:-4] if app_path.name.endswith(".app") else app_path.name
        bundle_id = info.get("CFBundleIdentifier")
        bundle_id = bundle_id if isinstance(bundle_id, str) and bundle_id else None
        display = info.get("CFBundleDisplayName") or info.get("CFBundleName")
        executable = info.get("CFBundleExecutable")
        version = info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")
        usable = I.usable_bundle_id(bundle_id)
        embedded = self._embedded_ids(app_path, bundle_id)
        protected = bool(bundle_id and I.is_apple_system_id(bundle_id))
        managed = "setapp" if "/Setapp/" in str(app_path) else None
        return App(
            name=name,
            identifier=usable or name,
            path=app_path,
            bundle_id=bundle_id,
            display_name=display if isinstance(display, str) else None,
            executable=executable if isinstance(executable, str) else None,
            team_id=codesign.team_id_for_bundle(app_path),
            version=str(version) if version is not None else None,
            embedded_bundle_ids=embedded,
            protected=protected,
            degraded=usable is None,
            managed_by=managed,
        )

    def _embedded_ids(self, app_path: Path, main_id: Optional[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for sub, _exts in _EMBEDDED_GLOBS:
            d = app_path / sub
            if not d.is_dir():
                continue
            try:
                entries = sorted(os.scandir(d), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                bid: Optional[str] = None
                if _is_dir_nofollow(entry):
                    bid = plist.read_bundle_id(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    # LaunchServices privileged helpers are often named by their
                    # bundle id directly.
                    cand = entry.name
                    if I.is_valid_bundle_id(cand):
                        bid = cand
                if bid and I.is_valid_bundle_id(bid):
                    if main_id and I.normalize_casefold(bid) == I.normalize_casefold(main_id):
                        continue
                    k = I.normalize_casefold(bid)
                    if k not in seen:
                        seen.add(k)
                        out.append(bid)
        for sub in _EMBEDDED_LAUNCHD:
            d = app_path / sub
            if not d.is_dir():
                continue
            for plist_path in sorted(d.glob("*.plist")):
                label = plist.read_plist(plist_path).get("Label")
                if not isinstance(label, str) or not I.is_valid_bundle_id(label):
                    continue
                if main_id and I.normalize_casefold(label) == I.normalize_casefold(main_id):
                    continue
                k = I.normalize_casefold(label)
                if k not in seen:
                    seen.add(k)
                    out.append(label)
        return out


def _is_dir_nofollow(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False
