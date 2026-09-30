"""leak_shapes.py (#164): every match on a line is checked against the allow-list, an allow entry covers the whole path,
and the review gate and kit-health skip the same files. Fact-shaped literals are assembled at run time.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import re
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
KIT = BIN.parents[1]
sys.path.insert(0, str(BIN))
import leak_shapes  # noqa: E402

CH1, CH2 = "C0" + "AB12CD3EF", "C0" + "ZZ98YX7WV"   # Slack-shaped, assembled


def allow(*lines: str):
    return tuple(re.compile(x) for x in lines)


class EveryMatch(unittest.TestCase):
    def test_an_allowed_first_match_does_not_hide_a_second(self):
        text = f"example {CH1} and real {CH2}\n"
        hits = leak_shapes.scan(text, rel="docs/a.md", allow=allow(f"docs/a.md:{CH1}$"))
        self.assertEqual([(n, h) for n, _w, h in hits], [(1, CH2)])

    def test_two_unallowed_values_on_one_line_are_two_hits(self):
        hits = leak_shapes.scan(f"{CH1} {CH2}\n", rel="docs/a.md")
        self.assertEqual([h for _n, _w, h in hits], [CH1, CH2])

    def test_one_span_matched_by_two_shapes_is_one_hit(self):
        key = "ABC" + "-12"  # assembled: the scanners read this file too
        pats = [(re.compile(r"\bABC-\d+\b"), "generic"), (re.compile(r"\bABC-\d+"), "key_regex")]
        self.assertEqual(leak_shapes.line_hits(f"see {key} now", pats), [("generic", key)])


class AllowAnchoring(unittest.TestCase):
    def test_an_entry_covers_the_whole_path(self):
        self.assertTrue(leak_shapes.is_allowed("docs/a.md", CH1, allow(f"docs/a.md:{CH1}$")))
        self.assertFalse(leak_shapes.is_allowed("docs/ab.md", CH1, allow("docs/a")))        # a path prefix is not the path
        self.assertFalse(leak_shapes.is_allowed("xdocs/a.md", CH1, allow(f"docs/a.md:{CH1}")))  # anchored at the start
        self.assertTrue(leak_shapes.is_allowed("docs/a.md", CH1, allow("docs/a\\.md:C0")))    # a hit prefix is fine


class OneSkipRule(unittest.TestCase):
    def test_skip_path(self):
        for rel, skip in (("context-db/tests/fixtures/leaky.md", True), ("skills/cost-report/fixtures/x.json", True),
                          ("docs/a.md", False), ("docs/pic.PNG", True), ("skills/kit-health/allow.txt", True),
                          ("context-db/bin/leak_shapes.py", True), ("docs/fixtures.md", False)):
            self.assertEqual(leak_shapes.skip_path(rel), skip, rel)

    def test_both_scanners_use_it_and_keep_no_list_of_their_own(self):
        for rel in ("context-db/bin/review_gate.py", "skills/kit-health/kit-health.py"):
            src = (KIT / rel).read_text(encoding="utf-8")
            self.assertIn("leak_shapes.skip_path(", src, rel)
            self.assertNotRegex(src, r"(?m)^SKIP_SUFFIX(ES)?\s*=|^TREE_SKIP_DIRS\s*=", rel)
            if "kit-health" in rel:
                self.assertIn("leak_shapes.line_hits(", src)


class IgnoreCaseKeyRegex(unittest.TestCase):
    """`shapes(ignore_case=True)` — `kit_profile.py public-text-check` wants a lane name's lower-cased tracker
    key (`key-123-topic`) caught, without disturbing kit-health's own case-sensitive scan (the default)."""

    def test_default_is_case_sensitive(self):
        key = "KEY" + "-99"  # assembled, per the file's own scan
        pats = leak_shapes.shapes("jira", r"\bKEY-\d+\b")
        self.assertEqual(leak_shapes.line_hits(key.lower(), pats), [])

    def test_ignore_case_true_catches_the_lower_cased_key(self):
        key = "KEY" + "-99"
        pats = leak_shapes.shapes("jira", r"\bKEY-\d+\b", ignore_case=True)
        hits = leak_shapes.line_hits(key.lower(), pats)
        self.assertEqual([h for _w, h in hits], [key.lower()])

    def test_github_tracker_still_skips_key_regex_either_way(self):
        # a GitHub key is `#12` — no id to leak, so key_regex is never added regardless of ignore_case
        pats_a = leak_shapes.shapes("github", r"\bKEY-\d+\b")
        pats_b = leak_shapes.shapes("github", r"\bKEY-\d+\b", ignore_case=True)
        self.assertEqual(len(pats_a), len(leak_shapes.LEAK_SHAPES))
        self.assertEqual(len(pats_b), len(leak_shapes.LEAK_SHAPES))


class ConfiguredValues(unittest.TestCase):
    """`configured_values(cfg, facts, …)` — the shared value-gathering behind kit-health's leak scan and
    `kit_profile.py public-text-check`. Every value here is assembled at run time, per the file's own scan."""

    def cfg(self, **over):
        base = {"github": {"org": "acme" + "corp", "review_bot": "", "display_names": {}},
                "tracker": {"repos": ["acme" + "corp/widgets" + "-app"]}}
        base.update(over)
        return base

    def test_full_slug_and_bare_repo_name_are_both_patterns(self):
        pats, errors = leak_shapes.configured_values(self.cfg(), {})
        self.assertEqual(errors, [])
        text = "see " + "acmecorp/widgets-app" + " and bare " + "widgets-app" + "#7 here"
        hits = leak_shapes.scan(text, pats)
        matched = {h for _n, _w, h in hits}
        self.assertIn("acmecorp/widgets-app", matched)
        self.assertIn("widgets-app", matched)

    def test_common_repo_name_bare_form_is_not_flagged(self):
        cfg = self.cfg(tracker={"repos": ["acmecorp/docs"]})
        pats, _ = leak_shapes.configured_values(cfg, {})
        # the full slug is still a hit; the bare common word ("docs") is not, on its own
        self.assertTrue(any(w == "tracker.repos" for _p, w in pats))
        self.assertFalse(any(w == "tracker.repos (repo name)" for _p, w in pats))

    def test_repo_dependencies_skips_the_repo_entirely(self):
        cfg = self.cfg(tracker={"repos": ["acmecorp/toolkit" + "app"]})
        pats, _ = leak_shapes.configured_values(cfg, {}, repo_dependencies={"toolkitapp"})
        self.assertFalse(any("tracker.repos" in w for _p, w in pats))

    def test_org_path_pattern_and_not_dep_exclusion(self):
        pats, _ = leak_shapes.configured_values(self.cfg(), {}, org_not_dep=r"(?!(?:kit)\b)")
        hits = leak_shapes.scan("acmecorp/kit is the kit, acmecorp/other is not", pats)
        matched = {h for _n, _w, h in hits}
        self.assertNotIn("acmecorp/kit", matched)
        self.assertIn("acmecorp/other", matched)

    def test_fact_table_values_are_scanned(self):
        facts = {"slack": {"channel": {"eng": {"value": "team" + "-alerts-42"}}}}
        pats, errors = leak_shapes.configured_values({}, facts)
        self.assertEqual(errors, [])
        hits = leak_shapes.scan("posted to team-alerts-42 earlier", pats)
        self.assertTrue(any(h == "team-alerts-42" for _n, _w, h in hits))

    def test_malformed_facts_table_is_an_error_not_a_crash(self):
        pats, errors = leak_shapes.configured_values({}, {"slack": {"channel": "not-a-dict"}})
        self.assertEqual(pats, [])
        self.assertTrue(errors and "malformed" in errors[0])


class CrossOrgShapes(unittest.TestCase):
    def test_flags_a_different_org_hash_reference(self):
        pats = leak_shapes.cross_org_shapes("home" + "org")
        hits = leak_shapes.scan("see other" + "org/thing#42 for details", pats)
        self.assertEqual([h for _n, _w, h in hits], ["otherorg/thing#42"])

    def test_same_org_reference_is_not_flagged(self):
        pats = leak_shapes.cross_org_shapes("home" + "org")
        hits = leak_shapes.scan("see homeorg/thing#42 for details", pats)
        self.assertEqual(hits, [])

    def test_empty_owner_yields_no_shape(self):
        self.assertEqual(leak_shapes.cross_org_shapes(""), [])


class CommonWordKinds(unittest.TestCase):
    def test_reads_common_word_flag_from_manifests(self):
        manifests = {"github": {"facts": [{"key": "person name", "common_word": True, "target": "row"},
                                          {"key": "team slug", "common_word": False}]}}
        self.assertEqual(leak_shapes.common_word_kinds(manifests), {"person"})

    def test_empty_manifests_is_empty_set(self):
        self.assertEqual(leak_shapes.common_word_kinds({}), set())


if __name__ == "__main__":
    unittest.main()
