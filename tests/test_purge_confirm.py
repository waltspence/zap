"""--purge --yes must be acknowledged with --confirm-purge."""

from __future__ import annotations

import json
import unittest

from tests.test_engine import build_widget_tree, darwin_env
from tests.util import TempTree
from zap import cli
from zap.core.envelope import EXIT_CONFIRMATION


class PurgeConfirmation(unittest.TestCase):
    def test_purge_without_confirm_is_refused(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["--json", "uninstall", "Widget", "--yes", "--purge"], env=env)
            self.assertEqual(rc, EXIT_CONFIRMATION)
            self.assertEqual(json.loads(buf.getvalue())["error"]["code"], "confirmation_required")
            self.assertTrue((root / "Applications" / "Widget.app").exists())

    def test_purge_dry_run_needs_no_confirm(self):
        with TempTree() as root:
            build_widget_tree(root)
            env = darwin_env(root)
            import io
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                rc = cli.main(["--json", "uninstall", "Widget", "--purge"], env=env)
            self.assertEqual(rc, 0)
            self.assertTrue((root / "Applications" / "Widget.app").exists())


if __name__ == "__main__":
    unittest.main()
