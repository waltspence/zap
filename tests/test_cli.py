"""CLI / agent-contract tests: JSON envelopes, exit codes, the full flow."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.test_engine import build_widget_tree, darwin_env
from tests.util import TempTree
from zap.cli import main


def run(argv, env):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv, env=env)
    return code, out.getvalue(), err.getvalue()


def run_json(argv, env):
    code, out, err = run(argv, env)
    payload = json.loads(out) if out.strip() else None
    return code, payload, err


class CliContractTests(unittest.TestCase):
    def test_list_json(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            code, payload, _ = run_json(["--json", "list"], env)
            self.assertEqual(code, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["schema_version"], 1)
            names = {a["name"] for a in payload["data"]["apps"]}
            self.assertIn("Widget", names)

    def test_scan_json_shape(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            code, payload, _ = run_json(["--json", "scan", "Widget"], env)
            self.assertEqual(code, 0)
            plan = payload["data"]
            self.assertIn("plan_id", plan)
            self.assertTrue(any(a["auto_selected"] for a in plan["artifacts"]))
            for a in plan["artifacts"]:
                self.assertEqual(len(a["id"]), 16)
                self.assertIn(a["match"]["confidence"], {"exact", "derived", "heuristic"})

    def test_app_not_found_exit_3(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            code, payload, _ = run_json(["--json", "scan", "NoSuchApp"], env)
            self.assertEqual(code, 3)
            self.assertEqual(payload["error"]["code"], "app_not_found")

    def test_uninstall_dry_run_by_default(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            code, payload, _ = run_json(["--json", "uninstall", "Widget"], env)
            self.assertEqual(code, 0)
            self.assertTrue(payload["data"]["dry_run"])
            self.assertTrue(all(r["status"] == "dry_run" for r in payload["data"]["results"]))
            # nothing actually removed
            self.assertTrue((root / "home/user/Library/Caches/com.acme.Widget").exists())
            self.assertTrue((root / "Applications/Widget.app").exists())

    def test_uninstall_yes_then_history_then_undo(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            code, payload, _ = run_json(["--json", "uninstall", "Widget", "--yes"], env)
            self.assertEqual(code, 0)
            op = payload["data"]["operation_id"]
            self.assertTrue(op)
            self.assertFalse((root / "Applications/Widget.app").exists())
            # sibling untouched
            self.assertTrue((root / "home/user/Library/Caches/com.acme.WidgetPro").exists())

            code, hist, _ = run_json(["--json", "history"], env)
            self.assertEqual(code, 0)
            self.assertTrue(any(o["operation_id"] == op for o in hist["data"]["operations"]))

            code, undo, _ = run_json(["--json", "undo", op], env)
            self.assertEqual(code, 0)
            self.assertTrue((root / "Applications/Widget.app").exists())

    def test_plan_round_trip(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            plan_file = str(root / "plan.json")
            code, _, _ = run(["scan", "Widget", "-o", plan_file], env)
            self.assertEqual(code, 0)
            self.assertTrue(Path(plan_file).exists())
            code, payload, _ = run_json(["--json", "uninstall", "--plan", plan_file, "--yes"], env)
            self.assertEqual(code, 0)
            self.assertFalse((root / "Applications/Widget.app").exists())

    def test_unknown_only_id_is_usage_error(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            code, payload, _ = run_json(
                ["--json", "uninstall", "Widget", "--yes", "--only", "deadbeefdeadbeef"], env
            )
            self.assertEqual(code, 2)
            self.assertEqual(payload["error"]["code"], "unknown_ids")

    def test_capabilities(self):
        with TempTree() as root:
            env = darwin_env(root)
            code, payload, _ = run_json(["--json", "capabilities"], env)
            self.assertEqual(code, 0)
            self.assertEqual(payload["data"]["platform"], "darwin")
            self.assertIn("uninstall", payload["data"]["commands"])

    def test_json_stdout_is_single_object(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            code, out, err = run(["--json", "scan", "Widget"], env)
            # exactly one JSON object, nothing else on stdout
            json.loads(out)  # raises if not exactly one object
            self.assertEqual(out.count("\n"), 1)

    def test_orphans(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            # com.other.Thing has no installed owner -> orphan
            code, payload, _ = run_json(["--json", "orphans"], env)
            self.assertEqual(code, 0)
            paths = {a["path"] for a in payload["data"]["artifacts"]}
            self.assertTrue(any("com.other.Thing" in p for p in paths))
            # none are auto-selected
            self.assertFalse(any(a["auto_selected"] for a in payload["data"]["artifacts"]))

    def test_orphans_du(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            code, out, _ = run(["orphans", "--du"], env)
            self.assertEqual(code, 0)
            lines = [ln for ln in out.splitlines() if ln]
            self.assertTrue(any("com.other.Thing" in ln for ln in lines))
            for ln in lines:
                kb, _sep, path = ln.partition("\t")
                self.assertTrue(kb.isdigit() and int(kb) >= 1, ln)
                self.assertTrue(path.startswith("/"), ln)


if __name__ == "__main__":
    unittest.main()
