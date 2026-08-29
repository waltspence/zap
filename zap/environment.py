"""The injected execution environment.

Everything the engine needs to know about *where* it is running — filesystem
roots, the current user, the clock, device identity — is funneled through an
``Environment`` object. Production code builds one from the real process
(:func:`current_environment`); tests build one pointing at a throwaway
directory tree. This is the single seam that makes macOS-specific logic
unit-testable on a Linux CI box.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Logical bases a catalog location can be anchored to.
HOME = "home"  # the user's home directory
SYS = "sys"  # the system root ("/" in production, a temp dir in tests)


@dataclass
class Environment:
    """A fully-injected view of the world the engine operates in."""

    system: str  # "darwin" or "linux"
    home: Path  # the *invoking* user's home (SUDO_USER-aware)
    sysroot: Path  # absolute system paths are resolved under here
    state_home: Path  # where zap stores history/manifests/locks
    uid: int = 0  # the *invoking* user's uid (SUDO_UID-aware); drives trash dirs
    gid: Optional[int] = None
    is_root: bool = False
    is_tty: bool = False
    data_home: Optional[Path] = None  # XDG_DATA_HOME; None -> ~/.local/share
    # Injected so tests can fake cross-volume layouts and freeze time.
    device_of: Callable[[Path], int] = field(
        default=lambda p: _safe_device(p), repr=False
    )
    now: Callable[[], datetime] = field(
        default=lambda: datetime.now(timezone.utc), repr=False
    )

    # -- path resolution ---------------------------------------------------
    def base_dir(self, base: str) -> Path:
        if base == HOME:
            return self.home
        if base == SYS:
            return self.sysroot
        raise ValueError(f"unknown location base: {base!r}")

    def resolve(self, base: str, *parts: str) -> Path:
        root = self.base_dir(base)
        p = root
        for part in parts:
            p = p / part
        return p

    @property
    def is_darwin(self) -> bool:
        return self.system == "darwin"

    @property
    def is_linux(self) -> bool:
        return self.system == "linux"

    def xdg_data_home(self) -> Path:
        return self.data_home if self.data_home is not None else self.home / ".local" / "share"


def _safe_device(path: Path) -> int:
    """st_dev of ``path`` or of its nearest existing ancestor (or -1)."""
    p = Path(path)
    for candidate in (p, *p.parents):
        try:
            # lstat: a leaf symlink lives on *its own* volume, not its target's.
            return os.lstat(candidate).st_dev
        except OSError:
            continue
    return -1


def mount_point_of(env: Environment, path: Path) -> Path:
    """The top directory of the filesystem that ``path`` lives on.

    Walks upward while the device number stays constant, using the
    environment's (possibly injected) ``device_of``.
    """
    p = Path(path)
    # Anchor to the nearest existing ancestor so a not-yet-created leaf works.
    while not os.path.lexists(p) and p != p.parent:
        p = p.parent
    dev = env.device_of(p)
    top = p
    for parent in p.parents:
        if env.device_of(parent) != dev:
            break
        top = parent
    return top


def _invoking_user_home(system: str) -> Path:
    """Resolve the *human's* home even when running under sudo.

    When zap is invoked with sudo, ``$HOME`` and ``expanduser`` may point at
    root's home, which would send trash and history to the wrong place and
    miss the user's actual leftovers. Prefer ``$SUDO_USER``'s home.
    """
    sudo_user = os.environ.get("SUDO_USER")
    running_as_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if sudo_user and running_as_root:
        try:
            import pwd

            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except (KeyError, ImportError):
            pass
    home = os.environ.get("HOME")
    if home:
        return Path(home)
    return Path(os.path.expanduser("~"))


def _invoking_ids(euid: int) -> "tuple[int, Optional[int]]":
    """(uid, gid) of the human who ran zap, even under sudo.

    Per-volume trash directories are keyed by uid (``.Trashes/<uid>``,
    ``.Trash-<uid>``); using root's would hide the items from the user's own
    Trash and make ``undo`` need root again.
    """
    if euid == 0:
        sudo_uid = os.environ.get("SUDO_UID")
        if sudo_uid and sudo_uid.isdigit():
            sudo_gid = os.environ.get("SUDO_GID")
            return int(sudo_uid), (int(sudo_gid) if sudo_gid and sudo_gid.isdigit() else None)
    return euid, None


def _default_state_home(system: str, home: Path) -> Path:
    if system == "darwin":
        return home / "Library" / "Application Support" / "zap"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "zap"
    return home / ".local" / "state" / "zap"


def current_environment(
    *,
    system: Optional[str] = None,
    stdout_isatty: Optional[bool] = None,
) -> Environment:
    """Build an :class:`Environment` from the real running process."""
    sysname = system or ("darwin" if sys.platform == "darwin" else "linux")
    home = _invoking_user_home(sysname)
    euid = os.geteuid() if hasattr(os, "geteuid") else 0
    is_root = euid == 0
    uid, gid = _invoking_ids(euid)
    if stdout_isatty is None:
        try:
            stdout_isatty = sys.stdout.isatty()
        except (ValueError, OSError):
            stdout_isatty = False
    xdg_data = os.environ.get("XDG_DATA_HOME")
    return Environment(
        system=sysname,
        home=home,
        sysroot=Path("/"),
        state_home=_default_state_home(sysname, home),
        uid=uid,
        gid=gid,
        is_root=is_root,
        is_tty=bool(stdout_isatty),
        data_home=Path(xdg_data) if xdg_data else None,
    )
