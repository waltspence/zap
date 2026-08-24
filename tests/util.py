"""Test helpers for building throwaway filesystem trees.

These helpers are deliberately generic (they materialize whatever paths a test
asks for) so they stay valid even as the artifact-location catalog evolves.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Optional, Union

# A tree spec maps a relative path -> its content.
#   - "dir/" (trailing slash) or the DIR sentinel -> empty directory
#   - str / bytes -> a file with that content
#   - dict -> a .plist file (written with plistlib) when the path ends in .plist
TreeSpec = Dict[str, Union[str, bytes, dict, "Dir"]]


class Dir:
    """Sentinel marking a spec entry as an (empty) directory."""


DIR = Dir()


def make_tree(root: Path, spec: TreeSpec) -> None:
    """Materialize *spec* under *root*."""
    for rel, content in spec.items():
        target = root / rel
        if isinstance(content, Dir) or rel.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            with open(target, "wb") as fh:
                plistlib.dump(content, fh)
        elif isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)


def make_app_bundle(
    apps_dir: Path,
    name: str,
    bundle_id: str,
    *,
    extra_info: Optional[dict] = None,
    payload_bytes: int = 0,
) -> Path:
    """Create a minimal but realistic macOS .app bundle and return its path."""
    app = apps_dir / f"{name}.app"
    contents = app / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    info = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleName": name,
        "CFBundleExecutable": name,
    }
    if extra_info:
        info.update(extra_info)
    with open(contents / "Info.plist", "wb") as fh:
        plistlib.dump(info, fh)
    exe = macos / name
    exe.write_bytes(b"\x00" * max(1, payload_bytes))
    os.chmod(exe, 0o755)
    return app


class TempTree:
    """Context manager yielding a fresh temp directory that is cleaned up."""

    def __init__(self, spec: Optional[TreeSpec] = None):
        self._spec = spec or {}
        self.root: Path = Path()

    def __enter__(self) -> Path:
        self.root = Path(tempfile.mkdtemp(prefix="zap-test-"))
        make_tree(self.root, self._spec)
        return self.root

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
