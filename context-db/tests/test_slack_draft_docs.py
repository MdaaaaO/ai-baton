"""skills/slack-draft/SKILL.md states one model of how a Slack draft behaves, not two contradictory ones:
§1/§3/§4 build the "one draft per conversation (top level or one thread), `attached_draft_exists` means update
it" model throughout the
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

    def test_the_closing_related_line_does_not_contradict_the_one_draft_per_conversation_model(self):
        self.assertNotIn("add rather than replace", self.text,
                          "the closing Related line still asserts drafts add rather than replace, which "
                          "contradicts §1's 'one attached draft per conversation' / §3's "
                          "`attached_draft_exists` → update-it model")

    def test_one_draft_per_conversation_is_the_stated_model(self):
        self.assertIn("One attached draft per conversation", self.text)
        self.assertIn("attached_draft_exists", self.text)

    def test_the_slot_is_not_the_channel_and_the_error_code_is_the_connectors(self):
        # #609: drafts in different threads of one channel coexist; the connector's code is attached_draft_exists
        self.assertNotIn("draft_already_exists", self.text.replace("attached_draft_exists", ""))
        self.assertNotIn("One attached draft per channel", self.text)
        self.assertNotIn("counts against the same channel slot", self.text)


if __name__ == "__main__":
    unittest.main()
