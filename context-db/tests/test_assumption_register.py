"""The assumption register: `docs/carousel.md` points at `docs/assumptions.md` for the distinction
between a decision and an assumption, `docs/assumptions.md` carries the `Assumed:` grammar, storage,
checkpoints and the 4-line bound, and `pr-open` / `ticket-update` each render their own due lines and
ask them in the carousel. Also locks `evals/README.md`'s hand-maintained behaviour-case total against
the actual `evals/*behaviour*` directory count, so the two never drift apart silently. Stdlib
unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]


class CarouselPointsAtAssumptions(unittest.TestCase):
    """`docs/carousel.md` distinguishes an assumption from a decision in one place and sends the
    reader to `docs/assumptions.md` for the rest, rather than restating the spec."""

    def setUp(self):
        self.body = (KIT / "docs" / "carousel.md").read_text(encoding="utf-8")

    def test_names_standing_go_and_the_doc(self):
        self.assertIn("## Assumptions", self.body)
        self.assertIn("standing-go", self.body)
        self.assertIn("docs/assumptions.md", self.body)

    def test_does_not_restate_the_grammar(self):
        # the grammar line itself lives only in docs/assumptions.md
        self.assertNotIn("revisit: pr-open | ticket-update | session-end", self.body)


class AssumptionsDocSpec(unittest.TestCase):
    """`docs/assumptions.md` carries the grammar, storage, checkpoints and bound the design comment
    on #380 settled on 2026-09-30."""

    def setUp(self):
        self.body = (KIT / "docs" / "assumptions.md").read_text(encoding="utf-8")
        self.squeezed = " ".join(self.body.split())

    def test_grammar_line(self):
        self.assertIn("Assumed: <one line> (revisit: pr-open | ticket-update | session-end)", self.body)

    def test_storage_next_to_open_decisions(self):
        self.assertIn("## Assumptions", self.squeezed)
        self.assertIn("## Open decisions", self.squeezed)

    def test_checkpoints_name_all_three_renderers(self):
        self.assertIn("`pr-open`", self.body)
        self.assertIn("## One honest limit", self.body)
        self.assertIn("`ticket-update`", self.body)
        self.assertIn("DELTA", self.body)
        self.assertIn("session-handoff", self.body)

    def test_bound_is_four_open_lines(self):
        self.assertIn("More than 4 open lines", self.body)
        self.assertIn("should have asked earlier", self.body)


class PrOpenRendersDueAssumedLines(unittest.TestCase):
    def setUp(self):
        self.body = (KIT / "skills" / "pr-open" / "SKILL.md").read_text(encoding="utf-8")

    def test_renders_under_one_honest_limit(self):
        self.assertIn("## One honest limit", self.body)
        self.assertIn("Assumed:", self.body)
        self.assertIn("(revisit: pr-open)", self.body)

    def test_asked_in_the_carousel_before_create(self):
        squeezed = " ".join(self.body.split())
        self.assertIn("asked in the carousel before `gh pr create`", squeezed)


class TicketUpdateRendersDueAssumedLines(unittest.TestCase):
    def setUp(self):
        self.body = (KIT / "skills" / "ticket-update" / "SKILL.md").read_text(encoding="utf-8")

    def test_renders_in_the_delta(self):
        self.assertIn("Assumed:", self.body)
        self.assertIn("(revisit: ticket-update)", self.body)
        self.assertIn("DELTA", self.body)

    def test_asked_in_the_carousel(self):
        squeezed = " ".join(self.body.split())
        self.assertIn("asked in the carousel the same turn", squeezed)


class EvalsReadmeBehaviourTotal(unittest.TestCase):
    """`evals/README.md`'s Coverage section quotes the behaviour-case total by hand; this locks it to
    the actual tree so a later add/rename that forgets the README trips a test, not a silent drift."""

    def test_stated_total_matches_the_directory_count(self):
        readme = (KIT / "evals" / "README.md").read_text(encoding="utf-8")
        m = re.search(r"\((\d+) total,\s*counting every `evals/\*behaviour\*` directory", readme)
        self.assertIsNotNone(m, "evals/README.md's Coverage section no longer states the hand-counted total")
        stated = int(m.group(1))
        actual = sum(1 for p in (KIT / "evals").iterdir() if p.is_dir() and "behaviour" in p.name)
        self.assertEqual(stated, actual, "evals/README.md's behaviour-case total is out of sync with evals/")

    def test_the_assumption_near_miss_case_exists(self):
        case = KIT / "evals" / "pr-open-behaviour-missing-assumed-line"
        self.assertTrue((case / "prompt.md").is_file())
        self.assertTrue((case / "graders" / "criteria.md").is_file())
        self.assertIn("pr-open-behaviour-missing-assumed-line", (KIT / "evals" / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
