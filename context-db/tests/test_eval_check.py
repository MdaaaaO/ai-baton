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

    def test_every_skill_has_a_trigger_suite(self):
        # a follow-up on the eval suite's own coverage work: most skills shipped with no trigger suite at all, so a
        # description edit that broke a skill's trigger went unnoticed by anyone. This pins that every skill listed
        # under skills/ now has one, meeting the same >= MIN_CASES / >= MIN_EACH bar the suites eval-check already
        # knew about (not just a handful of them).
        errors, notes, suites = eval_check.check(KIT)
        self.assertEqual(errors, [], errors)
        self.assertFalse(any(n.startswith("no trigger suite yet") for n in notes), notes)
        skills = eval_check.skill_names(KIT)
        self.assertEqual(sorted(suites), skills)
        for skill in skills:
            n = suites[skill]
            self.assertGreaterEqual(sum(n.values()), eval_check.MIN_CASES, skill)
            self.assertGreaterEqual(min(n.values()), eval_check.MIN_EACH, skill)

    def test_require_all_passes_on_the_real_kit(self):
        # the gate ci.yml now runs on every PR (REQUIRE_ALL=1): a trigger suite for every skill (pinned above) and a
        # recognized trigger phrase in every description. Widening the phrase check (this follow-up) is what makes
        # the second half true — on the old, narrower regex this failed on most of the kit's own descriptions.
        errors, _notes, _suites = eval_check.check(KIT, require_all=True)
        self.assertEqual(errors, [], errors)

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

    def test_an_unbounded_input_match_is_rejected(self):
        # #153: `skill in input_match` let a pattern that also fires for foo-helper / myfoo count as "names foo"
        self.kit.suite()
        d = self.kit.case("foo-loose")
        (d / "graders" / "fired.md").write_text("---\ntype: tool_used\ntool: Skill\ninput_match: 'foo'\nmin: 1\narm: both\n---\n",
                                               encoding="utf-8")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo-loose/graders/fired.md: `input_match` 'foo' also matches 'foo-helper'" in e for e in errors), errors)

    def test_an_input_match_naming_a_longer_skill_does_not_count(self):
        self.kit.suite()
        d = self.kit.case("foo-longer")
        (d / "graders" / "fired.md").write_text(
            "---\ntype: tool_used\ntool: Skill\ninput_match: '(?<![\\w-])foo-helper(?![\\w-])'\nmin: 1\narm: both\n---\n", encoding="utf-8")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("does not match the skill name 'foo'" in e for e in errors), errors)

    def test_an_invalid_input_match_and_a_non_integer_key_are_named(self):
        self.kit.suite()
        d = self.kit.case("foo-bad", extra="max_turns: many\n")
        (d / "graders" / "fired.md").write_text("---\ntype: tool_used\ntool: Skill\ninput_match: '(foo'\nmin: 1\narm: both\n---\n",
                                               encoding="utf-8")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo-bad/graders/fired.md: `input_match` is not a valid regex" in e for e in errors), errors)
        self.assertTrue(any("foo-bad/prompt.md: `max_turns` must be a whole number" in e for e in errors), errors)

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

    def test_a_block_style_allowed_tools_is_not_a_list(self):
        # frontmatter.parse_lines never produces a Python list (str/dict only); a block mapping under
        # `allowed_tools:` reads back as `{}`, which must still fail — pins the surviving `startswith("[")` check
        # now that the dead `isinstance(..., list)` branch is gone.
        d = self.kit.case("foo-block")
        (d / "prompt.md").write_text(
            "---\nmax_turns: 6\nallowed_tools:\n  Read: yes\ntags: [trigger, positive]\n---\n\nDo foo.\n",
            encoding="utf-8")
        (d / "graders" / "fired.md").write_text(
            "---\ntype: tool_used\ntool: Skill\ninput_match: '(?<![\\w-])foo(?![\\w-])'\nmin: 1\narm: both\n---\n",
            encoding="utf-8")
        errors, _, _ = self.kit.run()
        self.assertTrue(any("foo-block/prompt.md: `allowed_tools` must be a list" in e for e in errors), errors)

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

    def test_a_description_without_any_trigger_phrase_is_a_note(self):
        kit = Kit(Path(self.tmp.name), desc="Does foo, quietly, without saying why.")
        kit.suite()
        errors, notes, _ = kit.run()
        self.assertEqual(errors, [])
        self.assertTrue(any("without a recognized trigger phrase (1): foo" in n for n in notes), notes)

    def test_a_when_only_in_the_not_for_clause_does_not_count(self):
        # the trigger clause is the text BEFORE "Not for …" (docs/authoring.md's `<what>. Use when
        # <situations>. Not for <near misses>.` shape); a near miss can itself be conditional ("Not for … when
        # …"), and that "when" names what the unit does NOT do, not when it fires.
        kit = Kit(Path(self.tmp.name), desc="Does foo. Not for bar, even when bar looks like foo.")
        kit.suite()
        errors, notes, _ = kit.run()
        self.assertEqual(errors, [])
        self.assertTrue(any("without a recognized trigger phrase (1): foo" in n for n in notes), notes)

    def test_use_whenever_counts(self):
        kit = Kit(Path(self.tmp.name), desc="Does foo. Use whenever foo happens.")
        kit.suite()
        self.assertEqual(kit.run(require_all=True)[0], [])

    def test_invoke_when_counts(self):
        # a follow-up on the eval suite's own coverage work: most of the kit's forked or background skills say
        # "Invoke when/as/at/for …", not "Use when …" — the old regex matched only the latter, so requiring the
        # full gate failed most of the kit's own descriptions even though kit_verify's own (more lenient) shape
        # check already accepted them.
        kit = Kit(Path(self.tmp.name), desc="Does foo. Invoke when foo happens.")
        kit.suite()
        self.assertEqual(kit.run(require_all=True)[0], [])

    def test_invoke_as_at_for_count(self):
        for phrase in ('Invoke as "do foo".', "Invoke at the start of foo.", "Invoke for every foo."):
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as d:
                kit = Kit(Path(d), desc=f"Does foo. {phrase}")
                kit.suite()
                self.assertEqual(kit.run(require_all=True)[0], [])

    def test_arm_with_counts(self):
        kit = Kit(Path(self.tmp.name), desc="Does foo. Arm with `/loop 5m /foo`.")
        kit.suite()
        self.assertEqual(kit.run(require_all=True)[0], [])

    def test_use_once_and_use_for_count(self):
        for phrase in ("Use once, right after install.", "Use for every foo you see."):
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as d:
                kit = Kit(Path(d), desc=f"Does foo. {phrase}")
                kit.suite()
                self.assertEqual(kit.run(require_all=True)[0], [])

    def test_load_before_and_run_after_count(self):
        for phrase in ("Load before non-trivial foo work.", "Run after every foo."):
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as d:
                kit = Kit(Path(d), desc=f"Does foo. {phrase}")
                kit.suite()
                self.assertEqual(kit.run(require_all=True)[0], [])

    def test_for_every_and_for_prs_count(self):
        for phrase in ("For every foo your session owns.", "For PRs the queue surfaces."):
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as d:
                kit = Kit(Path(d), desc=f"Does foo. {phrase}")
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
    def make_n(self, *args: str, extra_env: "dict[str, str] | None" = None) -> str:
        env = {k: v for k, v in os.environ.items() if k not in ("CONTEXT", "CONTEXT_ROOT")}
        env.update(extra_env or {})
        p = subprocess.run(["make", "-C", str(KIT / "context-db"), "-n", *args], env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_make_target_builds_the_runner_command(self):
        out = self.make_n("eval", "SKILL=pr-open", "MODEL=some-model", "RUNS=1", "JOBS=2", "TRUST=1")
        cmd = " ".join(out.split())
        for part in ("claude plugin eval .", "--no-publish", "--ablation none", "--case 'pr-open-*'",
                     "--model 'some-model'", "--runs 1", "--concurrency 2", "--trust-plugin"):
            self.assertIn(part, cmd)

    def test_make_target_without_arguments_runs_every_case(self):
        # the second recipe line (the per-case Bash-grant loop, #489) always spells out --case/--allow-tools in
        # its own static text, so only the main invocation (everything before it) answers "no scope given"
        out = self.make_n("eval")
        main = out.split("eval_bash_cases.py", 1)[0]
        cmd = " ".join(main.split())
        self.assertIn("claude plugin eval . --no-publish --ablation none", cmd)
        self.assertIn("--model 'sonnet'", cmd)  # MODEL ?= sonnet (#489)
        self.assertIn("--scaffold", cmd)  # SCAFFOLD ?= 1 (#489)
        for flag in ("--case", "--trust-plugin", "--runs", "--concurrency", "--json"):
            self.assertNotIn(flag, cmd)

    def test_make_target_judges_with_sonnet_unless_told_otherwise(self):
        # the CLI's own default judge fails the suite's multi-condition criteria on answers that meet them
        self.assertIn("--judge-model 'sonnet'", " ".join(self.make_n("eval").split()))
        self.assertIn("--judge-model 'some-judge'", " ".join(self.make_n("eval", "JUDGE=some-judge").split()))
        self.assertNotIn("--judge-model", " ".join(self.make_n("eval", "JUDGE=").split()))

    def test_make_target_pins_the_model_to_sonnet_unless_told_otherwise(self):
        # #489: the default (unpinned) model took several turns where sonnet took one and hit a case's
        # max_turns — the model was the variable to fix, not a blanket max_turns raise
        self.assertIn("--model 'sonnet'", " ".join(self.make_n("eval").split()))
        self.assertIn("--model 'some-model'", " ".join(self.make_n("eval", "MODEL=some-model").split()))
        self.assertNotIn("--model", " ".join(self.make_n("eval", "MODEL=").split()))

    def test_scaffold_runs_by_default_and_can_be_turned_off(self):
        self.assertIn("--scaffold", " ".join(self.make_n("eval").split()))
        self.assertNotIn("--scaffold", " ".join(self.make_n("eval", "SCAFFOLD=").split()))

    def test_json_dir_writes_a_main_result_and_a_per_bash_case_result(self):
        out = " ".join(self.make_n("eval", "JSON_DIR=evals/results/run").split())
        self.assertIn("--json 'evals/results/run/main.json'", out)
        self.assertIn("--json 'evals/results/run/$c.json'", out)
        self.assertNotIn("--json", " ".join(self.make_n("eval").split()))

    def test_bash_case_loop_is_scoped_the_same_way_as_the_main_invocation(self):
        self.assertIn("eval_bash_cases.py --skill 'pr-open'", " ".join(self.make_n("eval", "SKILL=pr-open").split()))
        self.assertIn("eval_bash_cases.py --case 'foo-*'", " ".join(self.make_n("eval", "CASE=foo-*").split()))
        self.assertIn("eval_bash_cases.py", " ".join(self.make_n("eval").split()))

    def test_bash_case_loop_grants_bash_only_to_that_one_case(self):
        cmd = " ".join(self.make_n("eval").split())
        loop = cmd.split("eval_bash_cases.py", 1)[1]
        self.assertIn('--case "$c" --allow-tools Bash', loop)

    def test_ci_runs_the_static_check(self):
        mk = (KIT / "context-db" / "Makefile").read_text(encoding="utf-8")
        self.assertRegex(mk, r"(?m)^ci:.*\beval-check\b")
        self.assertIn("make -C .claude/context-db eval-check", (KIT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

    def test_ci_runs_the_full_require_all_gate(self):
        # a follow-up on the eval suite's own coverage work: eval-check also gates a trigger suite for every skill
        # and a recognized trigger phrase in every description, but only under --require-all — CI must pass
        # REQUIRE_ALL=1 (the Makefile's own switch for it), not just run the bare check.
        ci = (KIT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertRegex(ci, r"make -C \.claude/context-db eval-check REQUIRE_ALL=1")

    def test_the_token_spending_workflow_is_manual_only(self):
        text = (KIT / ".github" / "workflows" / "evals.yml").read_text(encoding="utf-8")
        triggers = re.split(r"\n(?=\S)", text.split("\non:", 1)[1], maxsplit=1)[0]  # the `on:` block, to the next top-level key
        self.assertEqual(re.findall(r"(?m)^  ([a-z_]+):", triggers), ["workflow_dispatch"])
        for name in ("skill", "models", "judge_model", "runs"):
            self.assertRegex(triggers, rf"(?m)^      {name}:")
        self.assertIn("secrets.CLAUDE_CODE_OAUTH_TOKEN", text)
        # inputs reach the scripts through env only — never spliced into a `run:` body, where they could inject shell
        # (the job's `timeout-minutes:` is a workflow key, not a script: it may read an input)
        for line in text.splitlines():
            if "${{ inputs." in line and not line.lstrip().startswith("timeout-minutes:"):
                self.assertRegex(line, r"^\s+[A-Z_]+: \$\{\{ inputs\.\w+ \}\}$", line)

    def test_the_workflow_never_passes_an_empty_judge(self):
        # `JUDGE=` on the make command line switches the target's default judge off; the workflow passes the
        # variable only when the input names a model
        text = (KIT / ".github" / "workflows" / "evals.yml").read_text(encoding="utf-8")
        run_line = next(line for line in text.splitlines() if "make -C context-db eval " in line)
        self.assertIn('${JUDGE_MODEL:+"JUDGE=$JUDGE_MODEL"}', run_line)
        self.assertNotRegex(run_line, r'(?<![+"])JUDGE="')

    def test_the_workflow_step_environment_keeps_the_default_judge(self):
        # make reads its environment too: a step variable named like one of the target's, exported empty because
        # the input was left empty, counts as set and switches `JUDGE ?=` off. Dry-run the target the way the step
        # calls it, with every variable of the step's `env:` block empty.
        text = (KIT / ".github" / "workflows" / "evals.yml").read_text(encoding="utf-8")
        step_env = text.split("name: Run the suite", 1)[1].split("run: |", 1)[0]
        names = re.findall(r"(?m)^          ([A-Z_]+): ", step_env)
        self.assertIn("SKILL", names)
        out = self.make_n("eval", "TRUST=1", "JOBS=4", "SKILL=", "MODEL=", "RUNS=", extra_env={n: "" for n in names})
        self.assertIn("--judge-model 'sonnet'", " ".join(out.split()))

    def check_inputs(self, cases: int, **inputs: str) -> "subprocess.CompletedProcess[str]":
        """The workflow's input check, run as the step runs it, in a kit with `cases` cases of the skill `demo`."""
        text = (KIT / ".github" / "workflows" / "evals.yml").read_text(encoding="utf-8")
        step = text.split("name: Check the inputs", 1)[1].split("run: |\n", 1)[1].split("\n\n", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "skills" / "demo").mkdir(parents=True)
            (Path(tmp) / "skills" / "demo" / "SKILL.md").write_text("", encoding="utf-8")
            for n in range(cases):
                (Path(tmp) / "evals" / f"demo-{n}").mkdir(parents=True)
            env = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(Path(tmp) / "out"),
                   "SKILL": "demo", "MODELS": "", "JUDGE_MODEL": "", "RUNS": "", **inputs}
            return subprocess.run(["bash", "-e", "-c", step], cwd=tmp, env=env, capture_output=True, text=True)

    def test_the_workflow_refuses_inputs_that_cannot_fit_the_time_limit(self):
        # the models run one after another under one limit, and `runs` multiplies every case: 15 cases at the
        # default 3 runs fit 30 minutes on one model and on two, not on three, and not at 9 runs
        self.assertEqual(self.check_inputs(15).returncode, 0)
        self.assertEqual(self.check_inputs(15, MODELS="sonnet,opus").returncode, 0)
        three = self.check_inputs(15, MODELS="sonnet,opus,haiku")
        self.assertEqual(three.returncode, 1)
        self.assertIn("::error::15 case(s) x 3 run(s) x 3 model(s) is about 38 min, the job stops at 30", three.stdout)
        self.assertEqual(self.check_inputs(15, RUNS="9").returncode, 1)
        self.assertEqual(self.check_inputs(15, RUNS="1", MODELS="sonnet,opus,haiku").returncode, 0)

    def test_the_time_limits_of_the_input_check_are_the_job_s(self):
        text = (KIT / ".github" / "workflows" / "evals.yml").read_text(encoding="utf-8")
        one, every = re.search(r"timeout-minutes: \$\{\{ inputs\.skill == '' && (\d+) \|\| (\d+) \}\}", text).groups()[::-1]
        self.assertIn(f"then limit={one}; ", text)
        self.assertIn(f"else limit={every}; ", text)


if __name__ == "__main__":
    unittest.main()
