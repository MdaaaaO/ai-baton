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
                          ("context-db/bin/leak_shapes.py", True), ("docs/fixtures.md", False),
                          ("skills/pr-open/node_modules/mermaid/index.js", True),  # vendored: `npm ci` in skills/pr-open/
                          ("skills/pr-open/mermaid-check.mjs", False)):  # the kit's own file beside it: still scanned
            self.assertEqual(leak_shapes.skip_path(rel), skip, rel)

    def test_both_scanners_use_it_and_keep_no_list_of_their_own(self):
        for rel in ("context-db/bin/review_gate.py", "skills/kit-health/kit-health.py"):
            src = (KIT / rel).read_text(encoding="utf-8")
            self.assertIn("leak_shapes.skip_path(", src, rel)
            self.assertNotRegex(src, r"(?m)^SKIP_SUFFIX(ES)?\s*=|^TREE_SKIP_DIRS\s*=", rel)
            if "kit-health" in rel:
                self.assertIn("leak_shapes.line_hits(", src)


if __name__ == "__main__":
    unittest.main()
