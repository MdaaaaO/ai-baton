"""eval_check.py (#54): the static gate over evals/ — case format, trigger suites of >= 10 cases with positives and near
misses, notes for skills without a suite — plus the wiring around it (manifest key, `make eval`, the manual workflow).
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT / "context-db" / "bin"))
import eval_check  # noqa: E402

PROMPT = "---\nmax_turns: 6\nallowed_tools: [Read, Skill]\ntags: [trigger, {kind}]\n{extra}---\n\n{body}\n"
FIRED = "---\ntype: tool_used\ntool: Skill\ninput_match: '(?<![\\w-]){skill}(?![\\w-])'\n{bounds}{arm}---\n"
OUTCOME = "---\ntype: llm\n---\n\nThe response does the thing.\n"


class Kit:
    """A throw-away kit root: skills/<name>/SKILL.md plus evals/<case>/ written on demand."""

    def __init__(self, root: Path, skills=("foo",), desc="Does foo. Use when foo happens."):
        self.root = root
        for s in skills:
            (root / "skills" / s).mkdir(parents=True)
            (root / "skills" / s / "SKILL.md").write_text(f"---\nname: {s}\ndescription: {desc}\n---\n\nbody\n", encoding="utf-8")
        (root / "evals").mkdir()

    def case(self, name, skill="foo", kind="positive", *, arm="arm: both\n", bounds=None, extra="", body="Do foo.",
             outcome=True, fired=True, grader_type=None):
        d = self.root / "evals" / name
        (d / "graders").mkdir(parents=True)
        (d / "prompt.md").write_text(PROMPT.format(kind=kind, extra=extra, body=body), encoding="utf-8")
        if bounds is None:
            bounds = "min: 1\n" if kind == "positive" else "min: 0\nmax: 0\n"
        if fired:
            (d / "graders" / "fired.md").write_text(FIRED.format(skill=skill, bounds=bounds, arm=arm), encoding="utf-8")
        if outcome:
            (d / "graders" / "outcome.md").write_text(OUTCOME if grader_type is None else f"---\ntype: {grader_type}\n---\n\nx\n", encoding="utf-8")
        return d

    def suite(self, skill="foo", pos=5, miss=5):
        for i in range(pos):
            self.case(f"{skill}-p{i}", skill, "positive")
        for i in range(miss):
            self.case(f"{skill}-miss-{i}", skill, "near-miss")

    def run(self, require_all=False):
        return eval_check.check(self.root, require_all)


class ThisKit(unittest.TestCase):
    def test_the_committed_suite_passes(self):
        errors, _notes, suites = eval_check.check(KIT)
        self.assertEqual(errors, [])
        for skill in ("pr-open", "ticket-update", "session-handoff"):
            n = suites[skill]
            self.assertGreaterEqual(sum(n.values()), eval_check.MIN_CASES, skill)
            self.assertGreaterEqual(min(n.values()), eval_check.MIN_EACH, skill)

    def test_the_manifest_names_the_eval_dir(self):
        manifest = json.loads((KIT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["experimental"]["evals"], "evals")
        self.assertEqual(eval_check.eval_dir(KIT), KIT / "evals")

    def test_results_stay_ignored(self):
        self.assertIn("evals/results/", (KIT / ".gitignore").read_text(encoding="utf-8").splitlines())


class Suites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit = Kit(Path(self.tmp.name))

    def test_a_full_suite_passes(self):
        self.kit.suite()
        errors, notes, suites = self.kit.run()
        self.assertEqual(errors, [])
        self.assertEqual(suites, {"foo": {"positive": 5, "near-miss": 5}})
        self.assertEqual(notes, [])

    def test_too_few_cases_fail(self):
        self.kit.suite(pos=5, miss=4)
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo: 9 trigger case(s)" in e for e in errors), errors)

    def test_too_few_near_misses_fail(self):
        self.kit.suite(pos=9, miss=1)
        errors, _, _ = self.kit.run()
        self.assertTrue(any("1 near miss" in e for e in errors), errors)

    def test_a_near_miss_that_allows_the_skill_fails(self):
        self.kit.suite()
        self.kit.case("foo-miss-loose", kind="near-miss", bounds="min: 0\n")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo-miss-loose/graders/fired.md: a near miss must forbid" in e for e in errors), errors)

    def test_a_positive_that_forbids_the_skill_fails(self):
        self.kit.suite()
        self.kit.case("foo-backwards", kind="positive", bounds="min: 0\nmax: 0\n")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("a positive case must require the skill" in e for e in errors), errors)

    def test_a_skill_grader_without_arm_both_fails(self):
        self.kit.suite()
        self.kit.case("foo-display-only", arm="")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("arm: both" in e for e in errors), errors)

    def test_a_skill_grader_for_another_skill_does_not_count(self):
        self.kit.suite()
        self.kit.case("foo-wrong-skill", skill="bar")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo-wrong-skill: no `tool_used` grader" in e for e in errors), errors)

    def test_a_trigger_case_needs_an_outcome_grader(self):
        self.kit.suite()
        self.kit.case("foo-bare", outcome=False)
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo-bare: a trigger case also needs an outcome grader" in e for e in errors), errors)

    def test_a_trigger_case_must_be_named_after_a_skill(self):
        self.kit.case("nosuch-case")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("nosuch-case: a trigger case must be named" in e for e in errors), errors)

    def test_a_trigger_case_needs_exactly_one_kind(self):
        self.kit.case("foo-both", kind="positive, near-miss")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("tagged exactly one of" in e for e in errors), errors)


class CaseFormat(unittest.TestCase):
    """What the runner would refuse to load fails here, before anyone spends a token."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit = Kit(Path(self.tmp.name))
        self.kit.suite()

    def test_unknown_prompt_key_fails(self):
        self.kit.case("foo-typo", extra="max_turn: 3\n")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo-typo/prompt.md: frontmatter key(s) the runner rejects: max_turn" in e for e in errors), errors)

    def test_empty_prompt_fails(self):
        self.kit.case("foo-empty", body="")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo-empty/prompt.md: empty prompt body" in e for e in errors), errors)

    def test_unknown_grader_type_fails(self):
        self.kit.case("foo-grader", grader_type="judge")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo-grader/graders/outcome.md: type 'judge'" in e for e in errors), errors)

    def test_no_prompt_fails_and_results_are_skipped(self):
        (self.kit.root / "evals" / "orphan").mkdir()
        (self.kit.root / "evals" / "results" / "2026").mkdir(parents=True)
        errors, _, _ = self.kit.run()
        self.assertEqual(errors, ["orphan: no prompt.md (or case.yaml)"])

    def test_a_case_without_the_trigger_tag_is_another_case(self):
        d = self.kit.root / "evals" / "review-clean"
        (d / "graders").mkdir(parents=True)
        (d / "prompt.md").write_text("---\nmax_turns: 8\n---\n\nReview this.\n", encoding="utf-8")
        (d / "graders" / "criteria.md").write_text(OUTCOME, encoding="utf-8")
        errors, notes, _ = self.kit.run()
        self.assertEqual(errors, [])
        self.assertTrue(any("other cases" in n and "review-clean" in n for n in notes), notes)


class Coverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_skills_without_a_suite_are_a_note_unless_required(self):
        kit = Kit(Path(self.tmp.name), skills=("foo", "bar"))
        kit.suite()
        errors, notes, _ = kit.run()
        self.assertEqual(errors, [])
        self.assertTrue(any("no trigger suite yet (1): bar" in n for n in notes), notes)
        errors, _, _ = kit.run(require_all=True)
        self.assertTrue(any("no trigger suite yet (1): bar" in e for e in errors), errors)

    def test_a_description_without_use_when_is_a_note(self):
        kit = Kit(Path(self.tmp.name), desc="Does foo. Invoke when foo happens.")
        kit.suite()
        errors, notes, _ = kit.run()
        self.assertEqual(errors, [])
        self.assertTrue(any('without a "Use when" phrase (1): foo' in n for n in notes), notes)

    def test_use_whenever_counts(self):
        kit = Kit(Path(self.tmp.name), desc="Does foo. Use whenever foo happens.")
        kit.suite()
        self.assertEqual(kit.run(require_all=True)[0], [])

    def test_the_longest_skill_prefix_wins(self):
        self.assertEqual(eval_check.skill_of("session-register-start", ["session", "session-register"]), "session-register")
        self.assertIsNone(eval_check.skill_of("kit-review-clean", ["kit-health", "pr-review"]))

    def test_the_manifest_can_move_the_eval_dir(self):
        kit = Kit(Path(self.tmp.name))
        (kit.root / ".claude-plugin").mkdir()
        (kit.root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"experimental": {"evals": "quality"}}), encoding="utf-8")
        (kit.root / "quality").mkdir()
        kit.suite()  # written under evals/, which the manifest no longer names
        errors, notes, suites = kit.run()
        self.assertEqual(suites, {})
        self.assertTrue(any("no trigger suite yet (1): foo" in n for n in notes), notes)

    def test_cli_exit_codes(self):
        kit = Kit(Path(self.tmp.name))
        kit.suite(pos=2, miss=2)
        p = subprocess.run([sys.executable, str(KIT / "context-db" / "bin" / "eval_check.py"), "--kit", str(kit.root)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("eval-check: FAIL — foo: 4 trigger case(s)", p.stdout)
        kit.suite(pos=0, miss=0)
        for i in range(2, 5):
            kit.case(f"foo-p{i}")
            kit.case(f"foo-miss-{i}", kind="near-miss")
        p = subprocess.run([sys.executable, str(KIT / "context-db" / "bin" / "eval_check.py"), "--kit", str(kit.root)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("eval-check: OK — trigger suites (positive+near miss): foo 5+5", p.stdout)


class Wiring(unittest.TestCase):
    def make_n(self, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if k not in ("CONTEXT", "CONTEXT_ROOT")}
        p = subprocess.run(["make", "-C", str(KIT / "context-db"), "-n", *args], env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_make_target_builds_the_runner_command(self):
        out = self.make_n("eval", "SKILL=pr-open", "MODEL=some-model", "RUNS=1", "TRUST=1")
        cmd = " ".join(out.split())
        for part in ("claude plugin eval .", "--no-publish", "--ablation none", "--case 'pr-open-*'",
                     "--model 'some-model'", "--runs 1", "--trust-plugin"):
            self.assertIn(part, cmd)

    def test_make_target_without_arguments_runs_every_case(self):
        cmd = " ".join(self.make_n("eval").split())
        self.assertIn("claude plugin eval . --no-publish --ablation none", cmd)
        for flag in ("--case", "--model", "--trust-plugin", "--runs"):
            self.assertNotIn(flag, cmd)

    def test_ci_runs_the_static_check(self):
        mk = (KIT / "context-db" / "Makefile").read_text(encoding="utf-8")
        self.assertRegex(mk, r"(?m)^ci:.*\beval-check\b")
        self.assertIn("make -C .claude/context-db eval-check", (KIT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

    def test_the_token_spending_workflow_is_manual_only(self):
        text = (KIT / ".github" / "workflows" / "evals.yml").read_text(encoding="utf-8")
        triggers = re.split(r"\n(?=\S)", text.split("\non:", 1)[1], maxsplit=1)[0]  # the `on:` block, to the next top-level key
        self.assertEqual(re.findall(r"(?m)^  ([a-z_]+):", triggers), ["workflow_dispatch"])
        for name in ("skill", "models"):
            self.assertRegex(triggers, rf"(?m)^      {name}:")
        self.assertIn("secrets.CLAUDE_CODE_OAUTH_TOKEN", text)
        # inputs reach the scripts through env only — never spliced into a `run:` body, where they could inject shell
        for line in text.splitlines():
            if "${{ inputs." in line:
                self.assertRegex(line, r"^\s+[A-Z_]+: \$\{\{ inputs\.\w+ \}\}$", line)


if __name__ == "__main__":
    unittest.main()
