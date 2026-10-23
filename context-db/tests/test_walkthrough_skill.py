"""The walkthrough skill's contract, pinned: the order of the steps, the one-carousel check, the re-open rule, and
what may be recorded; session-handoff keeps the section it records into; the templates explain who fills it."""
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]


def squeeze(text):
    return " ".join(text.split())


class WalkthroughSkill(unittest.TestCase):
    def setUp(self):
        self.text = (KIT / "skills" / "walkthrough" / "SKILL.md").read_text(encoding="utf-8")
        self.body = squeeze(self.text)

    def test_frontmatter_is_a_versioned_user_invocable_skill(self):
        head = self.text.split("---", 2)[1]
        self.assertIn("name: walkthrough", head)
        self.assertIn("user-invocable: true", head)
        self.assertRegex(head, r'version: "1"')
        description = re.search(r'^description: "(.*)"$', head, re.M).group(1)
        self.assertLessEqual(len(description.encode("utf-8")), 250)

    def test_steps_run_purpose_then_how_it_runs_then_where_it_breaks(self):
        order = [self.body.index(s) for s in ("**Purpose**", "**How it runs**", "**Where it breaks**")]
        self.assertEqual(order, sorted(order))
        self.assertIn("one per turn", self.body)

    def test_reads_the_artefacts_before_the_diff(self):
        for section in ("## Diagrams", "## For the operator", "## Verified"):
            self.assertIn(section, self.body)
        self.assertIn("never the raw diff first", self.body)

    def test_three_questions_in_one_carousel_with_a_headless_fallback(self):
        self.assertIn("exactly three questions", self.body)
        for kind in ("behaviour", "failure", "see it"):
            self.assertIn(kind, self.body)
        self.assertIn("3 options", self.body)
        self.assertIn("no option is marked `(Recommended)`", self.body)
        self.assertIn("`Decisions` block", self.body)
        self.assertIn("docs/carousel.md", self.body)

    def test_a_miss_reopens_once_and_a_second_miss_parks(self):
        self.assertIn("different explanation", self.body)
        self.assertIn("park the step as **open**", self.body)
        self.assertIn("never a third loop", self.body)
        self.assertIn("A wrong answer is never written anywhere", self.body)

    def test_records_only_confirmed_dated_lines_through_the_ctx_tools(self):
        self.assertIn("- <date> — <line> (walkthrough <target>)", self.body)
        self.assertIn("At most 10 lines", self.body)
        self.assertIn("replaced", self.body)
        self.assertIn("walkthrough <target>: 3/3 confirmed, 1 re-opened (<step>)", self.body)
        for tool in ("ctx_str_replace", "ctx_insert", "ctx_log"):
            self.assertIn(tool, self.body)
        self.assertIn("never `Write`/`Edit`", self.body)


class PlainWordsKeptCurrent(unittest.TestCase):
    def test_session_handoff_keeps_the_section_or_logs_why_not(self):
        body = squeeze((KIT / "skills" / "session-handoff" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("`## In plain words` section", body)
        self.assertIn("Plain words unchanged: <reason>", body)
        self.assertIn("Never add or reword a line", body)

    def test_templates_say_only_the_operator_confirms_a_line(self):
        for name in ("epic", "repo"):
            text = (KIT / "context-db" / "_templates" / f"{name}.md").read_text(encoding="utf-8")
            section = text.split("## In plain words", 1)[1].split("\n## ", 1)[0]
            self.assertIn("confirmed", section, name)
            self.assertIn("at most 10", section, name)


if __name__ == "__main__":
    unittest.main()
