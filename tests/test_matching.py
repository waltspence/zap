"""Tests for attribution logic: prefixes, sibling guard, confidence tiers."""

from __future__ import annotations

import unittest

from zap.core import matching as M
from zap.core.models import CONFIDENCE_DERIVED, CONFIDENCE_EXACT, CONFIDENCE_HEURISTIC


def keys(bundle_id=None, team_id=None, names=(), execs=(), others=()):
    return M.AppKeys(
        bundle_id=bundle_id,
        team_id=team_id,
        names=M.build_name_set(*names),
        executables={n.casefold() for n in execs},
        other_bundle_ids=set(others),
    )


class MatchBundleChildTests(unittest.TestCase):
    def test_exact_bundle_id(self):
        k = keys(bundle_id="com.google.Chrome")
        m = M.match_bundle_child("com.google.Chrome", k)
        self.assertEqual((m.key, m.confidence), ("bundle_id", CONFIDENCE_EXACT))

    def test_dot_boundary_prefix_is_derived(self):
        k = keys(bundle_id="com.google.Chrome")
        m = M.match_bundle_child("com.google.Chrome.helper", k)
        self.assertEqual(m.confidence, CONFIDENCE_DERIVED)

    def test_sibling_app_not_swept(self):
        # Chrome must not claim Chrome Canary's data.
        k = keys(bundle_id="com.google.Chrome")
        self.assertIsNone(M.match_bundle_child("com.google.ChromeCanary", k))

    def test_sibling_guard_prefix_belongs_to_other_installed_app(self):
        # A helper id that is actually a different installed app is dropped.
        k = keys(
            bundle_id="com.acme.Suite",
            others=["com.acme.Suite.Editor"],
        )
        self.assertIsNone(M.match_bundle_child("com.acme.Suite.Editor", k))

    def test_team_prefixed_cache(self):
        k = keys(bundle_id="com.acme.Widget", team_id="UBF8T346G9")
        m = M.match_bundle_child("UBF8T346G9.com.acme.Widget", k)
        self.assertEqual(m.confidence, CONFIDENCE_DERIVED)

    def test_shipit_suffix_cache(self):
        k = keys(bundle_id="com.acme.Widget")
        m = M.match_bundle_child("com.acme.Widget.ShipIt", k)
        self.assertIsNotNone(m)

    def test_no_bundle_id_no_match(self):
        k = keys(bundle_id=None)
        self.assertIsNone(M.match_bundle_child("com.acme.Widget", k))


class MatchNameChildTests(unittest.TestCase):
    def test_name_match_is_heuristic(self):
        k = keys(bundle_id="com.tinyspeck.slackmacgap", names=["Slack"])
        m = M.match_name_child("Slack", k)
        self.assertEqual(m.confidence, CONFIDENCE_HEURISTIC)

    def test_reserved_name_never_matches(self):
        k = keys(names=["systemd"])  # build_name_set drops it entirely
        self.assertIsNone(M.match_name_child("systemd", k))

    def test_unrelated_name_no_match(self):
        k = keys(names=["Slack"])
        self.assertIsNone(M.match_name_child("Photoshop", k))


class AutoSelectTests(unittest.TestCase):
    def test_exact_bundle_id_auto(self):
        m = M.Match("bundle_id", "com.acme.Widget", CONFIDENCE_EXACT)
        self.assertTrue(M.is_auto_selectable(m, shared=False, requires_root=False))

    def test_prefix_not_auto(self):
        m = M.Match("bundle_id_prefix", "com.acme.Widget.helper", CONFIDENCE_DERIVED)
        self.assertFalse(M.is_auto_selectable(m, shared=False, requires_root=False))

    def test_name_not_auto(self):
        m = M.Match("name", "Slack", CONFIDENCE_HEURISTIC)
        self.assertFalse(M.is_auto_selectable(m, shared=False, requires_root=False))

    def test_shared_never_auto(self):
        m = M.Match("bundle_id", "com.acme.Widget", CONFIDENCE_EXACT)
        self.assertFalse(M.is_auto_selectable(m, shared=True, requires_root=False))

    def test_root_never_auto(self):
        m = M.Match("bundle_id", "com.acme.Widget", CONFIDENCE_EXACT)
        self.assertFalse(M.is_auto_selectable(m, shared=False, requires_root=True))


if __name__ == "__main__":
    unittest.main()
