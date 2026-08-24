"""Tests for the safety guard — the module that must never be wrong."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests.util import TempTree
from zap.core.safety import SafetyGuard, build_allowed_roots
from zap.environment import HOME, SYS, Environment


def make_env(root: Path, system: str = "darwin") -> Environment:
    home = root / "home" / "user"
    home.mkdir(parents=True, exist_ok=True)
    (root / "System").mkdir(parents=True, exist_ok=True)
    (root / "Applications").mkdir(parents=True, exist_ok=True)
    return Environment(
        system=system,
        home=home,
        sysroot=root,
        state_home=home / ".state",
        uid=501,
        is_root=False,
        is_tty=False,
    )


ROOT_TEMPLATES = [
    (HOME, "Library/Caches"),
    (HOME, "Library/Application Support"),
    (HOME, "Library/Preferences"),
    (SYS, "Applications"),
    (SYS, "Library/LaunchDaemons"),
]


class SafetyGuardTests(unittest.TestCase):
    def _guard(self, root: Path, system: str = "darwin") -> SafetyGuard:
        env = make_env(root, system)
        return SafetyGuard(env, build_allowed_roots(env, ROOT_TEMPLATES))

    def test_normal_artifact_allowed(self):
        with TempTree() as root:
            guard = self._guard(root)
            target = root / "home/user/Library/Caches/com.acme.Widget"
            target.mkdir(parents=True)
            ok, reason = guard.check(target)
            self.assertTrue(ok, reason)

    def test_nested_vendor_folder_allowed(self):
        with TempTree() as root:
            guard = self._guard(root)
            target = root / "home/user/Library/Application Support/Google/Chrome"
            target.mkdir(parents=True)
            ok, reason = guard.check(target)
            self.assertTrue(ok, reason)

    def test_root_itself_rejected(self):
        with TempTree() as root:
            guard = self._guard(root)
            target = root / "home/user/Library/Caches"
            target.mkdir(parents=True)
            ok, reason = guard.check(target)
            self.assertFalse(ok)
            self.assertIn("root", reason)

    def test_outside_all_roots_rejected(self):
        with TempTree() as root:
            guard = self._guard(root)
            target = root / "home/user/Documents/important"
            target.mkdir(parents=True)
            ok, reason = guard.check(target)
            self.assertFalse(ok)

    def test_dotdot_escape_rejected(self):
        with TempTree() as root:
            guard = self._guard(root)
            target = root / "home/user/Library/Caches/../../Documents/secret"
            ok, reason = guard.check(target)
            self.assertFalse(ok)

    def test_filesystem_root_rejected(self):
        with TempTree() as root:
            guard = self._guard(root)
            ok, reason = guard.check(Path("/"))
            self.assertFalse(ok)

    def test_home_itself_rejected(self):
        with TempTree() as root:
            guard = self._guard(root)
            ok, reason = guard.check(root / "home/user")
            self.assertFalse(ok)

    def test_symlinked_ancestor_escaping_root_rejected(self):
        # The critical case: an intermediate component is a symlink that points
        # OUT of the allow-listed root, at ~/Documents. A purely lexical check
        # would pass; realpath-of-parent must catch it.
        with TempTree() as root:
            guard = self._guard(root)
            docs = root / "home/user/Documents"
            docs.mkdir(parents=True)
            (docs / "keepme").write_text("precious")
            appsup = root / "home/user/Library/Application Support"
            appsup.mkdir(parents=True)
            evil = appsup / "EvilApp"
            os.symlink(docs, evil)  # EvilApp -> ~/Documents
            # We try to remove a child *through* the symlinked ancestor.
            target = evil / "keepme"
            ok, reason = guard.check(target)
            self.assertFalse(ok, "symlinked ancestor escape must be rejected")
            self.assertIn("escape", reason)

    def test_leaf_symlink_pointing_outside_is_allowed(self):
        # Removing a leaf that is itself a symlink is fine — we unlink the link,
        # never the target. Its real parent stays inside the root.
        with TempTree() as root:
            guard = self._guard(root)
            docs = root / "home/user/Documents"
            docs.mkdir(parents=True)
            (docs / "keepme").write_text("precious")
            caches = root / "home/user/Library/Caches"
            caches.mkdir(parents=True)
            link = caches / "com.acme.Widget"
            os.symlink(docs / "keepme", link)
            ok, reason = guard.check(link)
            self.assertTrue(ok, reason)

    def test_applications_child_allowed_but_not_dir_itself(self):
        with TempTree() as root:
            guard = self._guard(root)
            (root / "Applications").mkdir(parents=True, exist_ok=True)
            ok_dir, _ = guard.check(root / "Applications")
            self.assertFalse(ok_dir)
            app = root / "Applications/Widget.app"
            app.mkdir()
            ok_app, reason = guard.check(app)
            self.assertTrue(ok_app, reason)

    def test_protected_apple_app(self):
        with TempTree() as root:
            env = make_env(root)
            guard = SafetyGuard(env, build_allowed_roots(env, ROOT_TEMPLATES))
            self.assertTrue(
                guard.is_protected_app(
                    root / "Applications/Safari.app", "com.apple.Safari", "Safari"
                )
            )
            self.assertTrue(
                guard.is_protected_app(
                    root / "System/Applications/Finder.app", None, "Finder"
                )
            )
            self.assertFalse(
                guard.is_protected_app(
                    root / "Applications/Widget.app", "com.acme.Widget", "Widget"
                )
            )

    def test_case_insensitive_containment_on_darwin(self):
        with TempTree() as root:
            guard = self._guard(root, system="darwin")
            target = root / "home/user/Library/caches/com.acme.Widget"
            target.mkdir(parents=True)
            ok, reason = guard.check(target)
            # On darwin (case-insensitive), 'caches' should match 'Caches' root.
            self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
