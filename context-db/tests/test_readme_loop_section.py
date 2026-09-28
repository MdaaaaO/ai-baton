"""README.md must pitch the kit through the loop it runs: one section, near the top — before the
"Why a kit" comparison table — naming the skills that carry an issue from open to merge to the next retro.
Every skill it names must be a real skill directory, so the section can never drift from what the kit ships."""
from __future__ import annotations
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
HEADING = re.compile(r"^##\s+(.+)$", re.MULTILINE)

# The loop's steps that are real skills (docs/delegation.md § The worker brief and § Sizing cover the rest:
# a worker sized to the ticket, the review verdict, auto-merge).
LOOP_SKILLS = ["ticket-open", "ticket-pickup", "pr-open", "pr-watch", "session-handoff", "session-retro"]


def sections(readme: str) -> list[tuple[str, int, int]]:
    """[(title, start, end)] for every H2 section, in file order."""
    heads = [(m.group(1).strip(), m.start()) for m in HEADING.finditer(readme)]
    out = []
    for i, (title, start) in enumerate(heads):
        end = heads[i + 1][1] if i + 1 < len(heads) else len(readme)
        out.append((title, start, end))
    return out


class ReadmeLoopSection(unittest.TestCase):
    def setUp(self):
        self.readme = (KIT / "README.md").read_text(encoding="utf-8")
        self.sections = sections(self.readme)
        self.titles = [t for t, _, _ in self.sections]

    def test_the_loop_section_exists(self):
        self.assertIn("The loop", self.titles, "README.md has no '## The loop' section")

    def test_the_loop_is_near_the_top(self):
        # "near the top": before the feature-list framing, not buried after it.
        self.assertIn("Why a kit", self.titles)
        self.assertLess(self.titles.index("The loop"), self.titles.index("Why a kit"),
                         "'The loop' should read before 'Why a kit', not after")

    def test_the_loop_names_every_step_of_the_issue_to_merge_cycle(self):
        _, start, end = next(s for s in self.sections if s[0] == "The loop")
        body = self.readme[start:end]
        missing = [s for s in LOOP_SKILLS if s not in body]
        self.assertEqual(missing, [], f"'The loop' section never names: {missing}")

    def test_every_loop_skill_it_names_actually_ships(self):
        # the section must only describe what exists: every LOOP_SKILLS name it uses has to be a real skill
        # directory, so a future rename that forgets the README trips this test.
        shipped = {p.name for p in (KIT / "skills").iterdir() if (p / "SKILL.md").exists()}
        missing = [s for s in LOOP_SKILLS if s not in shipped]
        self.assertEqual(missing, [], f"LOOP_SKILLS names a skill the kit no longer ships: {missing}")


if __name__ == "__main__":
    unittest.main()
