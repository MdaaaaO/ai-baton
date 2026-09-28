"""Calendar-year genericity in the self-assessment and pr-review skills: a `YYYY-Wnn` example must stay
generic (no year baked into the prose) so the skill works in any calendar year, and the pr-review
auto-approve row must not carry a shadow-mode end date that expires and is never refreshed — that value
already lives in the `auto_approve.shadow_review_due` store fact. Stdlib unittest, reads the shipped
skill files directly (no env store). Run: make -C .claude/context-db test."""
from __future__ import annotations
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]

# A year glued straight onto a week number (`2026-Wnn`, `2026-W{nn-1}`) or a scaffold slug
# (`SLUG=2026-Wnn`) or an inbox drop name (`inbox/2026-Wnn--...`): every such example must read
# `YYYY-Wnn`, not a specific year, so a session run in any calendar year copies it correctly.
YEAR_GLUED_TO_WEEK_RE = re.compile(r"\b(?:SLUG=)?20\d\d-W(?:nn|\{|\d)")


class GenericWeekExamples(unittest.TestCase):
    def test_self_assessment_skill_has_no_year_glued_to_a_week_example(self):
        text = (KIT / "skills" / "self-assessment" / "SKILL.md").read_text(encoding="utf-8")
        hits = YEAR_GLUED_TO_WEEK_RE.findall(text)
        self.assertEqual(hits, [], f"hardcoded year in a week example: {hits}")

    def test_self_assessment_template_has_no_year_glued_to_a_week_example(self):
        text = (KIT / "context-db" / "_templates" / "self-assessment.md").read_text(encoding="utf-8")
        hits = YEAR_GLUED_TO_WEEK_RE.findall(text)
        self.assertEqual(hits, [], f"hardcoded year in a week example: {hits}")

    def test_pr_review_skill_has_no_year_glued_to_a_week_example(self):
        text = (KIT / "skills" / "pr-review" / "SKILL.md").read_text(encoding="utf-8")
        hits = YEAR_GLUED_TO_WEEK_RE.findall(text)
        self.assertEqual(hits, [], f"hardcoded year in a week example: {hits}")


class DateGrepIsYearAgnostic(unittest.TestCase):
    def test_the_date_grep_example_derives_its_year_instead_of_hardcoding_one(self):
        text = (KIT / "skills" / "self-assessment" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\b20\d\d-09-\(0", "the date-grep example hardcodes a specific year")
        self.assertIn("date +%Y", text, "the date-grep example should derive the year from `date`, not hardcode it")


class ShadowDateNeverHardcoded(unittest.TestCase):
    def test_scope_md_names_the_store_fact_not_a_literal_expiry_date(self):
        text = (KIT / "skills" / "pr-review" / "reference" / "scope.md").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"shadow until 20\d\d-\d\d-\d\d",
                            "a shadow-mode end date belongs in the store (auto_approve.shadow_review_due), "
                            "never a literal date that expires and is never refreshed")
        self.assertIn("auto_approve.shadow_review_due", text)


if __name__ == "__main__":
    unittest.main()
