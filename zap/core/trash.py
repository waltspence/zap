"""Recoverable removal: move artifacts to a trash on their own volume.

Two backends implement the same :class:`Trash` interface:

* :class:`XDGTrash` — the freedesktop.org Trash spec used on Linux
  (``~/.local/share/Trash`` for the home volume, ``<mount>/.Trash-<uid>`` for
  others), including atomically-reserved ``.trashinfo`` metadata so a restore
  works from any spec-compliant file manager too.
* :class:`MacTrash` — ``~/.Trash`` for the home volume and
  ``<mount>/.Trashes/<uid>`` for others, matching Finder's own layout.

Both always place the trash on the *same filesystem* as the item, so the move
is an atomic rename; :func:`fsutil.safe_move` handles the rare cross-device
fallback safely. zap additionally records its own undo manifest (see
``history``), so recoverability never depends on the trash metadata alone.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from ..environment import Environment, mount_point_of
from . import fsutil


class TrashError(Exception):
    pass


@dataclass
class TrashedItem:
    original: str  # absolute original path
    location: str  # where it now lives inside the trash
    method: str = "trash"  # "trash" (recoverable) — purge is handled elsewhere
    info: Optional[str] = None  # .trashinfo path, if any

    @property
    def reversible(self) -> bool:
        return self.method == "trash"

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "location": self.location,
            "method": self.method,
            "info": self.info,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TrashedItem":
        return cls(
            original=d["original"],
            location=d["location"],
            method=d.get("method", "trash"),
            info=d.get("info"),
        )


class Trash:
    def __init__(self, env: Environment):
        self.env = env

    # subclasses implement these
    def _files_and_info_dirs(self, src: Path) -> "tuple[Path, Optional[Path], Optional[Path]]":
        """Return (files_dir, info_dir_or_None, topdir_or_None) for ``src``."""
        raise NotImplementedError

    def _write_info(self, info_dir: Path, name: str, original: Path, topdir: Optional[Path]) -> str:
        raise NotImplementedError

    # -- shared behaviour --------------------------------------------------

    def trash(self, path: Path) -> TrashedItem:
        original = Path(os.path.normpath(os.path.abspath(str(path))))
        files_dir, info_dir, topdir = self._files_and_info_dirs(original)
        fsutil.ensure_dir(files_dir)
        if info_dir is not None:
            fsutil.ensure_dir(info_dir)

        base = original.name or "item"
        # Try candidate names until the move succeeds, closing the small gap
        # between "name is free" and "we moved into it".
        attempt = 0
        while True:
            name = base if attempt == 0 else f"{base}.{attempt}"
            info_path: Optional[str] = None
            if info_dir is not None:
                # Atomically reserve the name via O_EXCL on the .trashinfo file.
                try:
                    info_path = self._write_info(info_dir, name, original, topdir)
                except FileExistsError:
                    attempt += 1
                    if attempt > 10000:
                        raise TrashError("could not reserve a trash name")
                    continue
            dst = files_dir / name
            if dst.exists() or dst.is_symlink():
                # files/ already holds this name (info reserved a fresh one, or
                # macOS has no info file) — clean up and try the next.
                if info_path:
                    _silent_unlink(Path(info_path))
                attempt += 1
                if attempt > 10000:
                    raise TrashError("could not find a free trash name")
                continue
            try:
                fsutil.safe_move(self.env, original, dst)
            except FileExistsError:
                if info_path:
                    _silent_unlink(Path(info_path))
                attempt += 1
                continue
            except Exception:
                if info_path:
                    _silent_unlink(Path(info_path))
                raise
            return TrashedItem(
                original=str(original), location=str(dst), method="trash", info=info_path
            )

    def restore(self, original: Path, location: Path, info: Optional[str] = None) -> Path:
        original = Path(os.path.normpath(os.path.abspath(str(original))))
        if original.exists() or original.is_symlink():
            raise TrashError(f"restore target already exists: {original}")
        if not (location.exists() or location.is_symlink()):
            raise TrashError(f"trashed item is gone: {location}")
        fsutil.ensure_dir(original.parent)
        fsutil.safe_move(self.env, location, original)
        if info:
            _silent_unlink(Path(info))
        return original

    def _on_home_volume(self, src: Path) -> bool:
        return self.env.device_of(src) == self.env.device_of(self.env.home)


class XDGTrash(Trash):
    def _home_trash(self) -> Path:
        return self.env.xdg_data_home() / "Trash"

    def _files_and_info_dirs(self, src: Path):
        if self._on_home_volume(src):
            root = self._home_trash()
            return (root / "files", root / "info", None)
        mp = mount_point_of(self.env, src)
        root = mp / f".Trash-{self.env.uid}"
        return (root / "files", root / "info", mp)

    def _write_info(self, info_dir: Path, name: str, original: Path, topdir):
        info_path = info_dir / f"{name}.trashinfo"
        # For a per-volume trash the spec stores the path relative to the
        # volume's top directory; for the home trash it is absolute.
        if topdir is not None:
            try:
                rel = original.relative_to(topdir)
                path_value = quote(str(rel))
            except ValueError:
                path_value = quote(str(original))
        else:
            path_value = quote(str(original))
        deletion = self.env.now().strftime("%Y-%m-%dT%H:%M:%S")
        body = f"[Trash Info]\nPath={path_value}\nDeletionDate={deletion}\n"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(info_path, flags, 0o600)  # raises FileExistsError if taken
        try:
            os.write(fd, body.encode("utf-8"))
        finally:
            os.close(fd)
        return str(info_path)


class MacTrash(Trash):
    def _files_and_info_dirs(self, src: Path):
        if self._on_home_volume(src):
            return (self.env.home / ".Trash", None, None)
        mp = mount_point_of(self.env, src)
        return (mp / ".Trashes" / str(self.env.uid), None, mp)

    def _write_info(self, info_dir, name, original, topdir):  # pragma: no cover
        raise NotImplementedError("mac trash uses no info files")


def get_trash(env: Environment) -> Trash:
    return MacTrash(env) if env.is_darwin else XDGTrash(env)


def _silent_unlink(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError as exc:
        if exc.errno not in (errno.ENOENT,):
            pass
