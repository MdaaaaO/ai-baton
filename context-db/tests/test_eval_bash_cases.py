"""eval_bash_cases.py: which cases in scope declare Bash in their prompt.md `allowed_tools`, so the eval
target can grant it to each of them alone, never as a blanket grant to a whole invocation. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
BIN = KIT / "context-db" / "bin"
sys.path.insert(0, str(BIN))
import eval_bash_cases  # noqa: E402

PROMPT = "---\nmax_turns: 6\n{allowed}tags: [behaviour]\n---\n\nDo the thing.\n"


class Kit:
    """A throw-away kit root: evals/<case>/prompt.md + graders/criteria.md, written on demand."""

    def __init__(self, root: Path):
        self.root = root
        (root / "evals").mkdir(parents=True)

    def case(self, name: str, allowed_tools: str | None = None) -> Path:
        d = self.root / "evals" / name
        (d / "graders").mkdir(parents=True)
        allowed = f"allowed_tools: {allowed_tools}\n" if allowed_tools is not None else ""
        (d / "prompt.md").write_text(PROMPT.format(allowed=allowed), encoding="utf-8")
        (d / "graders" / "criteria.md").write_text("---\ntype: llm\n---\n\nx\n", encoding="utf-8")
        return d

    def case_yaml_only(self, name: str) -> Path:
        d = self.root / "evals" / name
        d.mkdir(parents=True)
        (d / "case.yaml").write_text('schema_version: "1.1"\n', encoding="utf-8")
        return d


class DeclaresBash(unittest.TestCase):
    def test_bash_in_the_list_counts(self):
        self.assertTrue(eval_bash_cases.declares_bash("[Read, Bash, Glob]"))
        self.assertTrue(eval_bash_cases.declares_bash("[Bash]"))

    def test_absent_allowed_tools_does_not_count(self):
        self.assertFalse(eval_bash_cases.declares_bash(None))

    def test_no_bash_does_not_count(self):
        self.assertFalse(eval_bash_cases.declares_bash("[Read, Glob, Skill]"))

    def test_a_longer_tool_name_does_not_count_as_bash(self):
        # a hypothetical "BashTool" or "MyBash" entry must not be mistaken for the real Bash grant
        self.assertFalse(eval_bash_cases.declares_bash("[BashTool]"))
        self.assertFalse(eval_bash_cases.declares_bash("[MyBash]"))


class BashCaseNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit = Kit(Path(self.tmp.name))

    def test_finds_the_one_case_that_declares_bash(self):
        self.kit.case("foo-plain", "[Read, Glob]")
        self.kit.case("foo-bash", "[Read, Bash]")
        self.assertEqual(eval_bash_cases.bash_case_names(self.kit.root, None), ["foo-bash"])

    def test_no_case_declares_bash(self):
        self.kit.case("foo-plain", "[Read, Glob]")
        self.assertEqual(eval_bash_cases.bash_case_names(self.kit.root, None), [])

    def test_a_case_glob_narrows_the_scope(self):
        self.kit.case("foo-bash", "[Bash]")
        self.kit.case("bar-bash", "[Bash]")
        self.assertEqual(eval_bash_cases.bash_case_names(self.kit.root, "foo-*"), ["foo-bash"])
        self.assertEqual(sorted(eval_bash_cases.bash_case_names(self.kit.root, None)), ["bar-bash", "foo-bash"])

    def test_a_case_yaml_only_case_is_never_in_scope(self):
        # eval_check.check_case never reads a case.yaml case's allowed_tools either; this answers the same
        # question the same way, so the two never disagree about a case this script would grant Bash to.
        self.kit.case_yaml_only("foo-yaml")
        self.assertEqual(eval_bash_cases.bash_case_names(self.kit.root, None), [])

    def test_a_missing_evals_dir_is_empty_not_an_error(self):
        empty = Path(self.tmp.name) / "no-evals-here"
        empty.mkdir()
        self.assertEqual(eval_bash_cases.bash_case_names(empty, None), [])

    def test_names_come_back_sorted(self):
        self.kit.case("zzz-bash", "[Bash]")
        self.kit.case("aaa-bash", "[Bash]")
        self.assertEqual(eval_bash_cases.bash_case_names(self.kit.root, None), ["aaa-bash", "zzz-bash"])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit = Kit(Path(self.tmp.name))

    def run_cli(self, *args: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run([sys.executable, str(BIN / "eval_bash_cases.py"), "--kit", str(self.kit.root), *args],
                               capture_output=True, text=True)

    def test_prints_one_name_per_line(self):
        self.kit.case("foo-bash", "[Bash]")
        self.kit.case("foo-plain", "[Read]")
        p = self.run_cli()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.splitlines(), ["foo-bash"])

    def test_skill_flag_is_shorthand_for_a_prefixed_glob(self):
        self.kit.case("foo-bash", "[Bash]")
        self.kit.case("bar-bash", "[Bash]")
        p = self.run_cli("--skill", "foo")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.splitlines(), ["foo-bash"])

    def test_empty_scope_prints_nothing_and_still_exits_zero(self):
        p = self.run_cli()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, "")

    def test_the_committed_suite_s_pr_open_bash_case_is_found(self):
        p = subprocess.run([sys.executable, str(BIN / "eval_bash_cases.py"), "--kit", str(KIT), "--skill", "pr-open"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("pr-open-behaviour-missing-assumed-line", p.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
