"""Tiny plist helpers built on the stdlib ``plistlib`` (handles binary plists)."""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Optional


def read_plist(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            data = plistlib.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}


def read_bundle_info(bundle_dir: Path) -> dict:
    return read_plist(bundle_dir / "Contents" / "Info.plist")


def read_bundle_id(bundle_dir: Path) -> Optional[str]:
    info = read_bundle_info(bundle_dir)
    bid = info.get("CFBundleIdentifier")
    return bid if isinstance(bid, str) and bid else None
