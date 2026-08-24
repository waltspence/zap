"""Linux backend: discover apps from .desktop entries, Flatpak, and Snap."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from ..core import catalog as C
from ..core import identifiers as I
from ..core.models import App
from ..environment import Environment
from .base import Backend


class LinuxBackend(Backend):
    system = "linux"

    def locations(self) -> List[C.Location]:
        return C.linux_locations()

    def discover_apps(self) -> List[App]:
        apps: Dict[str, App] = {}

        for directory, managed in self._desktop_dirs():
            if not directory.is_dir():
                continue
            for entry in sorted(directory.glob("*.desktop")):
                app = self._read_desktop(entry, managed)
                if app is not None:
                    apps.setdefault(app.identifier.lower(), app)

        for app in self._flatpak_apps():
            apps.setdefault(app.identifier.lower(), app)
        for app in self._snap_apps():
            apps.setdefault(app.identifier.lower(), app)

        return sorted(apps.values(), key=lambda a: a.name.lower())

    # -- desktop entries ---------------------------------------------------

    def _desktop_dirs(self):
        return [
            (self.env.home / ".local/share/applications", None),
            (self.env.resolve("sys", "usr/share/applications"), "system"),
            (self.env.resolve("sys", "usr/local/share/applications"), "system"),
        ]

    def _read_desktop(self, path: Path, managed: Optional[str]) -> Optional[App]:
        entry = _parse_desktop_entry(path)
        if entry.get("Type") not in (None, "Application"):
            return None
        if entry.get("NoDisplay", "").strip().lower() == "true":
            # still a real app; keep it but note it is hidden from menus
            pass
        desktop_id = path.name[: -len(".desktop")]
        name = entry.get("Name") or desktop_id
        exec_line = entry.get("Exec", "")
        exec_bin = _exec_basename(exec_line)
        wmclass = entry.get("StartupWMClass")
        bundle_id = desktop_id if I.is_valid_bundle_id(desktop_id) else None
        return App(
            name=name,
            identifier=bundle_id or desktop_id,
            path=path,
            bundle_id=bundle_id,
            display_name=name,
            executable=exec_bin or wmclass,
            version=entry.get("Version"),
            embedded_bundle_ids=[],
            protected=False,
            degraded=bundle_id is None,
            managed_by=managed,
        )

    # -- flatpak -----------------------------------------------------------

    def _flatpak_apps(self) -> List[App]:
        out: List[App] = []
        roots = [
            self.env.home / ".local/share/flatpak/app",
            self.env.resolve("sys", "var/lib/flatpak/app"),
        ]
        for root in roots:
            if not root.is_dir():
                continue
            for child in sorted(_listdir(root)):
                if not I.is_valid_bundle_id(child):
                    continue
                out.append(
                    App(
                        name=child.split(".")[-1],
                        identifier=child,
                        path=root / child,
                        bundle_id=child,
                        display_name=child,
                        executable=child.split(".")[-1],
                        embedded_bundle_ids=[],
                        managed_by="flatpak",
                    )
                )
        return out

    # -- snap --------------------------------------------------------------

    def _snap_apps(self) -> List[App]:
        out: List[App] = []
        root = self.env.home / "snap"
        if not root.is_dir():
            return out
        for child in sorted(_listdir(root)):
            if child.startswith("."):
                continue
            out.append(
                App(
                    name=child,
                    identifier=child,
                    path=root / child,
                    bundle_id=None,
                    display_name=child,
                    executable=child,
                    embedded_bundle_ids=[],
                    degraded=True,
                    managed_by="snap",
                )
            )
        return out


def _listdir(p: Path) -> List[str]:
    try:
        return os.listdir(p)
    except OSError:
        return []


def _exec_basename(exec_line: str) -> Optional[str]:
    if not exec_line:
        return None
    # Drop field codes like %U, %f and take the first token's basename.
    tokens = [t for t in exec_line.split() if not t.startswith("%")]
    if not tokens:
        return None
    first = tokens[0]
    if first in ("env", "sh", "bash", "/usr/bin/env") and len(tokens) > 1:
        first = tokens[1]
    return os.path.basename(first)


def _parse_desktop_entry(path: Path) -> Dict[str, str]:
    """Return the keys of the ``[Desktop Entry]`` group (locale keys ignored)."""
    data: Dict[str, str] = {}
    in_group = False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_group = line == "[Desktop Entry]"
            continue
        if not in_group or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if "[" in key:  # a localized key like Name[de]
            continue
        data.setdefault(key, value.strip())
    return data
