"""The safety guard: the last line of defence against deleting the wrong thing.

Every path the engine intends to remove passes through :meth:`SafetyGuard.check`
both at scan time and again at execute time. The guard's contract:

* The path must be lexically well-formed (absolute, normalized, no ``..``).
* Its **parent**, after ``realpath`` resolution, must sit strictly inside one
  of the allow-listed roots. We resolve the parent, never the leaf, so that a
  leaf that is itself a symlink is removed as a *link* (not followed), while a
  symlinked *ancestor* that escapes the root is rejected.
* The path must be strictly deeper than the root — a root itself, ``$HOME``,
  ``/`` or any top-level directory can never be a target.
* Anything lexically under ``$HOME`` must also *resolve* under the real
  ``$HOME``, so a catalog root that is itself a symlink out of the home
  directory cannot be used to reach elsewhere.

The allow-listed roots are derived from the very same catalog used for
discovery, so the guard and the discovery layer cannot drift apart.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from ..environment import Environment
from .identifiers import normalize_casefold


class SafetyGuard:
    def __init__(self, env: Environment, allowed_roots: Iterable[Path]):
        self.env = env
        self._case_insensitive = env.is_darwin
        # De-duplicate and resolve roots up front. A root that does not exist
        # is still a valid boundary (we compare lexically against it), but we
        # also keep a realpath'd copy for symlink-escape checks.
        self._home = Path(os.path.normpath(os.path.abspath(str(env.home))))
        self._state_home = Path(os.path.normpath(os.path.abspath(str(env.state_home))))
        self._real_home = Path(os.path.realpath(str(self._home)))
        self._roots: List[Path] = []
        seen = set()
        for r in allowed_roots:
            rp = Path(os.path.normpath(os.path.abspath(str(r))))
            key = self._casekey(str(rp))
            if key in seen:
                continue
            seen.add(key)
            self._roots.append(rp)

    # -- public API --------------------------------------------------------

    def check(self, path: Path) -> Tuple[bool, Optional[str]]:
        """Return ``(ok, reason)``. ``reason`` is ``None`` when ``ok``."""
        raw = str(path)
        if "\x00" in raw:
            return False, "path contains a NUL byte"
        if not os.path.isabs(raw):
            return False, "path is not absolute"

        normalized = os.path.normpath(os.path.abspath(raw))
        # normpath collapses '..'; if any remain the path was malformed.
        parts = Path(normalized).parts
        if ".." in parts:
            return False, "path escapes via '..'"
        if "" in parts[1:]:
            return False, "path has an empty component"

        candidate = Path(normalized)

        # Never the filesystem root or a bare top-level directory.
        if len(parts) <= 2:
            return False, "path is too shallow to be an app artifact"

        # zap's own history and lock live under a catalog root; never target them.
        if self._within(candidate, self._state_home) or self._within(self._state_home, candidate):
            return False, "path is zap's own state directory"

        # Find the allow-listed root this candidate claims to live under,
        # comparing lexically first (fast, and independent of what exists).
        root = self._lexical_root_for(candidate)
        if root is None:
            return False, "path is not under any allow-listed root"

        # The candidate must be strictly deeper than that root.
        if self._casekey(str(candidate)) == self._casekey(str(root)):
            return False, "path is an allow-listed root itself, not an artifact"

        # Resolve the PARENT (not the leaf) and require it to remain inside the
        # root. This is what defeats a symlinked ancestor pointing elsewhere.
        parent = candidate.parent
        try:
            real_parent = Path(os.path.realpath(str(parent)))
        except OSError as exc:
            return False, f"could not resolve parent: {exc}"

        real_root = Path(os.path.realpath(str(root)))
        if not self._within(real_parent, real_root) and not self._within(
            real_parent, root
        ):
            return False, "resolved parent escapes the allow-listed root (symlink?)"

        # The root itself may be a symlink (~/Library/Logs -> /Volumes/X). Then
        # the check above is vacuous, so additionally require anything under
        # the home directory to *resolve* under the real home directory.
        if self._within(candidate, self._home) and not self._within(real_parent, self._real_home):
            return False, "resolved parent leaves the home directory (symlinked ancestor)"

        return True, None

    def is_protected_app(self, path: Path, bundle_id: Optional[str], name: str) -> bool:
        """Whether an *application* must never be uninstalled by zap."""
        p = os.path.normpath(os.path.abspath(str(path)))
        system_dir = os.path.normpath(str(self.env.resolve("sys", "System")))
        if self._within(Path(p), Path(system_dir)) or self._casekey(
            p
        ) == self._casekey(system_dir):
            return True
        if bundle_id and bundle_id.lower().startswith("com.apple."):
            return True
        core = {
            "finder",
            "dock",
            "systemuiserver",
            "loginwindow",
            "windowserver",
            "spotlight",
        }
        if name.strip().lower() in core:
            return True
        return False

    # -- helpers -----------------------------------------------------------

    def _casekey(self, s: str) -> str:
        return normalize_casefold(s) if self._case_insensitive else s

    def _lexical_root_for(self, candidate: Path) -> Optional[Path]:
        best: Optional[Path] = None
        best_len = -1
        for root in self._roots:
            if self._within(candidate, root) and len(str(root)) > best_len:
                best = root
                best_len = len(str(root))
        return best

    def _within(self, child: Path, parent: Path) -> bool:
        """True if ``child`` == ``parent`` or is a descendant of it.

        Uses an ``os.sep``-terminated prefix so ``/a/b`` is not considered
        within ``/a/bc``. Case-folds on case-insensitive filesystems.
        """
        c = self._casekey(os.path.normpath(str(child)))
        p = self._casekey(os.path.normpath(str(parent)))
        if c == p:
            return True
        if not p.endswith(os.sep):
            p_pref = p + os.sep
        else:
            p_pref = p
        return c.startswith(p_pref)


def build_allowed_roots(env: Environment, roots: Iterable[Tuple[str, str]]) -> List[Path]:
    """Resolve ``(base, subpath)`` root templates against the environment."""
    out: List[Path] = []
    for base, subpath in roots:
        parts = [seg for seg in subpath.split("/") if seg]
        out.append(env.resolve(base, *parts))
    return out
