"""kit_verify — a unit body states the current rule only: dated history (an owner-decision provenance line, a
YYYY-MM-DD date, "added/retired <year>") warns, frontmatter and code (fenced blocks, inline spans) are exempt;
and every unit's body size (lines + bytes) is reported where the body-line count already is. Reads fixture
strings and the kit's own units only — no env store, no git. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import contextlib
import io
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "bin"))

import kit_verify  # noqa: E402

FIX = HERE / "fixtures" / "skills" / "good-skill" / "SKILL.md"
DATE = "20" + "26-09-22"  # assembled so a body scan of this file never reads it as a hit


def warns_for(body: str) -> list[str]:
    errors: list[str] = []
    warn: list[str] = []
    kit_verify.check_body(FIX, FIX.relative_to(KIT), body, errors, warn=warn)
    return [w for w in warn if "dated history" in w]


class DatedHistoryHits(unittest.TestCase):
    def test_owner_decision_line_is_a_hit(self):
        hits = kit_verify.dated_history_hits("# x\n\nOwner decision: drafts go out.\n")
        self.assertEqual([n for n, _ in hits], [3])

    def test_iso_date_in_prose_is_a_hit(self):
        hits = kit_verify.dated_history_hits(f"Spend is the total, since {DATE}.\n")
        self.assertEqual(len(hits), 1, hits)
        self.assertIn(DATE, hits[0][1])

    def test_added_or_retired_year_is_a_hit(self):
        self.assertEqual(len(kit_verify.dated_history_hits("The flag was retired 2026.\n")), 1)
        self.assertEqual(len(kit_verify.dated_history_hits("Added in 2025 for the sweep.\n")), 1)

    def test_fenced_block_is_exempt(self):
        body = f"Comment shape:\n```\n✅ {DATE} — Closing: <outcome>\n```\nDone.\n"
        self.assertEqual(kit_verify.dated_history_hits(body), [])

    def test_inline_code_is_exempt(self):
        body = f"Run `cost_report.py ingest --since {DATE}` for the fixture.\n"
        self.assertEqual(kit_verify.dated_history_hits(body), [])

    def test_current_rule_prose_is_clean(self):
        body = "State the rule. A week is Mon 00:00Z to Sun 23:59Z; name it `YYYY-Wnn`.\n"
        self.assertEqual(kit_verify.dated_history_hits(body), [])


class CheckBodyWarns(unittest.TestCase):
    def test_dated_body_warns_with_line_number(self):
        w = warns_for(f"# x\n\nTOTAL spend (owner decision, {DATE}).\n")
        self.assertEqual(len(w), 1, w)
        self.assertIn("body line 3", w[0])

    def test_clean_body_does_not_warn(self):
        self.assertEqual(warns_for("# x\n\nState the rule only.\n"), [])

    def test_frontmatter_is_never_scanned(self):
        # check_body receives the body only; the unit check splits frontmatter off before calling it, so a
        # metadata.updated date can never warn. Exercise the real split on a whole file.
        import frontmatter as fmt
        text = f'---\nname: x\nmetadata:\n  updated: "{DATE}"\n---\n\n# x\n\nState the rule.\n'
        _, body = fmt.split(text)
        self.assertEqual(warns_for(body), [])

    def test_kit_bodies_carry_no_dated_history(self):
        import frontmatter as fmt
        for p in sorted(fmt.units(KIT)):
            parts = fmt.split(p.read_text(encoding="utf-8"))
            with self.subTest(unit=str(p.relative_to(KIT))):
                self.assertEqual(kit_verify.dated_history_hits(parts[1] if parts else ""), [])


class BodySizes(unittest.TestCase):
    def test_every_unit_reports_lines_and_bytes(self):
        sizes = kit_verify.body_sizes()
        import frontmatter as fmt
        self.assertEqual(len(sizes), len(fmt.units(KIT)))
        for rel, lines, size in sizes:
            self.assertGreater(lines, 0, rel)
            self.assertGreater(size, 0, rel)
        self.assertEqual([s for _, _, s in sizes], sorted((s for _, _, s in sizes), reverse=True))

    def test_loading_table_cli_lists_per_unit_body_bytes(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kit_verify.main(["--loading-table"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("body-bytes:", out)
        self.assertRegex(out, r"(?m)^  skills/self-assessment/SKILL\.md: \d+ lines, [\d,]+ B$")

    def test_long_body_warning_names_its_bytes(self):
        errors: list[str] = []
        warn: list[str] = []
        body = "line\n" * (kit_verify.BODY_WARN_LINES + 1)
        kit_verify.check_body(FIX, FIX.relative_to(KIT), body, errors, warn=warn)
        self.assertTrue(any(f"{len(body.encode()):,} B" in w for w in warn), warn)


if __name__ == "__main__":
    unittest.main()
