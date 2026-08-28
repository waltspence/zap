"""Undo manifests, operation history, and an advisory run lock.

Each uninstall writes a JSONL manifest *incrementally* — a header line, then
one line per artifact the instant after it is moved, each flushed and fsync'd.
A crash or partial failure therefore always leaves a manifest from which every
already-moved item can be restored. ``undo`` replays a manifest in reverse.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, TextIO

from ..environment import Environment
from . import fsutil
from .models import content_id
from .trash import get_trash


def _history_dir(env: Environment) -> Path:
    return env.state_home / "history"


_OPERATION_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}(-[0-9]+)?$")


def is_valid_operation_id(value: str) -> bool:
    return bool(value) and bool(_OPERATION_ID_RE.match(value))


def make_operation_id(env: Environment, app_identifier: str, created_at: str) -> str:
    """Timestamp + short hash; a numeric suffix keeps same-second runs apart."""
    stamp = created_at.replace(":", "").replace("-", "").replace("T", "-").rstrip("Z")
    base = f"{stamp}-{content_id(app_identifier + created_at, 8)}"
    d = _history_dir(env)
    candidate = base
    n = 1
    while (d / f"{candidate}.jsonl").exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


@dataclass
class ManifestRecord:
    artifact_id: str
    original: str
    location: str  # trash path ("" for purged items)
    method: str  # "trash" | "purge"
    reversible: bool
    info: Optional[str] = None  # XDG .trashinfo path, if the backend wrote one

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "original": self.original,
            "location": self.location,
            "method": self.method,
            "reversible": self.reversible,
            "info": self.info,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestRecord":
        return cls(
            artifact_id=d.get("artifact_id", ""),
            original=d["original"],
            location=d.get("location", ""),
            method=d.get("method", "trash"),
            reversible=bool(d.get("reversible", d.get("method") == "trash")),
            info=d.get("info"),
        )


class ManifestWriter:
    """Append-and-fsync JSONL manifest for a single operation."""

    def __init__(self, env: Environment, operation_id: str, header: dict):
        self.env = env
        self.operation_id = operation_id
        self.path = _history_dir(env) / f"{operation_id}.jsonl"
        self._fh: Optional[TextIO] = None
        self._header = header
        self._records = 0

    def __enter__(self) -> "ManifestWriter":
        fsutil.ensure_dir(self.path.parent)
        fsutil.chown_to_user(self.env, self.path.parent)
        self._fh = open(self.path, "x", encoding="utf-8")
        fsutil.chown_to_user(self.env, self.path)
        self._write({"type": "header", **self._header})
        return self

    def record(self, rec: ManifestRecord) -> None:
        self._write({"type": "record", **rec.to_dict()})
        self._records += 1

    def _write(self, obj: dict) -> None:
        assert self._fh is not None
        self._fh.write(json.dumps(obj, sort_keys=True) + "\n")
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is not None:
            try:
                self._write({"type": "footer", "records": self._records})
            finally:
                self._fh.close()
                self._fh = None
        # If nothing was recorded, don't leave an empty manifest lying around.
        if self._records == 0:
            try:
                os.unlink(self.path)
            except OSError:
                pass


@dataclass
class Operation:
    operation_id: str
    header: dict
    records: List[ManifestRecord]

    @property
    def reversible_count(self) -> int:
        return sum(1 for r in self.records if r.reversible and r.location)

    def to_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            **self.header,
            "item_count": len(self.records),
            "reversible_count": self.reversible_count,
        }


def read_operation(env: Environment, operation_id: str) -> Optional[Operation]:
    if not is_valid_operation_id(operation_id):
        return None
    path = _history_dir(env) / f"{operation_id}.jsonl"
    if not path.is_file():
        return None
    header: dict = {}
    records: List[ManifestRecord] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            # A torn line from a crash mid-write; every complete record before
            # it is still restorable, so keep going rather than lose the op.
            continue
        kind = obj.get("type")
        if kind == "header":
            header = {k: v for k, v in obj.items() if k != "type"}
        elif kind == "record":
            try:
                records.append(ManifestRecord.from_dict(obj))
            except (KeyError, TypeError, ValueError):
                continue
    if not header and not records:
        return None
    return Operation(operation_id=operation_id, header=header, records=records)


def list_operations(env: Environment) -> List[Operation]:
    d = _history_dir(env)
    if not d.is_dir():
        return []
    ops: List[Operation] = []
    for path in d.glob("*.jsonl"):
        op = read_operation(env, path.name[: -len(".jsonl")])
        if op is not None:
            ops.append(op)
    ops.sort(key=lambda o: o.operation_id, reverse=True)
    return ops


def undo_operation(env: Environment, operation_id: str) -> List[dict]:
    op = read_operation(env, operation_id)
    if op is None:
        raise FileNotFoundError(f"no such operation: {operation_id}")
    trash = get_trash(env)
    results: List[dict] = []
    # Replay in reverse order of removal. Execution sorts by path, so a parent
    # is always trashed before anything beneath it (which then reads "already
    # gone"); reversing simply undoes the most recent move first.
    for rec in reversed(op.records):
        if not rec.reversible or not rec.location:
            results.append(
                {"original": rec.original, "status": "unrecoverable", "reason": "purged"}
            )
            continue
        try:
            trash.restore(Path(rec.original), Path(rec.location), rec.info)
            results.append({"original": rec.original, "status": "restored"})
        except Exception as exc:  # noqa: BLE001 - report, never abort the rest
            results.append(
                {"original": rec.original, "status": "failed", "reason": str(exc)}
            )
    return results


@contextmanager
def exclusive_lock(env: Environment):
    """Advisory whole-operation lock so concurrent zap runs cannot race."""
    fsutil.ensure_dir(env.state_home)
    fsutil.chown_to_user(env, env.state_home)
    lock_path = env.state_home / "lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fsutil.chown_to_user(env, lock_path)
    try:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX fallback
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LockBusy("another zap operation is in progress") from exc
        try:
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        os.close(fd)


class LockBusy(Exception):
    pass
