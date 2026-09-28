"""skills/pr-review/SKILL.md and agents/review-runner.md must state the runner's bundle-vs-live-read
contract once, in a named section, so a reader cannot land on the flat "the runner works from the bundle
only" claim in one spot and a live `gh api` instruction a few lines later with no reasoned exception tying
the two together. Text-shape checks only (no model judgement). Run: make -C .claude/context-db test."""
from __future__ import annotations
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SKILL = KIT / "skills" / "pr-review" / "SKILL.md"
RUNNER = KIT / "agents" / "review-runner.md"


def norm(text: str) -> str:
    """Collapse whitespace (including line wraps) so a search string does not depend on exactly where the
    prose happens to wrap."""
    return re.sub(r"\s+", " ", text)


class BundleContract(unittest.TestCase):
    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")
        self.runner = RUNNER.read_text(encoding="utf-8")

    def test_skill_names_one_contract_section_with_its_exceptions(self):
        self.assertIn("### Contract", self.skill,
                      "skills/pr-review/SKILL.md: no named § Contract section stating the "
                      "bundle-vs-live-read rule once")
        contract = self.skill.split("### Contract", 1)[1].split("\n## ", 1)[0]
        for step in ("step 2.3", "step 4"):
            self.assertIn(step, contract, f"§ Contract does not name its {step} exception")

    def test_cost_model_table_points_to_the_contract_instead_of_repeating_the_blanket_claim(self):
        row = next(line for line in self.skill.splitlines() if line.startswith("| 1–4 snapshot"))
        self.assertIn("§ Contract", row,
                      "the cost-model table's runner cell does not point readers at § Contract")
        self.assertNotIn("no `gh api contents`, `git show` or CI-log polling", row,
                          "the table repeats the old unconditional claim the exceptions elsewhere contradict")

    def test_step_2_review_rules_read_is_bundle_first_with_a_named_live_exception(self):
        flat = norm(self.skill)
        self.assertIn("the bundle's `head/<path>` when the PR touched the file, else one live exception", flat,
                      "step 2's repo-review-rules bullet still claims an unconditional live `gh api` read "
                      "instead of the bundle's own head/<path> for a touched file")
        self.assertNotIn("(live via `gh api contents", flat,
                          "step 2 still states the old unconditional live-read claim")

    def test_runner_names_the_same_live_exception_and_points_back_at_the_skill_contract(self):
        flat = norm(self.runner)
        self.assertIn("else one live `gh api contents` read", flat,
                      "agents/review-runner.md step 2 does not name the live-read exception for an "
                      "untouched review-rules file")
        self.assertIn("pr-review/SKILL.md", flat)
        self.assertIn("§ Contract", flat,
                      "agents/review-runner.md does not point back at the skill's § Contract")


if __name__ == "__main__":
    unittest.main()
