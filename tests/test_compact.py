"""--compact / --summary JSON modes and the dropped-items cap."""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from tests.test_engine import build_widget_tree, darwin_env
from tests.util import TempTree
from zap import cli
from zap.core.models import Plan


def run(args, env):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(args, env=env)
    return rc, json.loads(buf.getvalue())


class CompactJson(unittest.TestCase):
    def test_compact_is_smaller_and_omits_fingerprints(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            _, full = run(["--json", "scan", "Widget"], env)
            _, compact = run(["--json", "--compact", "scan", "Widget"], env)
            self.assertLess(len(json.dumps(compact)), len(json.dumps(full)) * 0.6)
            for a in compact["data"]["artifacts"]:
                self.assertNotIn("fingerprint", a)
                self.assertIsInstance(a["match"], str)
            self.assertTrue(compact["data"]["compact"])
            self.assertIn("notes", compact["data"])
            # Same artifacts, same ids, same selection.
            self.assertEqual(
                [(a["id"], a["auto_selected"]) for a in compact["data"]["artifacts"]],
                [(a["id"], a["auto_selected"]) for a in full["data"]["artifacts"]],
            )

    def test_output_file_stays_full_and_compact_plan_replays(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            pf = root / "plan.json"
            run(["--json", "--compact", "scan", "Widget", "-o", str(pf)], env)
            written = json.loads(pf.read_text())
            self.assertFalse(written["compact"])
            self.assertIn("fingerprint", written["artifacts"][0])
            Plan.from_dict(written)
            # A compact payload is a valid selection: same ids, same auto_selected.
            _, compact = run(["--json", "--compact", "scan", "Widget"], env)
            cf = root / "compact.json"
            cf.write_text(json.dumps(compact))
            (env.home / ".Trash").mkdir(exist_ok=True)
            rc, out = run(["--json", "uninstall", "--plan", str(cf), "--yes"], env)
            self.assertEqual(rc, 0, out.get("error"))
            self.assertFalse((root / "Applications" / "Widget.app").exists())

    def test_summary_omits_removed_results(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            (env.home / ".Trash").mkdir(exist_ok=True)
            _, out = run(["--json", "uninstall", "Widget", "--yes", "--summary"], env)
            self.assertEqual(out["data"]["results"], [])
            self.assertGreater(out["data"]["results_omitted"], 0)
            self.assertGreater(out["data"]["counts"]["removed"], 0)

    def test_dropped_items_capped_on_stdout(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            # A symlinked ancestor makes every child a guard reject.
            lib = env.home / "Library"
            (root / "elsewhere").mkdir()
            (lib / "Logs").symlink_to(root / "elsewhere")
            for i in range(30):
                (root / "elsewhere" / f"com.acme.Widget.l{i}").mkdir()
            pf = root / "plan.json"
            _, out = run(["--json", "scan", "Widget", "-o", str(pf)], env)
            w = next(w for w in out["data"]["warnings"] if w["code"] == "dropped_by_safety")
            self.assertEqual(len(w["items"]), cli.MAX_DROPPED_ON_STDOUT)
            self.assertEqual(w["items_truncated"], 10)
            full = next(w for w in json.loads(pf.read_text())["warnings"] if w["code"] == "dropped_by_safety")
            self.assertEqual(len(full["items"]), 30)
            self.assertNotIn("items_truncated", full)


if __name__ == "__main__":
    unittest.main()
