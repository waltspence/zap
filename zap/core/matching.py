"""Attribution logic: does a given file/dir belong to the target app?

The engine walks known locations and asks this module, for each candidate
name, "is this the app's, and how sure are we?" The answers drive confidence
(auto-selection itself is decided in one place: ``Engine._apply_selection_tiers``). Two rules here are load-bearing for
safety:

* **Dot-boundary prefix only** — ``com.google.Chrome`` claims
  ``com.google.Chrome.helper`` but never the sibling app
  ``com.google.ChromeCanary``.
* **Sibling guard** — a prefix/name match is dropped if it actually belongs to
  a *different installed app*, so uninstalling one app never sweeps another's
  files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Set, Tuple

from . import identifiers as I
from .models import (
    CONFIDENCE_DERIVED,
    CONFIDENCE_EXACT,
    CONFIDENCE_HEURISTIC,
    Match,
)


@dataclass
class AppKeys:
    """The normalized set of tokens an app can be matched by."""

    bundle_id: Optional[str]  # usable (validated, non-template) bundle id or None
    team_id: Optional[str]
    names: Set[str]  # case-folded name forms, reserved names already removed
    executables: Set[str]  # case-folded executable/process names
    # bundle ids of *other* installed apps, used by the sibling guard.
    other_bundle_ids: Set[str] = None  # type: ignore[assignment]
    # case-folded names of *other* installed apps (name-match sibling guard).
    other_names: Set[str] = None  # type: ignore[assignment]
    # (vendor, product) folder pairs of *other* installed apps, case-folded.
    other_vendor_dirs: Set[Tuple[str, str]] = None  # type: ignore[assignment]
    # Compare bundle ids exactly (case-sensitive filesystems) or case-folded.
    case_insensitive: bool = True

    def __post_init__(self) -> None:
        if self.other_bundle_ids is None:
            self.other_bundle_ids = set()
        if self.other_names is None:
            self.other_names = set()
        if self.other_vendor_dirs is None:
            self.other_vendor_dirs = set()

    def idkey(self, value: str) -> str:
        return I.normalize_casefold(value) if self.case_insensitive else I.normalize_nfc(value)


def build_name_set(*names: Optional[str]) -> Set[str]:
    """Case-folded, Unicode-normalized name forms, minus reserved names."""
    out: Set[str] = set()
    for name in names:
        if not name or not I.is_safe_name_component(name):
            continue
        if I.is_reserved_name(name):
            continue
        for form in I.name_forms(name):
            out.add(I.normalize_casefold(form))
    return out


def _belongs_to_other_app(candidate_id: str, keys: AppKeys) -> bool:
    """True if ``candidate_id`` is a better (dot-boundary) match for a *different*
    installed app than for ours — the sibling guard."""
    cand = keys.idkey(candidate_id)
    for other in keys.other_bundle_ids:
        o = keys.idkey(other)
        if keys.bundle_id and o == keys.idkey(keys.bundle_id):
            continue
        if cand == o or I.bundle_id_prefix_matches(other, candidate_id, case_insensitive=keys.case_insensitive):
            # It matches another app exactly or as that app's own subtree.
            return True
    return False


def match_bundle_child(name: str, keys: AppKeys) -> Optional[Match]:
    """Classify a child whose *name is expected to be a bundle id* (Caches,
    Containers scan, plug-in dirs by id, etc.)."""
    if not keys.bundle_id:
        return None
    bid = keys.bundle_id
    # Strip an optional hardened-runtime team-id prefix: "TEAMID.com.acme.App".
    stripped = name
    if keys.team_id and name.startswith(keys.team_id + "."):
        stripped = name[len(keys.team_id) + 1 :]
    # Strip common updater suffixes for the comparison but keep the real name.
    core = stripped
    for suffix in (".ShipIt",):
        if core.endswith(suffix):
            core = core[: -len(suffix)]

    if keys.idkey(core) == keys.idkey(bid):
        if stripped != name:  # had a team-id prefix
            if _belongs_to_other_app(core, keys):
                return None
            return Match("bundle_id_team", name, CONFIDENCE_DERIVED)
        if core != stripped:  # had a suffix like .ShipIt: the *full* name must
            # not be another app's id (com.acme.Widget.ShipIt could be one).
            if _belongs_to_other_app(name, keys):
                return None
            return Match("bundle_id", name, CONFIDENCE_EXACT)
        return Match("bundle_id", name, CONFIDENCE_EXACT)

    if I.bundle_id_prefix_matches(bid, core, case_insensitive=keys.case_insensitive):
        if _belongs_to_other_app(core, keys):
            return None
        return Match("bundle_id_prefix", name, CONFIDENCE_DERIVED)
    return None


def embedded_belongs_to_other(embedded_id: str, keys: AppKeys) -> bool:
    """Sibling guard for ids found *inside* the app bundle."""
    return _belongs_to_other_app(embedded_id, keys)


def match_name_child(name: str, keys: AppKeys) -> Optional[Match]:
    """Classify a child matched purely by *display/product name* (Application
    Support, Logs). Never auto-selected by the engine; always review-tier."""
    if I.is_reserved_name(name):
        return None
    folded = I.normalize_casefold(name)
    if folded in keys.names:
        # If the folder name is a bundle id that belongs to another app, skip.
        if I.is_valid_bundle_id(name) and _belongs_to_other_app(name, keys):
            return None
        # A display name shared with another installed app is nobody's.
        if folded in keys.other_names:
            return None
        return Match("name", name, CONFIDENCE_HEURISTIC)
    return None


