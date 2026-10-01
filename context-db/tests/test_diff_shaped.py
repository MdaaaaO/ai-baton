"""skills/ticket-update/SKILL.md and skills/session-handoff/SKILL.md: a Pivot comment and a handoff
prompt are diff-shaped, never a restatement. Locks the exact Was/Now/Why labels `ticket-update`'s own
prose promises for every Pivot, sketch or not, and the `### Since last handoff` heading (with its
`first handoff` fallback for an initiative's first session) `session-handoff`'s own prose promises for
the `## Next session` prompt. Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations

import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]


class TicketUpdatePivotGrammar(unittest.TestCase):
    """The Pivot section is a delta — `**Was** — … / **Now** — … / **Why** — …` — on every ticket,
    sketch or not, and *Why* points at the recorded-decision ledger line when there is one."""

    def setUp(self):
        self.body = (KIT / "skills" / "ticket-update" / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_names_the_three_pivot_labels(self):
        self.assertIn("**Was**", self.body)
        self.assertIn("**Now**", self.body)
        self.assertIn("**Why**", self.body)

    def test_skill_applies_the_grammar_to_every_ticket(self):
        squeezed = " ".join(self.body.split())
        self.assertIn("on every ticket, sketch or not", squeezed)

    def test_skill_cites_the_ledger_line(self):
        self.assertIn("docs/carousel.md", self.body)
        squeezed = " ".join(self.body.split())
        self.assertIn("ledger line", squeezed)
        self.assertIn("recorded decision", squeezed)


class SessionHandoffSinceLastHandoff(unittest.TestCase):
    """The `## Next session` prompt opens with `### Since last handoff`, built from the activity list
    and the ledger lines; an initiative's first session writes `first handoff` instead."""

    def setUp(self):
        self.body = (KIT / "skills" / "session-handoff" / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_names_the_since_last_handoff_heading(self):
        self.assertIn("### Since last handoff", self.body)

    def test_skill_names_the_first_handoff_fallback(self):
        self.assertIn("first handoff", self.body)

    def test_skill_names_what_the_block_carries(self):
        squeezed = " ".join(self.body.split())
        self.assertIn("## Assumptions", squeezed)
        self.assertIn("## Architecture", squeezed)
        self.assertIn("when present", squeezed)
        self.assertIn("when the doc has one", squeezed)

    def test_skill_builds_the_block_with_no_extra_turn(self):
        squeezed = " ".join(self.body.split())
        self.assertIn("make session-activity", squeezed)
        self.assertIn("no extra turn", squeezed)


if __name__ == "__main__":
    unittest.main()
