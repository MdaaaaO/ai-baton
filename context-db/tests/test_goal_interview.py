"""The goal interview: `docs/carousel.md` § Goal interview names the four questions, how their answers map onto the
opening block, the ledger rule and the haiku-only SKIP; `ticket-open` step 1b and `ticket-carousel`'s Draft step ask
it; the issue template carries the `Out of scope` line; the evals cover it and `evals/README.md` lists them. Stdlib
unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT / "context-db" / "bin"))

import done_when as dw  # noqa: E402

CASES = {
    "ticket-open-behaviour-sonnet-no-interview": "ticket-open",
    "ticket-open-behaviour-haiku-skip": "ticket-open",
    "ticket-carousel-behaviour-interview-before-draft": "ticket-carousel",
}


def squeeze(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}.*?(?=^## |\Z)", text, re.S | re.M)
    assert m, heading
    return m.group(0)


class CarouselDocSpec(unittest.TestCase):
    def setUp(self):
        self.doc = (KIT / "docs" / "carousel.md").read_text(encoding="utf-8")
        self.sec = section(self.doc, "Goal interview")
        self.flat = " ".join(self.sec.split())

    def test_four_questions_in_one_carousel_with_short_headers(self):
        for header in ("`Goal`", "`Out of scope`", "`Done when`", "`First slice`"):
            self.assertIn(header, self.sec)
        self.assertIn("exactly four questions", self.flat)
        for h in ("Goal", "Out of scope", "Done when", "First slice"):
            self.assertLessEqual(len(h), 12)

    def test_recommended_option_is_the_sessions_own_read(self):
        self.assertIn("session's own read of the request", self.flat)
        self.assertIn("(Recommended)", self.sec)

    def test_answers_map_onto_the_block(self):
        for target in ("`**Goal**` line", "`**Out of scope**` line", "`## Done when` lines", "Plan step 1"):
            self.assertIn(target, self.sec)
        self.assertIn("DW-<n> <criterion> · check:", self.sec)

    def test_nothing_posted_before_the_answers_and_headless_stops(self):
        self.assertIn("Nothing is posted, filed or created until the author has answered all four", self.flat)
        self.assertIn("`Decisions` block", self.sec)

    def test_goal_over_the_sessions_read_is_ledgered(self):
        self.assertIn("ledgered in § After the answer's grammar", self.flat)
        self.assertIn("naming the session's read as the rejected option", self.flat)

    def test_skip_is_haiku_only(self):
        self.assertIn("`Interview: SKIP (<reason>)`", self.sec)
        self.assertIn("A `haiku`-sized ticket", self.sec)
        self.assertIn("A `sonnet` or `opus` ticket is always interviewed and never carries the line", self.flat)

    def test_listed_among_the_callers(self):
        self.assertIn("`ticket-open` and `ticket-carousel` (the goal interview above)", section(self.doc, "Who calls this"))


class TicketOpenAsks(unittest.TestCase):
    def setUp(self):
        self.body = squeeze(KIT / "skills" / "ticket-open" / "SKILL.md")

    def test_step_1b_points_at_the_spec_and_names_the_four(self):
        self.assertIn("1b. **Interview the author** — `docs/carousel.md` § Goal interview", self.body)
        self.assertIn("(Goal · Out of scope · Done when · First slice)", self.body)

    def test_block_gains_the_out_of_scope_line_and_plan_step_one(self):
        self.assertIn("**Out of scope** —", self.body)
        self.assertIn("`<step> — verified by <check>`", self.body)

    def test_ledger_nothing_posted_headless_and_skip(self):
        self.assertIn("is ledgered (§ After the answer)", self.body)
        self.assertIn("Nothing is posted until all four are answered", self.body)
        self.assertIn("print the `Decisions` block and stop", self.body)
        self.assertIn("Only a `haiku` ticket may write `Interview: SKIP (<reason>)`", self.body)

    def test_step_1b_sits_between_placement_and_link(self):
        t = (KIT / "skills" / "ticket-open" / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(t.index("1. **Create it placed**"), t.index("1b. **Interview"))
        self.assertLess(t.index("1b. **Interview"), t.index("2. **Link it**"))


class TicketCarouselInterviews(unittest.TestCase):
    def setUp(self):
        text = (KIT / "skills" / "ticket-carousel" / "SKILL.md").read_text(encoding="utf-8")
        self.draft = " ".join(section(text, "Draft").split())
        self.headless = " ".join(section(text, "No tool — headless or scheduled runs").split())

    def test_draft_runs_the_same_interview_per_candidate(self):
        self.assertIn("`ticket-open`'s step 1b", self.draft)
        self.assertIn("docs/carousel.md` § Goal interview", self.draft)

    def test_batched_where_candidates_share_a_goal(self):
        self.assertIn("share a goal ask Goal and `Out of scope` once for the group", self.draft)

    def test_skip_only_for_haiku(self):
        self.assertIn("`haiku`-sized candidate writes `Interview: SKIP", self.draft)

    def test_headless_decisions_carry_the_questions(self):
        self.assertIn("four interview questions", self.headless)


class IssueTemplate(unittest.TestCase):
    def test_template_has_the_out_of_scope_line(self):
        t = (KIT / ".github" / "ISSUE_TEMPLATE" / "change.md").read_text(encoding="utf-8")
        self.assertRegex(t, r"(?m)^\*\*Out of scope\*\* — ")

    def test_template_done_when_still_parses_with_the_id_grammar(self):
        t = (KIT / ".github" / "ISSUE_TEMPLATE" / "change.md").read_text(encoding="utf-8")
        crits, problems = dw.parse_ticket(t)
        self.assertEqual(problems, [])
        self.assertEqual([c.id for c in crits], ["DW-1"])

    def test_a_block_the_interview_writes_parses(self):
        block = ("## Done when\n\n- [ ] DW-1 the export job batches rows in groups of 500 · check: `make test`\n"
                 "- [ ] DW-2 the nightly run finishes under the target · check: an observation on the next run\n")
        crits, problems = dw.parse_ticket(block)
        self.assertEqual(problems, [])
        self.assertEqual([c.id for c in crits], ["DW-1", "DW-2"])


class Evals(unittest.TestCase):
    def test_each_case_is_a_behaviour_case_with_a_criteria_grader(self):
        for case, skill in CASES.items():
            with self.subTest(case=case):
                d = KIT / "evals" / case
                prompt = (d / "prompt.md").read_text(encoding="utf-8")
                self.assertIn(f"tags: [behaviour, {skill}]", prompt)
                self.assertNotIn("trigger", prompt.split("---")[1])
                self.assertTrue((d / "graders" / "criteria.md").is_file())

    def test_the_cases_pin_the_interview_and_the_skip_rule(self):
        sonnet = (KIT / "evals" / "ticket-open-behaviour-sonnet-no-interview" / "graders" / "criteria.md").read_text()
        self.assertIn("must not write `Interview: SKIP", sonnet)
        haiku = (KIT / "evals" / "ticket-open-behaviour-haiku-skip" / "graders" / "criteria.md").read_text()
        self.assertIn("carries the line `Interview: SKIP (<reason>)`", " ".join(haiku.split()))

    def test_readme_coverage_table_lists_every_case_under_its_skill(self):
        readme = (KIT / "evals" / "README.md").read_text(encoding="utf-8")
        for case, skill in CASES.items():
            with self.subTest(case=case):
                row = next(line for line in readme.splitlines() if line.startswith(f"| `{skill}` |"))
                self.assertIn(f"`{case}`", row)

    def test_readme_total_counts_the_behaviour_directories(self):
        readme = (KIT / "evals" / "README.md").read_text(encoding="utf-8")
        stated = int(re.search(r"\((\d+) total,", readme).group(1))
        actual = sum(1 for p in (KIT / "evals").iterdir() if p.is_dir() and "behaviour" in p.name)
        self.assertEqual(stated, actual)


if __name__ == "__main__":
    unittest.main()
