"""Tests for trash routing, collision handling, and crash-safe moves."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.util import TempTree
from zap.core import fsutil
from zap.core.trash import MacTrash, XDGTrash
from zap.environment import Environment


def env_with_devices(root: Path, system: str, device_map) -> Environment:
    home = root / "home" / "user"
    home.mkdir(parents=True, exist_ok=True)

    def device_of(p: Path) -> int:
        s = str(p)
        for prefix, dev in device_map:
            if s == str(prefix) or s.startswith(str(prefix) + os.sep):
                return dev
        # nearest existing ancestor fallback -> default device 1
        return 1

    return Environment(
        system=system,
        home=home,
        sysroot=root,
        state_home=home / ".state",
        uid=501,
        is_root=False,
        is_tty=False,
        device_of=device_of,
        now=lambda: datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc),
    )


class FsUtilTests(unittest.TestCase):
    def test_tree_size(self):
        with TempTree() as root:
            d = root / "a"
            (d / "sub").mkdir(parents=True)
            (d / "f1").write_bytes(b"x" * 100)
            (d / "sub" / "f2").write_bytes(b"y" * 50)
            total, count = fsutil.tree_size(d)
            self.assertGreaterEqual(total, 150)
            self.assertGreaterEqual(count, 3)  # sub, f1, f2

    def test_remove_tree_does_not_follow_symlink(self):
        with TempTree() as root:
            outside = root / "outside"
            outside.mkdir()
            (outside / "precious").write_text("keep")
            victim = root / "victim"
            victim.mkdir()
            os.symlink(outside, victim / "link_to_outside")
            (victim / "own").write_text("gone")
            fsutil.remove_tree(victim)
            self.assertFalse(victim.exists())
            self.assertTrue((outside / "precious").exists(), "must not follow symlink")

    def test_safe_move_same_device_rename(self):
        with TempTree() as root:
            env = env_with_devices(root, "linux", [(root, 1)])
            src = root / "src.txt"
            src.write_text("hello")
            dst = root / "dst.txt"
            fsutil.safe_move(env, src, dst)
            self.assertFalse(src.exists())
            self.assertEqual(dst.read_text(), "hello")

    def test_safe_move_cross_device_copies_then_removes(self):
        with TempTree() as root:
            vol = root / "vol"
            vol.mkdir()
            # src on device 99, dst parent on device 1 -> cross-device path.
            env = env_with_devices(root, "linux", [(vol, 99)])
            src = vol / "data"
            src.mkdir()
            (src / "file").write_bytes(b"z" * 200)
            dst = root / "moved"
            fsutil.safe_move(env, src, dst)
            self.assertFalse(src.exists(), "source removed after successful copy")
            self.assertEqual((dst / "file").read_bytes(), b"z" * 200)

    def test_safe_move_refuses_existing_dst(self):
        with TempTree() as root:
            env = env_with_devices(root, "linux", [(root, 1)])
            src = root / "s"
            src.write_text("a")
            dst = root / "d"
            dst.write_text("b")
            with self.assertRaises(FileExistsError):
                fsutil.safe_move(env, src, dst)
            self.assertTrue(src.exists())


class XDGTrashTests(unittest.TestCase):
    def test_home_volume_trash_and_restore(self):
        with TempTree() as root:
            env = env_with_devices(root, "linux", [(root, 1)])
            trash = XDGTrash(env)
            target = env.home / "Library" / "Caches" / "com.acme.Widget"
            target.mkdir(parents=True)
            (target / "blob").write_bytes(b"c" * 10)
            item = trash.trash(target)
            self.assertFalse(target.exists())
            self.assertTrue(Path(item.location).exists())
            self.assertIn(".local/share/Trash/files", item.location)
            self.assertTrue(Path(item.info).exists())
            info_text = Path(item.info).read_text()
            self.assertIn("[Trash Info]", info_text)
            self.assertIn("DeletionDate=2026-08-24T12:00:00", info_text)
            # restore
            restored = trash.restore(Path(item.original), Path(item.location), item.info)
            self.assertTrue((restored / "blob").exists())
            self.assertFalse(Path(item.info).exists())

    def test_per_volume_trash_for_other_device(self):
        with TempTree() as root:
            vol = root / "Volumes" / "External"
            vol.mkdir(parents=True)
            env = env_with_devices(root, "linux", [(vol, 42)])
            trash = XDGTrash(env)
            target = vol / "Application Support" / "com.acme.Ext"
            target.mkdir(parents=True)
            (target / "x").write_text("data")
            item = trash.trash(target)
            expected_root = vol / f".Trash-{env.uid}" / "files"
            self.assertTrue(str(item.location).startswith(str(expected_root)))
            # Path= should be relative to the volume top dir.
            info_text = Path(item.info).read_text()
            self.assertIn("Path=Application%20Support/com.acme.Ext", info_text)

    def test_name_collision_disambiguated(self):
        with TempTree() as root:
            env = env_with_devices(root, "linux", [(root, 1)])
            trash = XDGTrash(env)
            caches = env.home / "Library" / "Caches"
            caches.mkdir(parents=True)
            first = caches / "dup"
            first.write_text("1")
            item1 = trash.trash(first)
            second = caches / "dup"
            second.write_text("2")
            item2 = trash.trash(second)
            self.assertNotEqual(item1.location, item2.location)
            self.assertTrue(item2.location.endswith("dup.1"))

    def test_restore_refuses_to_overwrite(self):
        with TempTree() as root:
            env = env_with_devices(root, "linux", [(root, 1)])
            trash = XDGTrash(env)
            caches = env.home / "Library" / "Caches"
            caches.mkdir(parents=True)
            target = caches / "thing"
            target.write_text("orig")
            item = trash.trash(target)
            # user recreated a file at the original path
            target.write_text("new")
            with self.assertRaises(Exception):
                trash.restore(Path(item.original), Path(item.location), item.info)


class MacTrashTests(unittest.TestCase):
    def test_home_trash_no_info_file(self):
        with TempTree() as root:
            env = env_with_devices(root, "darwin", [(root, 1)])
            trash = MacTrash(env)
            target = env.home / "Library" / "Caches" / "com.acme.Widget"
            target.mkdir(parents=True)
            (target / "b").write_text("x")
            item = trash.trash(target)
            self.assertIsNone(item.info)
            self.assertTrue(str(item.location).startswith(str(env.home / ".Trash")))
            self.assertTrue(Path(item.location).exists())

    def test_other_volume_uses_dot_trashes(self):
        with TempTree() as root:
            vol = root / "Volumes" / "Ext"
            vol.mkdir(parents=True)
            env = env_with_devices(root, "darwin", [(vol, 7)])
            trash = MacTrash(env)
            target = vol / "SomeApp"
            target.mkdir()
            item = trash.trash(target)
            self.assertIn(f".Trashes/{env.uid}", item.location)


if __name__ == "__main__":
    unittest.main()
