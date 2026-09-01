"""Up-front permission checks, run before an uninstall acts.

Failing mid-run leaves a half-removed app; these checks refuse *before*
touching anything and say exactly what to grant. Each blocker is a dict:
``{"code", "message", "paths"}``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, List, Optional

from ..environment import Environment
from .models import Artifact

# Directories under $HOME/Library that macOS TCC protects even against the
# owning user: moving them needs Full Disk Access for the invoking app.
TCC_PROTECTED = (
    "Library/Containers",
    "Library/Group Containers",
    "Library/Cookies",
    "Library/Mail",
    "Library/Safari",
    "Library/HomeKit",
    "Library/IdentityServices",
    "Library/Messages",
)

FDA_SETTINGS_HINT = (
    "grant Full Disk Access to this app/terminal in System Settings > "
    "Privacy & Security > Full Disk Access, restart it, then retry"
)


def has_full_disk_access(env: Environment) -> Optional[bool]:
    """True/False when determinable, None off-macOS.

    Probe: the user TCC database is readable only with Full Disk Access.
    """
    if sys.platform != "darwin":
        return None
    probe = Path(env.home) / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
    try:
        with open(probe, "rb"):
            return True
    except PermissionError:
        return False
    except OSError:
        return None  # missing/odd layout: don't block on an inconclusive probe


def _is_tcc_protected(env: Environment, path: Path) -> bool:
    p = str(path)
    home = str(env.home)
    return any(p.startswith(os.path.join(home, d) + os.sep) or p == os.path.join(home, d)
               for d in TCC_PROTECTED)


def check(env: Environment, selected: List[Artifact],
          fda_probe: Optional[Callable[[Environment], Optional[bool]]] = None) -> List[dict]:
    """Return blockers for artifacts that will fail with EPERM if we proceed."""
    blockers: List[dict] = []

    if env.uid != 0:
        need_root: List[str] = []
        for art in selected:
            if art.requires_root:
                need_root.append(str(art.path))
                continue
            try:
                if os.lstat(art.path).st_uid not in (env.uid, os.geteuid()):
                    need_root.append(str(art.path))
            except OSError:
                pass
        if need_root:
            blockers.append({
                "code": "needs_root",
                "message": "these items are owned by another user (App Store installs are "
                           "owned by root) — rerun with sudo, or deselect them",
                "paths": sorted(need_root),
            })

    if sys.platform == "darwin":
        tcc_paths = sorted(str(a.path) for a in selected if _is_tcc_protected(env, Path(a.path)))
        if tcc_paths and (fda_probe or has_full_disk_access)(env) is False:
            blockers.append({
                "code": "needs_full_disk_access",
                "message": "these items are under macOS privacy protection — " + FDA_SETTINGS_HINT
                           + ", or deselect them",
                "paths": tcc_paths,
            })

    return blockers
