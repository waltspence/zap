"""Regression tests for the defects found in the 0.1.0 review.

Each test reproduces the exact reported behaviour (not a proxy), so a fix
cannot be reverted without one of these going red.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.util import TempTree, make_app_bundle
from zap import cli
from zap.core import catalog as C
from zap.core import history
from zap.core.engine import Engine
from zap.core.matching import AppKeys
from zap.core.models import Artifact, Fingerprint, Match, Plan, App
from zap.core.safety import SafetyGuard, build_allowed_roots
from zap.core.scanner import Scanner, ScanInput
from zap.environment import Environment


def _env(root: Path, system: str = "darwin") -> Environment:
    home = root / "home" / "user"
    home.mkdir(parents=True, exist_ok=True)
    return Environment(
        system=system,
        home=home,
        sysroot=root,
        state_home=home / ".state",
        uid=501,
        now=lambda: datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc),
    )


class PlanFileCannotWidenRoots(unittest.TestCase):
    def test_plan_cannot_widen_roots(self):
        with TempTree() as root:
            env = _env(root)
            victim = env.home / "Documents" / "Taxes"
            victim.mkdir(parents=True)
            (victim / "2025.pdf").write_text("x")
            (env.home / ".Trash").mkdir()
            evil = App(name="Evil", identifier="evil", path=env.home / "Documents" / "Evil.app")
            plan = Plan(
                app=evil,
                artifacts=[
                    Artifact(
                        path=victim, category="other",
                        match=Match("bundle_id", "x", "exact"), risk="normal",
                        auto_selected=True, fingerprint=Fingerprint.of(victim),
                    )
                ],
                created_at="", schema_version=1, zap_version="0",
            )
            pf = root / "plan.json"
            pf.write_text(json.dumps(plan.to_dict()))
            rc = cli.main(["--json", "uninstall", "--plan", str(pf), "--yes"], env=env)
            self.assertTrue(victim.exists(), "a crafted plan trashed a path outside the catalog roots")
            self.assertNotEqual(rc, 0)


class PlanCannotTouchOtherApps(unittest.TestCase):
    """A plan for app A must never remove app B's bundle, protected or not."""

    def _run(self, victim_name, victim_id):
        with TempTree() as root:
            env = _env(root)
            apps = root / "Applications"
            make_app_bundle(apps, "Innocent", "com.acme.Innocent")
            victim = make_app_bundle(apps, victim_name, victim_id)
            (env.home / ".Trash").mkdir()
            eng = Engine(env)
            innocent = eng.resolve("com.acme.Innocent").app
            plan = Plan(
                app=innocent,
                artifacts=[
                    Artifact(
                        path=victim, category="other",
                        match=Match("bundle_id", "x", "exact"), risk="normal",
                        auto_selected=True, fingerprint=Fingerprint.of(victim),
                    )
                ],
                created_at="", schema_version=1, zap_version="0",
            )
            pf = root / "plan.json"
            pf.write_text(json.dumps(plan.to_dict()))
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.main(["--json", "uninstall", "--plan", str(pf), "--yes"], env=env)
            self.assertTrue(victim.exists(), f"{victim_name} was removed via another app's plan")
            out = json.loads(buf.getvalue())
            # The foreign path is not attributed by a fresh scan, so the plan
            # entry is reported stale and nothing is acted on.
            self.assertEqual(out["data"]["results"], [])
            self.assertTrue(any(n["code"] == "plan_stale_ids" for n in out["data"]["selection_notes"]))

    def test_protected_apple_app(self):
        self._run("Calculator", "com.apple.calculator")

    def test_third_party_sibling(self):
        self._run("Other", "com.other.Thing")


class AmbiguousIdentifierPlanBindsByPath(unittest.TestCase):
    def test_same_app_in_two_roots(self):
        with TempTree() as root:
            env = _env(root)
            make_app_bundle(root / "Applications", "Widget", "com.acme.Widget")
            second = make_app_bundle(env.home / "Applications", "Widget", "com.acme.Widget")
            (env.home / ".Trash").mkdir()
            import contextlib
            import io
            pf = root / "plan.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["scan", str(second), "-o", str(pf)], env=env), 0)
                rc = cli.main(["--json", "uninstall", "--plan", str(pf), "--yes"], env=env)
            self.assertEqual(rc, 0)
            self.assertFalse(second.exists())
            self.assertTrue((root / "Applications" / "Widget.app").exists())


class VendorDirDedupe(unittest.TestCase):
    def test_vendor_dir_dedupes_case_insensitive(self):
        with TempTree() as root:
            env = _env(root)
            d = env.home / "Library" / "Application Support" / "Google" / "Chrome"
            d.mkdir(parents=True)
            (d / "f").write_text("12345")
            probe = env.home / "Library" / "Application Support" / "google"
            if not probe.is_dir():
                self.skipTest("filesystem is case-sensitive")
            guard = SafetyGuard(env, build_allowed_roots(env, C.roots_for(C.mac_locations(), "darwin")))
            keys = AppKeys(bundle_id="com.google.Chrome", team_id=None, names=set(), executables=set())
            out = Scanner(env, guard).scan(ScanInput(keys=keys), C.mac_locations())
            vendor = [a for a in out.artifacts if a.match.key == "vendor_folder"]
            self.assertEqual(len(vendor), 1, [str(a.path) for a in vendor])


class UndoRemovesTrashinfo(unittest.TestCase):
    def test_undo_removes_trashinfo(self):
        with TempTree() as root:
            env = _env(root, system="linux")
            apps = root / "usr" / "share" / "applications"
            apps.mkdir(parents=True)
            (apps / "org.acme.Widget.desktop").write_text("[Desktop Entry]\nName=Widget\nExec=widget\n")
            cfg = env.home / ".config" / "Widget"
            cfg.mkdir(parents=True)
            (cfg / "rc").write_text("x")
            eng = Engine(env)
            app = eng.resolve("org.acme.Widget").app
            plan = eng.scan(app, include_low=True)
            sel, _, _ = eng.select(plan)
            sel = [a for a in sel if a.path == cfg]
            self.assertEqual(len(sel), 1)
            report = eng.execute(plan, sel, dry_run=False)
            self.assertTrue(report.removed_any())
            info_dir = env.xdg_data_home() / "Trash" / "info"
            self.assertEqual(len(os.listdir(info_dir)), 1)
            history.undo_operation(env, report.operation_id)
            self.assertTrue(cfg.exists())
            self.assertEqual(os.listdir(info_dir), [], "orphan .trashinfo left behind after undo")


class LockImportErrorPropagates(unittest.TestCase):
    def test_lock_body_importerror_propagates(self):
        with TempTree() as root:
            env = _env(root)
            with self.assertRaises(ImportError):
                with history.exclusive_lock(env):
                    raise ImportError("from body")


class CrashLogNameWithSpaces(unittest.TestCase):
    def test_crash_log_name_with_spaces(self):
        with TempTree() as root:
            env = _env(root)
            crash = env.home / "Library" / "Logs" / "DiagnosticReports"
            crash.mkdir(parents=True)
            # Modern .ips reports use '-' as the separator; a hyphenated app
            # name is split at its own hyphen and never matches.
            (crash / "Widget-Pro-2026-01-01-120000.ips").write_text("x")
            guard = SafetyGuard(env, build_allowed_roots(env, C.roots_for(C.mac_locations(), "darwin")))
            keys = AppKeys(bundle_id="com.acme.WidgetPro", team_id=None,
                           names={"widget-pro"}, executables={"widget-pro"})
            out = Scanner(env, guard).scan(ScanInput(keys=keys), C.mac_locations())
            self.assertTrue(any(a.category == "crash_report" for a in out.artifacts))




if __name__ == "__main__":
    unittest.main()


class PermissionReasonTest(unittest.TestCase):
    """EPERM failures must tell the user what to actually do."""

    def _art(self, requires_root=False):
        from zap.core.engine import _perm_reason
        return _perm_reason

    def _env(self, uid=501, home="/Users/u"):
        class E:
            pass
        e = E()
        e.uid = uid
        e.home = home
        return e

    def _artifact(self, requires_root=False):
        class A:
            pass
        a = A()
        a.requires_root = requires_root
        return a

    def test_requires_root_says_sudo(self):
        from pathlib import Path
        from zap.core.engine import _perm_reason
        r = _perm_reason(self._env(), self._artifact(requires_root=True), Path("/nonexistent"))
        self.assertIn("sudo", r)

    def test_root_owned_app_store_hint(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        from zap.core.engine import _perm_reason
        with tempfile.NamedTemporaryFile() as f:
            st = os.lstat(f.name)
            fake = os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 0, 0,
                                   st.st_size, st.st_atime, st.st_mtime, st.st_ctime))
            with mock.patch("os.lstat", return_value=fake):
                r = _perm_reason(self._env(), self._artifact(), Path(f.name))
        self.assertIn("owned by root", r)
        self.assertIn("App Store", r)

    def test_tcc_hint_for_owned_library_path(self):
        import sys
        import unittest as _u
        from pathlib import Path
        from zap.core.engine import _perm_reason
        if sys.platform != "darwin":
            raise _u.SkipTest("darwin only")
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as td:
            env = self._env(uid=os.getuid(), home=td)
            lib = Path(td) / "Library" / "Containers" / "x"
            lib.mkdir(parents=True)
            r = _perm_reason(env, self._artifact(), lib)
        self.assertIn("Full Disk Access", r)

    def test_plain_fallback(self):
        import os
        import tempfile
        from pathlib import Path
        from zap.core.engine import _perm_reason
        with tempfile.NamedTemporaryFile() as f:
            r = _perm_reason(self._env(uid=os.getuid(), home="/elsewhere"), self._artifact(), Path(f.name))
        self.assertEqual(r, "permission denied")
