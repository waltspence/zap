"""Apply catalog locations to a target app to produce concrete artifacts.

The scanner is the bridge between the declarative :mod:`catalog` and the live
filesystem. For each location it runs the location's strategy, classifies every
candidate through :mod:`matching`, then annotates the survivor with size,
fingerprint and risk (auto-selection is the engine's decision). Every candidate is passed through the
:class:`~zap.core.safety.SafetyGuard`; rejects are not silently dropped but
recorded on a ``dropped`` channel so an agent can see near-misses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..environment import Environment
from . import catalog as C
from . import fsutil
from . import identifiers as I
from . import matching
from . import plist
from .models import (
    CAT_SYSTEM_EXTENSION,
    CONFIDENCE_DERIVED,
    CONFIDENCE_EXACT,
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_ORDER,
    Artifact,
    Fingerprint,
    Match,
    artifact_id_for,
)
from .safety import SafetyGuard


@dataclass
class EmbeddedSource:
    bundle_id: str
    shared: bool
    note: str = ""


@dataclass
class ScanInput:
    keys: matching.AppKeys
    embedded: List[EmbeddedSource] = field(default_factory=list)


@dataclass
class ScanOutput:
    artifacts: List[Artifact]
    dropped: List[dict]  # {path, reason} for guard rejects
    warnings: List[dict]


def _lexists(p: Path) -> bool:
    return os.path.lexists(p)


def _iter_children(container: Path) -> List[str]:
    try:
        return sorted(os.listdir(container))
    except OSError:
        return []


class Scanner:
    def __init__(self, env: Environment, guard: SafetyGuard):
        self.env = env
        self.guard = guard

    def scan(self, inp: ScanInput, locations: List[C.Location]) -> ScanOutput:
        found: Dict[str, Tuple[Artifact, str]] = {}  # id -> (artifact, confidence)
        dropped: List[dict] = []
        warnings: List[dict] = []
        self._seen_inodes: Dict[Tuple[int, int], str] = {}  # (dev, ino) -> artifact id

        for loc in locations:
            container = self.env.resolve(loc.base, *[s for s in loc.container.split("/") if s])
            if not container.exists():
                continue
            for path, match, shared in self._apply(loc, container, inp):
                self._consider(found, dropped, warnings, loc, path, match, shared)

        artifacts = [a for (a, _c) in found.values()]
        artifacts.sort(key=lambda a: str(a.path))
        return ScanOutput(artifacts=artifacts, dropped=dropped, warnings=warnings)

    # -- candidate generation ---------------------------------------------

    def _apply(self, loc: C.Location, container: Path, inp: ScanInput):
        strat = loc.strategy
        if strat == C.ID_PATH:
            yield from self._id_path(loc, container, inp)
        elif strat == C.ID_SCAN:
            yield from self._id_scan(loc, container, inp)
        elif strat == C.NAME_SCAN:
            yield from self._name_scan(loc, container, inp)
        elif strat == C.VENDOR_DIR:
            yield from self._vendor_dir(loc, container, inp)
        elif strat == C.BYHOST:
            yield from self._byhost(loc, container, inp)
        elif strat == C.GROUP_CONTAINER:
            yield from self._group_container(loc, container, inp)
        elif strat == C.RECEIPT:
            yield from self._receipt(loc, container, inp)
        elif strat == C.CRASH:
            yield from self._crash(loc, container, inp)
        elif strat == C.BUNDLE_SCAN:
            yield from self._bundle_scan(loc, container, inp)
        elif strat == C.ICLOUD:
            yield from self._icloud(loc, container, inp)
        elif strat == C.WARN_ONLY:
            yield from self._warn_only(loc, container, inp)

    def _sources(self, inp: ScanInput):
        """(bundle_id, key_label, confidence, shared) tuples for id-keyed scans."""
        out = []
        if inp.keys.bundle_id:
            out.append((inp.keys.bundle_id, "bundle_id", CONFIDENCE_EXACT, False))
        for emb in inp.embedded:
            out.append((emb.bundle_id, "embedded_bundle_id", CONFIDENCE_DERIVED, emb.shared))
        return out

    def _id_path(self, loc, container, inp):
        suffixes = loc.suffixes or ("",)
        for bid, key, conf, shared in self._sources(inp):
            if not I.is_valid_bundle_id(bid):
                continue
            for suf in suffixes:
                cand = container / (bid + suf)
                if _lexists(cand):
                    yield cand, Match(key, bid, conf), shared

    def _id_scan(self, loc, container, inp):
        suffixes = tuple(s for s in (loc.suffixes or ()) if s)
        for child in _iter_children(container):
            stem = child
            if suffixes:
                matched = next((s for s in suffixes if child.endswith(s)), None)
                if matched is None:
                    continue
                stem = child[: -len(matched)]
            m = matching.match_bundle_child(stem, inp.keys)
            shared = False
            if m is None:
                # try embedded ids
                for emb in inp.embedded:
                    em = self._embedded_bundle_match(stem, emb, inp.keys)
                    if em is not None:
                        m, shared = em, emb.shared
                        break
            if m is not None:
                yield container / child, m, shared

    def _embedded_bundle_match(self, child, emb: EmbeddedSource, keys) -> Optional[Match]:
        ek = matching.AppKeys(
            bundle_id=emb.bundle_id,
            team_id=keys.team_id,
            names=set(),
            executables=set(),
            other_bundle_ids=keys.other_bundle_ids,
        )
        m = matching.match_bundle_child(child, ek)
        if m is None:
            return None
        return Match("embedded_bundle_id", child, CONFIDENCE_DERIVED)

    def _name_scan(self, loc, container, inp):
        suffixes = loc.suffixes
        for child in _iter_children(container):
            stem = child
            if suffixes:
                matched_suffix = next((s for s in suffixes if child.endswith(s)), None)
                if matched_suffix is None:
                    continue
                stem = child[: -len(matched_suffix)] if matched_suffix else child
            m = matching.match_name_child(stem, inp.keys)
            if m is not None:
                yield container / child, m, False

    def _vendor_dir(self, loc, container, inp):
        if not inp.keys.bundle_id:
            return
        vp = I.vendor_product_from_bundle_id(inp.keys.bundle_id)
        if vp is None:
            return
        vendor, product = vp
        for v in {vendor, vendor.lower(), vendor.capitalize()}:
            cand = container / v / product
            if cand.is_dir():
                yield cand, Match("vendor_folder", f"{v}/{product}", CONFIDENCE_DERIVED), False

    def _byhost(self, loc, container, inp):
        bid = inp.keys.bundle_id
        if not bid:
            return
        prefix = bid + "."
        for child in _iter_children(container):
            if child.startswith(prefix) and child.endswith(".plist"):
                yield container / child, Match("byhost", bid, CONFIDENCE_EXACT), False

    def _group_container(self, loc, container, inp):
        bid = inp.keys.bundle_id
        team = inp.keys.team_id
        for child in _iter_children(container):
            hit = False
            if bid:
                forms = {f"group.{bid}", bid}
                if I.normalize_casefold(child) in {I.normalize_casefold(f) for f in forms}:
                    hit = True
            if not hit and team and I.is_valid_team_id(team) and child.startswith(team + "."):
                hit = True
            if hit:
                yield container / child, Match("group_container", child, CONFIDENCE_DERIVED), True

    def _receipt(self, loc, container, inp):
        bid = inp.keys.bundle_id
        if not bid:
            return
        for suf in loc.suffixes or ("",):
            cand = container / (bid + suf)
            if _lexists(cand):
                yield cand, Match("receipt", bid, CONFIDENCE_EXACT), False

    def _crash(self, loc, container, inp):
        procnames = set(inp.keys.names) | set(inp.keys.executables)
        if not procnames:
            return
        for child in _iter_children(container):
            folded = I.normalize_casefold(child)
            # Crash reports are "<Process>_<date>.crash" or "<Process>-<date>.ips".
            # Match the longest known process name that is followed by a
            # separator, so hyphenated names are not split at their own hyphen.
            hit = any(
                folded.startswith(name) and len(folded) > len(name) and folded[len(name)] in "_-"
                for name in procnames
                if name
            )
            if hit:
                yield container / child, Match("process_name", child, CONFIDENCE_HEURISTIC), False

    def _bundle_scan(self, loc, container, inp):
        for child in _iter_children(container):
            cand = container / child
            if not cand.is_dir():
                continue
            cid = plist.read_bundle_id(cand)
            if cid:
                m = matching.match_bundle_child(cid, inp.keys)
                if m is not None:
                    yield cand, Match("plugin_bundle_id", cid, m.confidence), False
                    continue
                for emb in inp.embedded:
                    if I.normalize_casefold(cid) == I.normalize_casefold(emb.bundle_id):
                        yield cand, Match("plugin_bundle_id", cid, CONFIDENCE_DERIVED), emb.shared
                        break
            else:
                # fall back to the bundle's filename stem vs the app's names
                stem = os.path.splitext(child)[0]
                m = matching.match_name_child(stem, inp.keys)
                if m is not None:
                    yield cand, m, False

    def _icloud(self, loc, container, inp):
        bid = inp.keys.bundle_id
        if not bid:
            return
        for child in _iter_children(container):
            decoded = child.replace("~", ".")
            cand_id = decoded[len("iCloud."):] if decoded.startswith("iCloud.") else decoded
            if I.normalize_casefold(cand_id) == I.normalize_casefold(bid) or I.bundle_id_prefix_matches(bid, cand_id):
                yield container / child, Match("icloud_container", child, CONFIDENCE_DERIVED), True

    def _warn_only(self, loc, container, inp):
        for child in _iter_children(container):
            cand = container / child
            cid = plist.read_bundle_id(cand) if cand.is_dir() else None
            hit = False
            if cid and matching.match_bundle_child(cid, inp.keys) is not None:
                hit = True
            elif matching.match_name_child(os.path.splitext(child)[0], inp.keys) is not None:
                hit = True
            if hit:
                yield cand, Match("system_extension", child, CONFIDENCE_HEURISTIC), False

    # -- assembly ----------------------------------------------------------

    def _consider(self, found, dropped, warnings, loc: C.Location, path: Path, match: Match, shared: bool):
        ok, reason = self.guard.check(path)
        if not ok:
            dropped.append({"path": str(path), "reason": reason, "category": loc.category})
            return

        art_id = artifact_id_for(path)
        # The same object can be reached by differently-cased paths on a
        # case-insensitive filesystem; collapse those onto the first id seen.
        fp = Fingerprint.of(path)
        if fp.exists and fp.ino != 0:  # some FUSE/network mounts report 0 for everything
            inode = (fp.dev, fp.ino)
            prior = self._seen_inodes.get(inode)
            if prior is not None and prior != art_id:
                art_id = prior
                path = found[prior][0].path
            else:
                self._seen_inodes[inode] = art_id
        existing = found.get(art_id)
        if existing is not None:
            _, prev_conf = existing
            if CONFIDENCE_ORDER.get(match.confidence, 0) <= CONFIDENCE_ORDER.get(prev_conf, 0):
                return  # keep the stronger classification

        size, count = fsutil.tree_size(path)
        requires_root = loc.requires_root
        shared_id = I.is_valid_bundle_id(match.value) and I.is_shared_component_id(match.value)
        is_shared = bool(shared or loc.shared or shared_id)
        never_remove = loc.category == CAT_SYSTEM_EXTENSION

        notes = []
        if loc.note:
            notes.append(loc.note)
        if is_shared:
            notes.append("Shared with other apps or extensions — review before removing.")
        if never_remove:
            notes.append("Detected only; zap will not remove this item.")

        art = Artifact(
            path=path,
            category=loc.category,
            match=match,
            risk=loc.risk_level(),
            size_bytes=size,
            item_count=count,
            shared=is_shared,
            reversible=not never_remove,
            requires_root=requires_root,
            auto_selected=False,  # decided later by Engine._apply_selection_tiers
            fingerprint=fp,
            notes=notes,
        )
        found[art_id] = (art, match.confidence)
