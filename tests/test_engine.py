"""End-to-end engine tests over a simulated macOS install tree."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.util import TempTree, make_app_bundle
from zap.core.engine import Engine
from zap.core.models import (
    CAT_APP_BUNDLE,
    STATUS_DRY_RUN,
    STATUS_REMOVED,
)
from zap.environment import Environment


def darwin_env(root: Path) -> Environment:
    home = root / "home" / "user"
    home.mkdir(parents=True, exist_ok=True)
    return Environment(
        system="darwin",
        home=home,
        sysroot=root,
        state_home=home / ".state",
        uid=501,
        is_root=False,
        is_tty=False,
        now=lambda: datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc),
    )


def build_widget_tree(root: Path) -> None:
    home = root / "home" / "user"
    lib = home / "Library"
    apps = root / "Applications"

    # Main app bundle with an embedded login-item helper and an embedded
    # shared updater (Keystone).
    app = make_app_bundle(apps, "Widget", "com.acme.Widget", payload_bytes=2048)
    login = app / "Contents" / "Library" / "LoginItems" / "Helper.app" / "Contents"
    login.mkdir(parents=True)
    import plistlib

    with open(login / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": "com.acme.Widget.Helper"}, fh)
    keystone = app / "Contents" / "Library" / "LoginItems" / "Keystone.app" / "Contents"
    keystone.mkdir(parents=True)
    with open(keystone / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": "com.google.Keystone.Agent"}, fh)

    # A sibling app that shares a prefix — MUST NOT be swept.
    make_app_bundle(apps, "WidgetPro", "com.acme.WidgetPro", payload_bytes=1024)

    def mk(p: Path, data: bytes = b"x"):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    # Exact-bundle-id artifacts (auto-selected).
    mk(lib / "Caches" / "com.acme.Widget" / "blob", b"c" * 100)
    mk(lib / "Preferences" / "com.acme.Widget.plist", b"p" * 20)
    mk(lib / "HTTPStorages" / "com.acme.Widget" / "s")
    mk(lib / "Saved Application State" / "com.acme.Widget.savedState" / "data")
    mk(lib / "LaunchAgents" / "com.acme.Widget.plist", b"la")

    # Embedded-helper cache (derived) and shared Keystone cache (shared).
    mk(lib / "Caches" / "com.acme.Widget.Helper" / "h")
    mk(lib / "Caches" / "com.google.Keystone.Agent" / "k")

    # Name-only application support (heuristic).
    mk(lib / "Application Support" / "Widget" / "state.db", b"s" * 40)

    # Sibling's cache — must survive an uninstall of Widget.
    mk(lib / "Caches" / "com.acme.WidgetPro" / "keep", b"keep")

    # An unrelated app's data — must never be touched.
    mk(lib / "Caches" / "com.other.Thing" / "keep", b"keep")


class EngineScanTests(unittest.TestCase):
    def test_discovery_and_resolution(self):
        with TempTree() as root:
            build_widget_tree(root)
            eng = Engine(darwin_env(root))
            apps = eng.list_apps()
            names = {a.name for a in apps}
            self.assertIn("Widget", names)
            self.assertIn("WidgetPro", names)
            r = eng.resolve("Widget")
            self.assertIsNotNone(r.app)
            self.assertEqual(r.app.bundle_id, "com.acme.Widget")
            self.assertIn("com.acme.Widget.Helper", r.app.embedded_bundle_ids)
            self.assertIn("com.google.Keystone.Agent", r.app.embedded_bundle_ids)

    def test_scan_attribution(self):
        with TempTree() as root:
            build_widget_tree(root)
            eng = Engine(darwin_env(root))
            app = eng.resolve("Widget").app
            plan = eng.scan(app)
            paths = {str(a.path): a for a in plan.artifacts}

            def has(suffix):
                return any(p.endswith(suffix) for p in paths)

            # exact-id artifacts present and auto-selected
            cache = paths[str(root / "home/user/Library/Caches/com.acme.Widget")]
            self.assertTrue(cache.auto_selected)
            self.assertEqual(cache.match.confidence, "exact")

            prefs = paths[str(root / "home/user/Library/Preferences/com.acme.Widget.plist")]
            self.assertTrue(prefs.auto_selected)

            # app bundle present and auto-selected
            bundle = paths[str(root / "Applications/Widget.app")]
            self.assertEqual(bundle.category, CAT_APP_BUNDLE)
            self.assertTrue(bundle.auto_selected)

            # sibling and unrelated app data absent
            self.assertNotIn(str(root / "home/user/Library/Caches/com.acme.WidgetPro"), paths)
            self.assertNotIn(str(root / "home/user/Library/Caches/com.other.Thing"), paths)

            # embedded helper cache present but NOT auto (derived)
            helper = paths.get(str(root / "home/user/Library/Caches/com.acme.Widget.Helper"))
            self.assertIsNotNone(helper)
            self.assertFalse(helper.auto_selected)

            # shared Keystone cache present, shared, not auto
            ks = paths.get(str(root / "home/user/Library/Caches/com.google.Keystone.Agent"))
            self.assertIsNotNone(ks)
            self.assertTrue(ks.shared)
            self.assertFalse(ks.auto_selected)

            # name-only app support present but heuristic / not auto
            appsup = paths.get(str(root / "home/user/Library/Application Support/Widget"))
            self.assertIsNotNone(appsup)
            self.assertFalse(appsup.auto_selected)
            self.assertEqual(appsup.match.confidence, "heuristic")

    def test_dry_run_then_trash_then_undo(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            eng = Engine(env)
            app = eng.resolve("Widget").app
            plan = eng.scan(app)
            selected, unknown, _notes = eng.select(plan)
            self.assertEqual(unknown, [])

            # dry-run touches nothing
            dry = eng.execute(plan, selected, method="trash", dry_run=True)
            self.assertTrue(all(r.status == STATUS_DRY_RUN for r in dry.results))
            self.assertTrue((root / "home/user/Library/Caches/com.acme.Widget").exists())

            # real trash
            rep = eng.execute(plan, selected, method="trash", dry_run=False)
            removed = [r for r in rep.results if r.status == STATUS_REMOVED]
            self.assertTrue(removed)
            self.assertIsNotNone(rep.operation_id)
            # auto-selected exact artifacts are gone
            self.assertFalse((root / "home/user/Library/Caches/com.acme.Widget").exists())
            self.assertFalse((root / "Applications/Widget.app").exists())
            # sibling + unrelated survive
            self.assertTrue((root / "home/user/Library/Caches/com.acme.WidgetPro").exists())
            self.assertTrue((root / "home/user/Library/Caches/com.other.Thing").exists())
            # shared Keystone survives (not auto)
            self.assertTrue((root / "home/user/Library/Caches/com.google.Keystone.Agent").exists())

            # undo restores everything
            from zap.core.history import undo_operation

            results = undo_operation(env, rep.operation_id)
            self.assertTrue(all(r["status"] == "restored" for r in results), results)
            self.assertTrue((root / "home/user/Library/Caches/com.acme.Widget").exists())
            self.assertTrue((root / "Applications/Widget.app").exists())

    def test_purge_refuses_shared(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            eng = Engine(env)
            app = eng.resolve("Widget").app
            plan = eng.scan(app, aggressive=True)
            ks_id = None
            for a in plan.artifacts:
                if a.path.name == "com.google.Keystone.Agent":
                    ks_id = a.id
            self.assertIsNotNone(ks_id)
            selected, _u, notes = eng.select(plan, only={ks_id}, method="purge")
            # shared component is refused for purge
            self.assertEqual(selected, [])
            self.assertTrue(any(n["code"] == "purge_refused_shared_or_system" for n in notes))


if __name__ == "__main__":
    unittest.main()
