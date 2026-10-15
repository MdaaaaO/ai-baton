"""agents/review-runner.md must state the runner's bundle-vs-live-read contract once, in a named section, so a
reader cannot land on the flat "the runner works from the bundle only" claim in one spot and a live `gh api`
instruction a few lines later with no reasoned exception tying the two together. Since #48 the runner steps
live only there: skills/pr-review/SKILL.md and reference/runner.md point at that section and repeat none of
it. Text-shape checks only (no model judgement). Run: make -C .claude/context-db test."""
from __future__ import annotations
import re
import sys
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SKILL = KIT / "skills" / "pr-review" / "SKILL.md"
RUNNER = KIT / "agents" / "review-runner.md"
RUNNER_REF = KIT / "skills" / "pr-review" / "reference" / "runner.md"
AUTO_RUNNER = KIT / "agents" / "auto-runner.md"
GH_ENV_LINE = 'eval "$(python3 $BATON/context-db/bin/kit_profile.py gh-env)"'


def norm(text: str) -> str:
    """Collapse whitespace (including line wraps) so a search string does not depend on exactly where the
    prose happens to wrap."""
    return re.sub(r"\s+", " ", text)


class BundleContract(unittest.TestCase):
    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")
        self.runner = RUNNER.read_text(encoding="utf-8")
        self.ref = RUNNER_REF.read_text(encoding="utf-8")

    def test_runner_names_one_contract_section_with_its_exceptions(self):
        self.assertIn("### Contract", self.runner,
                      "agents/review-runner.md: no named § Contract section stating the "
                      "bundle-vs-live-read rule once")
        contract = self.runner.split("### Contract", 1)[1].split("**Do exactly this**", 1)[0]
        for step in ("step 2", "step 4", "step 5"):
            self.assertIn(step, contract, f"§ Contract does not name its {step} exception")

    def test_the_skill_points_at_the_runner_contract_and_repeats_no_step(self):
        self.assertNotIn("### Contract", self.skill,
                         "skills/pr-review/SKILL.md carries a second copy of the bundle-vs-live contract")
        flat = norm(self.skill)
        self.assertIn("agents/review-runner.md` § Contract", flat,
                      "skills/pr-review/SKILL.md does not point at the runner's § Contract")
        for step_text in ("fetch-context.sh", "## 1. Snapshot", "## 2. Load", "## 3. Review pass",
                          "## 4. Verify", "## 4b."):
            self.assertNotIn(step_text, self.skill, f"SKILL.md still writes out a runner step: {step_text}")

    def test_cost_model_table_points_to_the_contract_instead_of_repeating_the_blanket_claim(self):
        row = next(line for line in self.ref.splitlines() if line.startswith("| 1–4 snapshot"))
        self.assertIn("§ Contract", row,
                      "runner.md's cost-model table does not point readers at § Contract")
        self.assertNotIn("no `gh api contents`, `git show` or CI-log polling", row,
                          "the table repeats the old unconditional claim the exceptions elsewhere contradict")

    def test_step_2_review_rules_read_is_bundle_first_with_a_named_live_exception(self):
        flat = norm(self.runner)
        self.assertIn("`head/<path>` when the PR touched the file, else one live `gh api contents` read", flat,
                      "agents/review-runner.md step 2 does not name the live-read exception for an "
                      "untouched review-rules file")
        self.assertNotIn("(live via `gh api contents", flat,
                          "step 2 still states the old unconditional live-read claim")


class GhEnvBeforeLiveReads(unittest.TestCase):
    """A sandbox that needs `github.sandbox_token_prefix` (kit_profile.py gh-env) must not have its live
    `gh` reads fail silently: both the agent definition the runner actually follows and the prompt template
    the main session spawns it with carry the same setup line `fetch-context.sh` already runs, and an auth
    failure on one of those reads is a `NEEDS` hand-back — never folded into an `unverified` trap."""

    def test_gh_env_is_prefixed_per_command_not_run_once(self):
        # an export in one Bash call does not reach the next: "once, before the reads" leaves them unauthenticated
        for rel in ("agents/review-runner.md", "agents/auto-runner.md", "skills/pr-review/reference/runner.md"):
            text = (KIT / rel).read_text(encoding="utf-8")
            self.assertIn("does not survive into the next Bash call", text, rel)
            self.assertNotRegex(text, r"gh-env[^\n]*\n?[^\n]*\bonce, before", rel)

    def test_the_auth_line_is_a_named_reason_not_a_fact_handback(self):
        # a line matching kb.NEEDS_HANDBACK is routed to env-init as a missing fact; an auth failure is not one
        sys.path.insert(0, str(KIT / "context-db" / "bin"))
        import kb
        self.assertIsNone(kb.NEEDS_HANDBACK.fullmatch("NEEDS gh reauth"))
        self.assertIsNotNone(kb.NEEDS_HANDBACK.fullmatch("NEEDS github.person octo"))


    def test_review_runner_agent_runs_gh_env_before_its_live_reads(self):
        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn(GH_ENV_LINE, runner,
                      "agents/review-runner.md does not eval kit_profile.py gh-env before its live `gh` reads")
        self.assertIn("NEEDS gh reauth", runner,
                      "agents/review-runner.md does not turn a live-read auth failure into a NEEDS hand-back")

    def test_prompt_template_carries_the_same_gh_env_line(self):
        ref = RUNNER_REF.read_text(encoding="utf-8")
        self.assertIn(GH_ENV_LINE, ref,
                      "skills/pr-review/reference/runner.md § Prompt template does not carry the gh-env line")
        self.assertIn("NEEDS gh reauth", ref,
                      "skills/pr-review/reference/runner.md does not document the github.auth NEEDS hand-back")

    def test_auto_runner_agent_runs_gh_env_before_its_live_reads(self):
        auto = AUTO_RUNNER.read_text(encoding="utf-8")
        self.assertIn(GH_ENV_LINE, auto,
                      "agents/auto-runner.md does not eval kit_profile.py gh-env before its live `gh api` reads")
        self.assertIn("NEEDS gh reauth", auto,
                      "agents/auto-runner.md does not turn a live-read auth failure into a NEEDS hand-back")


if __name__ == "__main__":
    unittest.main()
