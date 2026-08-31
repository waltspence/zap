"""Preflight refuses before acting when permissions predictably fail."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zap.core import preflight  # noqa: E402


class FakeEnv:
    def __init__(self, home, uid=None):
        self.home = Path(home)
        self.uid = os.getuid() if uid is None else uid


class Art:
    def __init__(self, path, requires_root=False):
        self.path = Path(path)
        self.requires_root = requires_root


class PreflightTest(unittest.TestCase):
    def test_requires_root_blocks(self):
        env = FakeEnv("/tmp")
        b = preflight.check(env, [Art("/opt/x", requires_root=True)], fda_probe=lambda e: True)
        self.assertEqual([x["code"] for x in b], ["needs_root"])
        self.assertIn("sudo", b[0]["message"])

    def test_root_owned_path_blocks(self):
        env = FakeEnv("/tmp")
        b = preflight.check(env, [Art("/")], fda_probe=lambda e: True)  # / is root-owned
        self.assertEqual([x["code"] for x in b], ["needs_root"])

    def test_running_as_root_never_needs_root(self):
        env = FakeEnv("/tmp", uid=0)
        b = preflight.check(env, [Art("/", requires_root=True)], fda_probe=lambda e: True)
        self.assertEqual([x["code"] for x in b if x["code"] == "needs_root"], [])

    @unittest.skipUnless(sys.platform == "darwin", "darwin only")
    def test_tcc_path_without_fda_blocks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            env = FakeEnv(td)
            art = Art(os.path.join(td, "Library/Containers/com.x.y"))
            b = preflight.check(env, [art], fda_probe=lambda e: False)
            self.assertEqual([x["code"] for x in b], ["needs_full_disk_access"])
            self.assertIn("Full Disk Access", b[0]["message"])
            # with FDA granted (or inconclusive) the same selection passes
            self.assertEqual(preflight.check(env, [art], fda_probe=lambda e: True), [])
            self.assertEqual(preflight.check(env, [art], fda_probe=lambda e: None), [])

    @unittest.skipUnless(sys.platform == "darwin", "darwin only")
    def test_ordinary_library_paths_do_not_require_fda(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            env = FakeEnv(td)
            arts = [Art(os.path.join(td, "Library/Preferences/com.x.plist")),
                    Art(os.path.join(td, "Library/Caches/com.x"))]
            self.assertEqual(preflight.check(env, arts, fda_probe=lambda e: False), [])

    def test_own_files_pass(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            f = os.path.join(td, "mine")
            open(f, "w").close()
            env = FakeEnv(td)
            self.assertEqual(preflight.check(env, [Art(f)], fda_probe=lambda e: True), [])


if __name__ == "__main__":
    unittest.main()
