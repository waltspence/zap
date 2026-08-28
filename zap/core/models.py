"""Core data types shared across the engine and the JSON/CLI surface.

Every object here serializes to a plain ``dict`` with a stable shape (the
agent-facing contract). IDs are content-addressed so that an agent's
``--only``/``--except`` selections and saved plans bind to the *same files*
across re-scans, and fingerprints capture enough state to detect drift (TOCTOU)
between scan and apply.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Enumerated string constants (kept as plain strings for clean JSON).
# ---------------------------------------------------------------------------

# How confident we are that an artifact belongs to the target app.
CONFIDENCE_EXACT = "exact"  # full bundle-id (or package-id) exact match
CONFIDENCE_DERIVED = "derived"  # dot-boundary prefix, or name corroborated by id
CONFIDENCE_HEURISTIC = "heuristic"  # name-only / vendor folder — review required
CONFIDENCE_ORDER = {CONFIDENCE_HEURISTIC: 0, CONFIDENCE_DERIVED: 1, CONFIDENCE_EXACT: 2}

# Blast-radius / privilege of removing an artifact.
RISK_SAFE = "safe"  # caches, logs, saved state — regenerated freely
RISK_NORMAL = "normal"  # preferences, app support, containers
RISK_ELEVATED = "elevated"  # system /Library, launch daemons, receipts (root)

# Artifact categories, each with a default risk (see CATEGORY_RISK below).
CAT_APP_BUNDLE = "app_bundle"
CAT_PREFERENCES = "preferences"
CAT_CACHES = "caches"
CAT_APP_SUPPORT = "application_support"
CAT_CONTAINERS = "containers"
CAT_GROUP_CONTAINERS = "group_containers"
CAT_SAVED_STATE = "saved_state"
CAT_LOGS = "logs"
CAT_COOKIES = "cookies"
CAT_HTTP_STORAGE = "http_storage"
CAT_WEBKIT = "webkit"
CAT_LAUNCH_AGENT = "launch_agent"
CAT_LAUNCH_DAEMON = "launch_daemon"
CAT_LOGIN_ITEM = "login_item"
CAT_RECEIPT = "receipt"
CAT_CRASH_REPORT = "crash_report"
CAT_PLUGIN = "plugin"
CAT_PRIVILEGED_HELPER = "privileged_helper"
CAT_APP_SCRIPTS = "application_scripts"
CAT_ICLOUD = "icloud"
CAT_SYSTEM_EXTENSION = "system_extension"
CAT_CONFIG = "config"  # Linux ~/.config style
CAT_STATE = "state"
CAT_OTHER = "other"

CATEGORY_RISK = {
    CAT_APP_BUNDLE: RISK_NORMAL,
    CAT_PREFERENCES: RISK_NORMAL,
    CAT_CACHES: RISK_SAFE,
    CAT_APP_SUPPORT: RISK_NORMAL,
    CAT_CONTAINERS: RISK_NORMAL,
    CAT_GROUP_CONTAINERS: RISK_NORMAL,
    CAT_SAVED_STATE: RISK_SAFE,
    CAT_LOGS: RISK_SAFE,
    CAT_COOKIES: RISK_NORMAL,
    CAT_HTTP_STORAGE: RISK_SAFE,
    CAT_WEBKIT: RISK_SAFE,
    CAT_LAUNCH_AGENT: RISK_NORMAL,
    CAT_LAUNCH_DAEMON: RISK_ELEVATED,
    CAT_LOGIN_ITEM: RISK_NORMAL,
    CAT_RECEIPT: RISK_ELEVATED,
    CAT_CRASH_REPORT: RISK_SAFE,
    CAT_PLUGIN: RISK_NORMAL,
    CAT_PRIVILEGED_HELPER: RISK_ELEVATED,
    CAT_APP_SCRIPTS: RISK_NORMAL,
    CAT_ICLOUD: RISK_NORMAL,
    CAT_SYSTEM_EXTENSION: RISK_ELEVATED,
    CAT_CONFIG: RISK_NORMAL,
    CAT_STATE: RISK_SAFE,
    CAT_OTHER: RISK_NORMAL,
}


def content_id(text: str, length: int = 16) -> str:
    """A stable short hex id derived from ``text`` (content-addressing)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def artifact_id_for(path: Path) -> str:
    """Content-addressed id for an artifact at ``path``.

    Uses the normalized absolute path (not realpath — we must not follow a
    leaf symlink here) so the id is stable and does not depend on scan order.
    """
    normalized = os.path.normpath(os.path.abspath(str(path)))
    return content_id(normalized)


# ---------------------------------------------------------------------------
# Match, Fingerprint, Artifact
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    """Why an artifact was attributed to the app."""

    key: str  # "bundle_id" | "bundle_id_prefix" | "name" | "vendor_folder" | "receipt" | ...
    value: str  # the concrete matched token
    confidence: str  # CONFIDENCE_*

    def to_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict) -> "Match":
        return cls(
            key=d.get("key", "unknown"),
            value=d.get("value", ""),
            confidence=d.get("confidence", CONFIDENCE_HEURISTIC),
        )


@dataclass(frozen=True)
class Fingerprint:
    """A cheap snapshot of a path's identity, for drift detection."""

    exists: bool
    is_symlink: bool
    size: int
    mtime_ns: int
    dev: int
    ino: int

    @classmethod
    def of(cls, path: Path) -> "Fingerprint":
        try:
            st = os.lstat(path)  # never follow the leaf symlink
        except OSError:
            return cls(False, False, 0, 0, 0, 0)
        return cls(
            exists=True,
            is_symlink=_is_symlink_mode(st.st_mode),
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            dev=st.st_dev,
            ino=st.st_ino,
        )

    def matches(self, other: "Fingerprint") -> bool:
        """Whether ``other`` is the same object we fingerprinted at scan time.

        Identity is (dev, ino) plus existence and link-ness; size/mtime are
        advisory and reported but not required to be identical (a cache can
        grow between scan and apply without being a different file).
        """
        if not (self.exists and other.exists):
            return self.exists == other.exists
        return (
            self.is_symlink == other.is_symlink
            and self.dev == other.dev
            and self.ino == other.ino
        )

    def to_dict(self) -> dict:
        return {
            "exists": self.exists,
            "is_symlink": self.is_symlink,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "dev": self.dev,
            "ino": self.ino,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Fingerprint":
        return cls(
            exists=bool(d.get("exists", False)),
            is_symlink=bool(d.get("is_symlink", False)),
            size=int(d.get("size", 0)),
            mtime_ns=int(d.get("mtime_ns", 0)),
            dev=int(d.get("dev", 0)),
            ino=int(d.get("ino", 0)),
        )


def _is_symlink_mode(mode: int) -> bool:
    import stat

    return stat.S_ISLNK(mode)


@dataclass
class Artifact:
    """A single filesystem object attributed to an app."""

    path: Path
    category: str
    match: Match
    risk: str
    size_bytes: int = 0
    item_count: int = 1
    shared: bool = False  # shared with other apps (group containers, updaters)
    reversible: bool = True
    requires_root: bool = False
    auto_selected: bool = False
    fingerprint: Optional[Fingerprint] = None
    notes: List[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return artifact_id_for(self.path)

    def to_dict(self, *, compact: bool = False) -> dict:
        """Full shape by default; ``compact`` drops what only a ``--plan``
        round-trip needs (fingerprint, per-item notes, item_count) and any
        flag that is at its default, so an agent reads only what matters."""
        if compact:
            d: dict = {
                "id": self.id,
                "path": str(self.path),
                "category": self.category,
                "match": self.match.key,
                "confidence": self.match.confidence,
                "risk": self.risk,
                "size_bytes": int(self.size_bytes),
                "auto_selected": self.auto_selected,
            }
            if self.shared:
                d["shared"] = True
            if self.requires_root:
                d["requires_root"] = True
            if not self.reversible:
                d["reversible"] = False
            return d
        return {
            "id": self.id,
            "path": str(self.path),
            "category": self.category,
            "match": self.match.to_dict(),
            "risk": self.risk,
            "size_bytes": int(self.size_bytes),
            "item_count": int(self.item_count),
            "shared": self.shared,
            "reversible": self.reversible,
            "requires_root": self.requires_root,
            "auto_selected": self.auto_selected,
            "fingerprint": self.fingerprint.to_dict() if self.fingerprint else None,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Artifact":
        fp = d.get("fingerprint")
        return cls(
            path=Path(d["path"]),
            category=d.get("category", CAT_OTHER),
            match=Match.from_dict(d.get("match", {})),
            risk=d.get("risk", RISK_NORMAL),
            size_bytes=int(d.get("size_bytes", 0)),
            item_count=int(d.get("item_count", 1)),
            shared=bool(d.get("shared", False)),
            reversible=bool(d.get("reversible", True)),
            requires_root=bool(d.get("requires_root", False)),
            auto_selected=bool(d.get("auto_selected", False)),
            fingerprint=Fingerprint.from_dict(fp) if fp else None,
            notes=list(d.get("notes", [])),
        )


@dataclass
class App:
    """A discovered installed application."""

    name: str
    identifier: str  # canonical selector: bundle id if available else name
    path: Path
    bundle_id: Optional[str] = None
    display_name: Optional[str] = None
    executable: Optional[str] = None
    team_id: Optional[str] = None
    version: Optional[str] = None
    embedded_bundle_ids: List[str] = field(default_factory=list)
    protected: bool = False
    degraded: bool = False  # no usable bundle id -> name-only, never auto-select
    managed_by: Optional[str] = None  # e.g. "dpkg", "flatpak", "snap", "setapp"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "identifier": self.identifier,
            "path": str(self.path),
            "bundle_id": self.bundle_id,
            "display_name": self.display_name,
            "executable": self.executable,
            "team_id": self.team_id,
            "version": self.version,
            "embedded_bundle_ids": list(self.embedded_bundle_ids),
            "protected": self.protected,
            "degraded": self.degraded,
            "managed_by": self.managed_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "App":
        return cls(
            name=d.get("name", ""),
            identifier=d.get("identifier", ""),
            path=Path(d.get("path", "")),
            bundle_id=d.get("bundle_id"),
            display_name=d.get("display_name"),
            executable=d.get("executable"),
            team_id=d.get("team_id"),
            version=d.get("version"),
            embedded_bundle_ids=list(d.get("embedded_bundle_ids", [])),
            protected=bool(d.get("protected", False)),
            degraded=bool(d.get("degraded", False)),
            managed_by=d.get("managed_by"),
        )


@dataclass
class Plan:
    """The result of a scan: an app and everything attributed to it."""

    app: App
    artifacts: List[Artifact]
    created_at: str  # RFC3339 UTC
    schema_version: int
    zap_version: str
    warnings: List[dict] = field(default_factory=list)

    @property
    def plan_id(self) -> str:
        ids = sorted(a.id for a in self.artifacts)
        basis = self.app.identifier + "|" + "|".join(ids) + "|" + str(self.schema_version)
        return content_id(basis)

    def selected(self) -> List[Artifact]:
        return [a for a in self.artifacts if a.auto_selected]

    def summary(self) -> dict:
        sel = self.selected()
        return {
            "total_artifacts": len(self.artifacts),
            "auto_selected": len(sel),
            "total_bytes": sum(a.size_bytes for a in self.artifacts),
            "selected_bytes": sum(a.size_bytes for a in sel),
            "requires_root": any(a.requires_root for a in self.artifacts),
        }

    def to_dict(self, *, compact: bool = False, max_dropped: Optional[int] = None) -> dict:
        """``compact`` slims each artifact and hoists repeated notes into a
        ``notes`` legend keyed by artifact id; ``max_dropped`` caps the
        ``dropped_by_safety`` item list (the count is always reported)."""
        d = {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "zap_version": self.zap_version,
            "compact": compact,
            "app": self.app.to_dict(),
            "artifacts": [a.to_dict(compact=compact) for a in self.artifacts],
            "summary": self.summary(),
            "warnings": [_cap_dropped(w, max_dropped) for w in self.warnings],
        }
        if compact:
            legend: dict = {}
            for a in self.artifacts:
                for note in a.notes:
                    legend.setdefault(note, []).append(a.id)
            d["notes"] = legend
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(
            app=App.from_dict(d.get("app", {})),
            artifacts=[Artifact.from_dict(a) for a in d.get("artifacts", [])],
            created_at=d.get("created_at", ""),
            schema_version=int(d.get("schema_version", 1)),
            zap_version=d.get("zap_version", ""),
            warnings=list(d.get("warnings", [])),
        )


def _cap_dropped(warning: dict, max_items: Optional[int]) -> dict:
    if max_items is None or warning.get("code") != "dropped_by_safety":
        return warning
    items = warning.get("items") or []
    if len(items) <= max_items:
        return warning
    out = dict(warning)
    out["items"] = items[:max_items]
    out["items_truncated"] = len(items) - max_items
    out["message"] = out.get("message", "") + f" (showing {max_items}; write the plan with -o for the full list)"
    return out


# Status values for a per-artifact action result.
STATUS_REMOVED = "removed"
STATUS_DRY_RUN = "dry_run"
STATUS_SKIPPED = "skipped"
STATUS_DRIFTED = "drifted"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"  # refused by the safety guard


@dataclass
class ActionResult:
    """The outcome of acting on one artifact."""

    id: str
    path: Path
    status: str
    reversible: bool
    method: str = ""  # "trash" | "purge" | ""
    trash_path: Optional[str] = None
    size_bytes: int = 0
    reason: Optional[str] = None

    def to_dict(self, *, compact: bool = False) -> dict:
        d = {
            "id": self.id,
            "path": str(self.path),
            "status": self.status,
            "reversible": self.reversible,
            "method": self.method,
            "trash_path": self.trash_path,
            "size_bytes": int(self.size_bytes),
            "reason": self.reason,
        }
        if compact:
            d = {k: v for k, v in d.items() if v not in (None, "")}
        return d
