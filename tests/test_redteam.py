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
