"""Filesystem primitives: sizing, symlink-safe removal, crash-safe moves.

The move helpers are the second-biggest data-loss surface after the safety
guard. The rule they enforce everywhere: **never unlink the source before the
destination copy is complete and durable.** Same-device moves are atomic
renames; cross-device moves copy, fsync, verify, and only then remove the
source — and abort leaving the source intact on any error.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from pathlib import Path
from typing import Tuple

from ..environment import Environment


def tree_size(path: Path) -> Tuple[int, int]:
    """Return ``(total_bytes, item_count)`` for ``path``.

    Permission-tolerant (skips what it cannot stat), never follows symlinks
    (a symlink counts as its own small size, its target is not traversed).
    """
    try:
        st = os.lstat(path)
    except OSError:
        return (0, 0)
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        return (st.st_size, 1)

    total = 0
    count = 0
    stack = [str(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        est = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    count += 1
                    total += est.st_size
                    if stat.S_ISDIR(est.st_mode) and not stat.S_ISLNK(est.st_mode):
                        stack.append(entry.path)
        except OSError:
            continue
    return (total, count)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def remove_tree(path: Path) -> None:
    """Permanently remove ``path`` without ever following a symlink.

    A symlink (even to a directory) is unlinked, not traversed. ``shutil.rmtree``
    on 3.8+ is itself resistant to symlink attacks inside the tree.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        os.unlink(path)
        return
    shutil.rmtree(path)


def ensure_dir(path: Path, mode: int = 0o700) -> None:
    os.makedirs(path, mode=mode, exist_ok=True)


def _fsync_path(path: Path) -> None:
    """Best-effort fsync of a file or directory so a move survives a crash."""
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY") and os.path.isdir(path):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def fsync_parent(path: Path) -> None:
    _fsync_path(path.parent)


def same_device(env: Environment, a: Path, b: Path) -> bool:
    return env.device_of(a) == env.device_of(b)


def safe_move(env: Environment, src: Path, dst: Path) -> None:
    """Move ``src`` to ``dst`` (which must not already exist) without data loss.

    * Same device -> atomic ``os.rename``.
    * Different device -> copy (preserving symlinks), fsync, verify, then remove
      the source. On any failure the partial destination is cleaned up and the
      source is left untouched.
    """
    if dst.exists() or dst.is_symlink():
        raise FileExistsError(f"destination already exists: {dst}")

    if same_device(env, src, dst.parent):
        try:
            os.rename(src, dst)
            fsync_parent(dst)
            return
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            # Devices reported same but kernel disagrees; fall through to copy.

    _cross_device_move(src, dst)


def _cross_device_move(src: Path, dst: Path) -> None:
    lst = os.lstat(src)
    if stat.S_ISLNK(lst.st_mode):
        linkto = os.readlink(src)
        os.symlink(linkto, dst)
        os.unlink(src)
        fsync_parent(dst)
        return

    if stat.S_ISDIR(lst.st_mode):
        src_bytes, _ = tree_size(src)
        try:
            shutil.copytree(src, dst, symlinks=True)
            dst_bytes, _ = tree_size(dst)
            if dst_bytes < src_bytes:
                raise OSError("cross-device copy verification failed (size shrank)")
            _fsync_path(dst)
        except Exception:
            shutil.rmtree(dst, ignore_errors=True)
            raise  # source is untouched
        shutil.rmtree(src)
        fsync_parent(dst)
        return

    # Regular file.
    try:
        shutil.copy2(src, dst)
        if os.path.getsize(dst) != lst.st_size:
            raise OSError("cross-device copy verification failed (size mismatch)")
        _fsync_path(dst)
    except Exception:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise  # source is untouched
    os.unlink(src)
    fsync_parent(dst)
