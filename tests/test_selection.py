"""Table-driven test of the single auto-selection authority."""

from __future__ import annotations

import unittest
from pathlib import Path

from zap.core.engine import Engine
from zap.core.models import (
    CAT_APP_BUNDLE,
    CAT_CACHES,
    CAT_SYSTEM_EXTENSION,
    CONFIDENCE_DERIVED,
    CONFIDENCE_EXACT,
    CONFIDENCE_HEURISTIC,
    Artifact,
    Match,
)


def art(category=CAT_CACHES, key="bundle_id", conf=CONFIDENCE_EXACT, **kw) -> Artifact:
    return Artifact(path=Path("/x"), category=category, match=Match(key, "v", conf), risk="normal", **kw)


# (artifact, default, --aggressive, --include-low)
TABLE = [
    (art(category=CAT_APP_BUNDLE), True, True, True),
    (art(category=CAT_APP_BUNDLE, requires_root=True), False, False, False),
    (art(category=CAT_SYSTEM_EXTENSION), False, False, False),
    (art(shared=True), False, False, False),
    (art(requires_root=True), False, False, False),
    (art(key="bundle_id"), True, True, True),
    (art(key="byhost"), True, True, True),
    (art(key="plugin_bundle_id"), True, True, True),
    (art(key="app_path"), False, True, True),  # exact but not in AUTO_EXACT_KEYS
    (art(key="bundle_id_prefix", conf=CONFIDENCE_DERIVED), False, True, True),
    (art(key="vendor_folder", conf=CONFIDENCE_DERIVED), False, True, True),
    (art(key="name", conf=CONFIDENCE_HEURISTIC), False, False, True),
    (art(key="bogus", conf="unknown"), False, False, False),
]


class SelectionTiers(unittest.TestCase):
    def test_table(self):
        for a, default, aggressive, low in TABLE:
            with self.subTest(a=a.match, cat=a.category, shared=a.shared, root=a.requires_root):
                self.assertEqual(Engine._auto_select(a, aggressive=False, include_low=False), default)
                self.assertEqual(Engine._auto_select(a, aggressive=True, include_low=False), aggressive)
                # --include-low implies --aggressive (see _apply_selection_tiers)
                self.assertEqual(Engine._auto_select(a, aggressive=True, include_low=True), low)


if __name__ == "__main__":
    unittest.main()
