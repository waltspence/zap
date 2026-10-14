"""GUI tests: exercise zap.gui without subprocesses, popups or a display loop.

Every zap call is stubbed via ``run_zap``; the Tk root is withdrawn; a hard
SIGALRM kills the test if Tk ever blocks (the failure mode that hung earlier
ad-hoc runs). Skipped where no Tk display is available (CI Linux).
"""

import os
import signal
import sys
import tkinter as tk
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zap import gui  # noqa: E402

HARD_TIMEOUT_S = 20

APPS = {"ok": True, "data": {"apps": [
    {"name": "Grumble Studio", "identifier": "com.zappatest.grumble", "bundle_id": "com.zappatest.grumble",
     "display_name": "Grumble Studio", "version": "1.3.7", "protected": False},
    {"name": "Finder", "identifier": "com.apple.finder", "bundle_id": "com.apple.finder",
     "display_name": "Finder", "version": "1", "protected": True},
]}}


def art(i, cat, conf, auto, size=10, **kw):
    return {"id": i, "path": f"/x/{cat}", "category": cat, "match": {"confidence": conf},
            "risk": "normal", "size_bytes": size, "auto_selected": auto, "requires_root": False,
            "shared": False, **kw}


PLAN = {"ok": True, "data": {"app": APPS["data"]["apps"][0], "warnings": [], "artifacts": [
    art("a1", "app_bundle", "exact", True, 500),
    art("a2", "caches", "exact", True, 2000),
    art("a3", "application_support", "heuristic", False, 300),
]}}
REPORT = {"ok": True, "data": {"counts": {"removed": 2}, "bytes_reclaimed": 2500, "results": [
    {"id": "a1", "path": "/x/app_bundle", "status": "removed"},
    {"id": "a2", "path": "/x/caches", "status": "removed"},
]}}
HISTORY = {"ok": True, "data": {"operations": [
    {"operation_id": "20260829-000000-deadbeef", "app_name": "Grumble Studio", "created_at": "2026-08-29T00:00:00Z",
     "method": "trash", "item_count": 2, "reversible_count": 2}]}}
UNDO = {"ok": True, "data": {"results": [{"status": "restored"}, {"status": "restored"}]}}


def _tk_available():
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except tk.TclError:
        return False


@unittest.skipUnless(_tk_available(), "no Tk display")
class GuiTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.replies = {"list": APPS, "scan": PLAN, "uninstall": REPORT, "history": HISTORY, "undo": UNDO}

        def fake_run(args):
            self.calls.append(args)
            return self.replies[args[0]]

        self._orig = gui.run_zap
        gui.run_zap = fake_run
        gui.messagebox.askokcancel = lambda *a, **k: True
        gui.messagebox.showwarning = lambda *a, **k: None
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("Tk blocked")))
        signal.alarm(HARD_TIMEOUT_S)
        self.root = tk.Tk()
        self.root.withdraw()
        self.g = gui.ZapGUI(self.root)
        self.settle()

    def tearDown(self):
        signal.alarm(0)
        gui.run_zap = self._orig
        self.root.destroy()

    def settle(self, max_iter=200):
        """Pump Tk until the worker is idle; bounded so a bug can't spin forever."""
        for _ in range(max_iter):
            self.root.update()
            if not self.g.busy and self.g.q.empty():
                return
            self.g.root.after(10)
        self.fail("GUI never became idle")

    def test_list_hides_protected_apps(self):
        self.assertEqual([a["name"] for a in self.g.apps], ["Grumble Studio"])
        self.assertEqual(self.g.status.cget("text"), "1 apps")

    def test_scan_toggle_uninstall_undo_round_trip(self):
        g = self.g
        g.applist.selection_set(0)
        g.scan()
        self.settle()
        self.assertEqual(g.status.cget("text"), "2 of 3 selected · 2.4 KB")
        self.assertEqual(g.tree.item("a3")["values"][0], "☐")

        g._toggle("a2")  # untick caches
        self.assertEqual(g.status.cget("text"), "1 of 3 selected · 500 B")

        g.uninstall()
        self.settle()
        cmd = next(c for c in self.calls if c[0] == "uninstall")
        self.assertEqual(cmd[-1], "a1")  # only ticked ids go to the CLI
        self.assertIn("--only", cmd)
        self.assertIn("2 removed", g.status.cget("text"))
        self.assertEqual(str(g.btn_go.cget("state")), "disabled")

        g.undo()
        self.settle()
        self.assertEqual(g.btn_go.cget("text"), "Restore")
        self.assertEqual(list(g.tree.get_children()), ["20260829-000000-deadbeef"])
        g.tree.selection_set("20260829-000000-deadbeef")
        g._restore()
        self.settle()
        self.assertEqual(self.calls[-2], ["undo", "20260829-000000-deadbeef"])
        self.assertEqual(g.status.cget("text"), "restored 2 of 2")
        self.assertEqual(g.btn_go.cget("text"), "Move to Trash")

    def test_aggressive_rescans_with_flag(self):
        self.g.applist.selection_set(0)
        self.g.aggressive.set(True)
        self.g.scan()
        self.settle()
        self.assertEqual(self.calls[-1], ["scan", "com.zappatest.grumble", "--aggressive"])

    def test_error_envelope_lands_in_status(self):
        self.replies["scan"] = {"ok": False, "error": {"code": "app_not_found", "message": "nope"}}
        self.g.applist.selection_set(0)
        self.g.scan()
        self.settle()
        self.assertEqual(self.g.status.cget("text"), "app_not_found: nope")
        self.assertEqual(str(self.g.btn_go.cget("state")), "disabled")


if __name__ == "__main__":
    unittest.main()


class FdaProbeTest(unittest.TestCase):
    def test_probe_states(self):
        import tempfile
        if sys.platform != "darwin":
            self.assertIsNone(gui.has_full_disk_access())
            return
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(gui.has_full_disk_access(td))  # no db → inconclusive
            db = os.path.join(td, "Library", "Application Support", "com.apple.TCC")
            os.makedirs(db)
            open(os.path.join(db, "TCC.db"), "wb").close()
            self.assertTrue(gui.has_full_disk_access(td))
