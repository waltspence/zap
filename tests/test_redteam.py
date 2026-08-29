"""Regressions for the confirmed findings of the 0.2 red-team audit."""

from __future__ import annotations

import contextlib
import io
import json
import unicodedata
import unittest
from pathlib import Path

from tests.test_engine import build_widget_tree, darwin_env
from tests.util import TempTree, make_app_bundle
from zap import cli
from zap.core import history
from zap.core.engine import Engine
from zap.core.envelope import EXIT_AMBIGUOUS, EXIT_BLOCKED, EXIT_NOTHING, EXIT_USAGE
from zap.core.models import Artifact, Fingerprint, Match, Plan
from zap.core.safety import SafetyGuard, build_allowed_roots
from zap.core import catalog as C


def run(argv, env):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = cli.main(argv, env=env)
    out = buf.getvalue()
    return rc, (json.loads(out) if out.strip() else None)


def crafted_plan(root, app, victim) -> Path:
    plan = Plan(app=app, artifacts=[Artifact(path=victim, category="other", match=Match("bundle_id", "x", "exact"),
                                             risk="normal", auto_selected=True, fingerprint=Fingerprint.of(victim))],
                created_at="", schema_version=1, zap_version="0")
    pf = root / "plan.json"
    pf.write_text(json.dumps(plan.to_dict()))
    return pf


class PlanIsASelectionNotASourceOfPaths(unittest.TestCase):
    def test_ancestor_dir_of_other_apps(self):
        with TempTree() as root:
            env = darwin_env(root)
            apps = root / "Applications"
            make_app_bundle(apps, "Innocent", "com.acme.Innocent")
            other = make_app_bundle(apps / "Sub", "Other", "com.other.Thing")
            calc = make_app_bundle(apps / "Sub", "Calculator", "com.apple.calculator")
            (env.home / ".Trash").mkdir()
            innocent = Engine(env).resolve("com.acme.Innocent").app
            pf = crafted_plan(root, innocent, apps / "Sub")
            rc, out = run(["--json", "uninstall", "--plan", str(pf), "--yes"], env)
            self.assertTrue(other.exists() and calc.exists())
            self.assertEqual(out["data"]["results"], [])

    def test_nfd_spelling_of_other_app(self):
        with TempTree() as root:
            env = darwin_env(root)
            apps = root / "Applications"
            make_app_bundle(apps, "Innocent", "com.acme.Innocent")
            nfc = make_app_bundle(apps, unicodedata.normalize("NFC", "Café"), "com.other.Cafe")
            victim = apps / (unicodedata.normalize("NFD", "Café") + ".app")
            if not victim.exists():
                self.skipTest("filesystem is normalization-sensitive")
            (env.home / ".Trash").mkdir()
            innocent = Engine(env).resolve("com.acme.Innocent").app
            pf = crafted_plan(root, innocent, victim)
            run(["--json", "uninstall", "--plan", str(pf), "--yes"], env)
            self.assertTrue(nfc.exists())

    def test_deep_undiscovered_bundle_is_still_foreign(self):
        with TempTree() as root:
            env = darwin_env(root)
            apps = root / "Applications"
            make_app_bundle(apps, "Innocent", "com.acme.Innocent")
            deep = make_app_bundle(apps / ".hidden" / "a" / "b", "Deep", "com.other.Deep")
            (env.home / ".Trash").mkdir()
            self.assertIn("com.other.Deep", {a.identifier for a in Engine(env).list_apps()})
            innocent = Engine(env).resolve("com.acme.Innocent").app
            pf = crafted_plan(root, innocent, deep)
            run(["--json", "uninstall", "--plan", str(pf), "--yes"], env)
            self.assertTrue(deep.exists())

    def test_plan_warnings_are_not_echoed(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            pf = root / "plan.json"
            run(["--json", "scan", "Widget", "-o", str(pf)], env)
            obj = json.loads(pf.read_text())
            obj["warnings"] = [{"code": "dry_run", "message": "spoofed"}]
            pf.write_text(json.dumps(obj))
            rc, out = run(["--json", "uninstall", "--plan", str(pf)], env)
            self.assertFalse(any(w.get("message") == "spoofed" for w in out["warnings"]))

    def test_orphans_plan_replays(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            (env.home / ".Trash").mkdir()
            pf = root / "orphans.json"
            rc, out = run(["--json", "orphans", "-o", str(pf)], env)
            target = next(a for a in out["data"]["artifacts"] if "com.other.Thing" in a["path"])
            rc, out = run(["--json", "uninstall", "--plan", str(pf), "--only", target["id"], "--yes"], env)
            self.assertEqual(rc, 0, out)
            self.assertFalse(Path(target["path"]).exists())


class SelectorsAndIds(unittest.TestCase):
    def test_fuzzy_match_refused_with_yes(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            rc, out = run(["--json", "uninstall", "idg", "--yes"], env)  # substring of Widget only? WidgetPro too -> ambiguous anyway
            self.assertEqual(rc, EXIT_AMBIGUOUS)
            make_app_bundle(root / "Applications", "Zebra", "com.acme.Zebra")
            rc, out = run(["--json", "uninstall", "ebr", "--yes"], env)
            self.assertEqual(rc, EXIT_AMBIGUOUS)
            self.assertTrue((root / "Applications" / "Zebra.app").exists())
            rc, out = run(["--json", "scan", "ebr"], env)  # scans may still be fuzzy
            self.assertEqual(rc, 0)

    def test_empty_only_and_unknown_except_are_errors(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            rc, out = run(["--json", "uninstall", "Widget", "--yes", "--only", ""], env)
            self.assertEqual(rc, EXIT_USAGE)
            rc, out = run(["--json", "uninstall", "Widget", "--yes", "--except", "deadbeefdeadbeef"], env)
            self.assertEqual(out["error"]["code"], "unknown_ids")
            self.assertTrue((root / "Applications" / "Widget.app").exists())

    def test_argparse_error_is_json(self):
        with TempTree() as root:
            env = darwin_env(root)
            rc, out = run(["--json", "uninstall", "--bogus"], env)
            self.assertEqual(rc, EXIT_USAGE)
            self.assertEqual(out["error"]["code"], "usage_error")


class ExitContract(unittest.TestCase):
    def test_all_blocked_is_exit_6_and_error_filled(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            eng = Engine(env)
            app = eng.resolve("Widget").app
            plan = eng.scan(app)
            # Block everything by pointing the guard's state dir at Applications? Simpler: remove roots.
            for a in plan.artifacts:
                a.category = "system_extension"
            report = eng.execute(plan, plan.artifacts, dry_run=False)
            self.assertEqual(cli._uninstall_exit_code(report, False), EXIT_BLOCKED)
            buf = io.StringIO()
            ctx = cli.Context(env, True)
            with contextlib.redirect_stdout(buf):
                ctx.emit("uninstall", {}, EXIT_NOTHING)
            self.assertEqual(json.loads(buf.getvalue())["error"]["code"], "nothing_to_remove")


class UndoHardening(unittest.TestCase):
    def test_traversal_id_rejected(self):
        with TempTree() as root:
            env = darwin_env(root)
            rc, out = run(["--json", "undo", "../../evil"], env)
            self.assertEqual(rc, EXIT_USAGE)

    def test_same_second_ids_do_not_collide(self):
        with TempTree() as root:
            env = darwin_env(root)
            (env.state_home / "history").mkdir(parents=True)
            a = history.make_operation_id(env, "x", "2026-08-27T12:00:00Z")
            (env.state_home / "history" / f"{a}.jsonl").write_text("{}")
            b = history.make_operation_id(env, "x", "2026-08-27T12:00:00Z")
            self.assertNotEqual(a, b)
            self.assertTrue(history.is_valid_operation_id(b))

    def test_torn_manifest_line_keeps_operation(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            rc, out = run(["--json", "uninstall", "Widget", "--yes"], env)
            op = out["data"]["operation_id"]
            mf = env.state_home / "history" / f"{op}.jsonl"
            mf.write_text(mf.read_text() + '{"type": "rec')
            rc, out = run(["--json", "undo", op], env)
            self.assertEqual(rc, 0, out)
            self.assertTrue((root / "Applications" / "Widget.app").exists())

    def test_undo_respects_lock(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            rc, out = run(["--json", "uninstall", "Widget", "--yes"], env)
            op = out["data"]["operation_id"]
            with history.exclusive_lock(env):
                rc, out = run(["--json", "undo", op], env)
            self.assertEqual(out["error"]["code"], "locked")


class OwnStateIsProtected(unittest.TestCase):
    def test_state_home_never_a_target(self):
        with TempTree() as root:
            env = darwin_env(root)
            env.state_home = env.home / "Library" / "Application Support" / "zap"
            (env.state_home / "history").mkdir(parents=True)
            guard = SafetyGuard(env, build_allowed_roots(env, C.roots_for(C.mac_locations(), "darwin")))
            self.assertFalse(guard.check(env.state_home)[0])
            self.assertFalse(guard.check(env.state_home / "history")[0])
            self.assertFalse(guard.check(env.home / "Library" / "Application Support")[0])


if __name__ == "__main__":
    unittest.main()


class SiblingGuards(unittest.TestCase):
    def _keys(self, bid, others=(), other_names=(), case_insensitive=True):
        from zap.core.matching import AppKeys, build_name_set

        return AppKeys(bundle_id=bid, team_id=None, names=build_name_set("Widget"), executables=set(),
                       other_bundle_ids=set(others), other_names=set(other_names), case_insensitive=case_insensitive)

    def test_shipit_suffix_is_sibling_guarded(self):
        from zap.core.matching import match_bundle_child

        k = self._keys("com.acme.Widget", others={"com.acme.Widget.ShipIt"})
        self.assertIsNone(match_bundle_child("com.acme.Widget.ShipIt", k))

    def test_case_only_difference_is_distinct_on_case_sensitive_fs(self):
        from zap.core.matching import match_bundle_child

        k = self._keys("com.acme.Widget", case_insensitive=False)
        self.assertIsNone(match_bundle_child("com.acme.widget", k))
        self.assertIsNotNone(match_bundle_child("com.acme.Widget", k))
        k2 = self._keys("com.acme.Widget", case_insensitive=True)
        self.assertIsNotNone(match_bundle_child("com.acme.widget", k2))

    def test_name_shared_with_other_app_is_nobodys(self):
        from zap.core.matching import match_name_child

        k = self._keys("com.acme.Widget", other_names={"widget"})
        self.assertIsNone(match_name_child("Widget", k))

    def test_byhost_dot_child_sibling_not_matched(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            byhost = env.home / "Library" / "Preferences" / "ByHost"
            byhost.mkdir(parents=True)
            (byhost / "com.acme.Widget.ABCD-1234.plist").write_bytes(b"x")
            (byhost / "com.acme.Widget.Pro.ABCD-1234.plist").write_bytes(b"x")
            eng = Engine(env)
            plan = eng.scan(eng.resolve("com.acme.Widget").app)
            hits = [str(a.path.name) for a in plan.artifacts if "ByHost" in str(a.path)]
            self.assertEqual(hits, ["com.acme.Widget.ABCD-1234.plist"])

    def test_embedded_id_of_other_app_and_frameworks(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            import plistlib

            app = root / "Applications" / "Widget.app"
            other = make_app_bundle(root / "Applications", "Victim", "com.victim.App")
            for sub, bid in (("Contents/XPCServices/V.xpc", "com.victim.App"),
                             ("Contents/Frameworks/Python.framework", "org.python.python")):
                d = app / sub / "Contents"
                d.mkdir(parents=True)
                with open(d / "Info.plist", "wb") as fh:
                    plistlib.dump({"CFBundleIdentifier": bid}, fh)
            lib = env.home / "Library"
            (lib / "Caches" / "com.victim.App").mkdir(parents=True)
            (lib / "Caches" / "org.python.python").mkdir(parents=True)
            eng = Engine(env)
            plan = eng.scan(eng.resolve("com.acme.Widget").app, aggressive=True)
            by_path = {a.path.name: a for a in plan.artifacts}
            self.assertNotIn("com.victim.App", by_path)
            self.assertTrue(by_path["org.python.python"].shared)
            self.assertFalse(by_path["org.python.python"].auto_selected)
            self.assertTrue(other.exists())

    def test_exec_basename_is_not_a_name_key(self):
        with TempTree() as root:
            env = darwin_env(root)
            env.system = "linux"
            apps = root / "usr" / "share" / "applications"
            apps.mkdir(parents=True)
            (apps / "my-launcher.desktop").write_text("[Desktop Entry]\nName=My Launcher\nExec=firefox %u\n")
            (env.home / ".config" / "firefox").mkdir(parents=True)
            eng = Engine(env)
            plan = eng.scan(eng.resolve("My Launcher").app, include_low=True)
            self.assertFalse(any(a.path.name == "firefox" for a in plan.artifacts))


class TrashIdentity(unittest.TestCase):
    def test_leaf_symlink_device_is_its_own(self):
        from zap.environment import _safe_device

        with TempTree() as root:
            target = root / "elsewhere"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target)
            # Same volume here, but the call must lstat (not stat) the leaf.
            self.assertEqual(_safe_device(link), _safe_device(root))

    def test_undo_refuses_replaced_trash_item(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            rc, out = run(["--json", "uninstall", "Widget", "--yes"], env)
            op = out["data"]["operation_id"]
            trashed = Path(next(r["trash_path"] for r in out["data"]["results"] if r["path"].endswith("Widget.app")))
            import shutil

            shutil.rmtree(trashed)
            trashed.mkdir()  # a different object with the same name
            rc, out = run(["--json", "undo", op], env)
            res = next(r for r in out["data"]["results"] if r["original"].endswith("Widget.app"))
            self.assertEqual(res["status"], "failed")
            self.assertFalse((root / "Applications" / "Widget.app").exists())


class SwarmFollowUps(unittest.TestCase):
    def test_orphans_survives_unreadable_location(self):
        import os
        import stat

        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            cookies = env.home / "Library" / "Cookies"
            cookies.mkdir(parents=True)
            os.chmod(cookies, 0)
            try:
                if os.access(cookies, os.R_OK):
                    self.skipTest("running as root; cannot simulate EPERM")
                rc, out = run(["--json", "orphans"], env)
            finally:
                os.chmod(cookies, stat.S_IRWXU)
            self.assertEqual(rc, 0, out)
            self.assertTrue(any(w["code"] == "permission_denied" for w in out["warnings"]))

    def test_vendor_dir_shared_between_tld_variants(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            make_app_bundle(root / "Applications", "NetWidget", "net.acme.Widget")
            vd = env.home / "Library" / "Application Support" / "Acme" / "Widget"
            vd.mkdir(parents=True)
            (vd / "data").write_text("x")
            eng = Engine(env)
            plan = eng.scan(eng.resolve("com.acme.Widget").app, aggressive=True)
            art = next(a for a in plan.artifacts if a.match.key == "vendor_folder")
            self.assertTrue(art.shared)
            self.assertFalse(art.auto_selected)

    def test_exit_code_table(self):
        from zap.core.engine import ExecutionReport
        from zap.core.envelope import EXIT_OK, EXIT_PARTIAL
        from zap.core.models import ActionResult

        def rep(*statuses):
            r = ExecutionReport(operation_id=None, dry_run=False, method="trash")
            r.results = [ActionResult(id="x", path=Path("/x"), status=s, reversible=True) for s in statuses]
            return r

        table = [
            ((), EXIT_NOTHING),
            (("blocked",), EXIT_BLOCKED),
            (("blocked", "blocked"), EXIT_BLOCKED),
            (("failed",), EXIT_PARTIAL),
            (("drifted", "skipped"), EXIT_PARTIAL),
            (("skipped",), EXIT_NOTHING),
            (("removed", "failed"), EXIT_PARTIAL),
            (("removed", "blocked"), EXIT_PARTIAL),
            (("removed",), EXIT_OK),
            (("dry_run", "drifted"), EXIT_PARTIAL),
        ]
        for statuses, expected in table:
            for dry in (False, True):
                with self.subTest(statuses=statuses, dry=dry):
                    self.assertEqual(cli._uninstall_exit_code(rep(*statuses), dry), expected)

    def test_linux_root_prefix_uses_sysroot(self):
        from zap.core.models import App

        with TempTree() as root:
            env = darwin_env(root)
            env.system = "linux"
            eng = Engine(env)
            self.assertTrue(eng._bundle_requires_root(App(name="a", identifier="a", path=root / "opt" / "a")))
            self.assertFalse(eng._bundle_requires_root(App(name="a", identifier="a", path=root / "optional" / "a")))
            self.assertFalse(eng._bundle_requires_root(App(name="a", identifier="a", path=Path("/opt/a"))))

    def test_linux_case_variant_ids_are_distinct_apps(self):
        with TempTree() as root:
            env = darwin_env(root)
            env.system = "linux"
            apps = root / "usr" / "share" / "applications"
            apps.mkdir(parents=True)
            (apps / "Probe").write_text("")
            if (apps / "probe").exists():
                self.skipTest("filesystem is case-insensitive")
            for ident in ("org.acme.App", "org.acme.app"):
                (apps / f"{ident}.desktop").write_text(f"[Desktop Entry]\nName={ident}\nExec=x\n")
            ids = {a.identifier for a in Engine(env).list_apps()}
            self.assertEqual(ids, {"org.acme.App", "org.acme.app"})

    def test_plan_with_scan_flags_rejected_before_rescan(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            pf = root / "plan.json"
            run(["--json", "scan", "Widget", "-o", str(pf)], env)
            obj = json.loads(pf.read_text())
            obj["app"]["identifier"] = "com.nope.Missing"
            pf.write_text(json.dumps(obj))
            rc, out = run(["--json", "uninstall", "--plan", str(pf), "--aggressive"], env)
            self.assertEqual(out["error"]["code"], "usage_error")
