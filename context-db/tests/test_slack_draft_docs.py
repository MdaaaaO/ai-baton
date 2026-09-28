"""skills/slack-draft/SKILL.md states one model of how a Slack draft behaves, not two contradictory ones:
§1/§3/§4 build the "one draft per channel, `draft_already_exists` means update it" model throughout the
body; the closing "Related" line must agree, not assert "drafts add rather than replace" (which implies
more than one can coexist). Text-shape check only, no model judgement. Run: make -C .claude/context-db
test."""
from __future__ import annotations
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SKILL = KIT / "skills" / "slack-draft" / "SKILL.md"


class OneDraftModel(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def test_the_closing_related_line_does_not_contradict_the_one_draft_per_channel_model(self):
        self.assertNotIn("add rather than replace", self.text,
                          "the closing Related line still asserts drafts add rather than replace, which "
                          "contradicts §1's 'one attached draft per channel' / §3's "
                          "`draft_already_exists` → update-it model")

    def test_one_draft_per_channel_is_still_the_stated_model(self):
        self.assertIn("One attached draft per channel", self.text)
        self.assertIn("draft_already_exists", self.text)


if __name__ == "__main__":
    unittest.main()
