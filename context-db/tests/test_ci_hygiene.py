"""CI hygiene: third-party actions pinned by commit SHA, Dependabot keeps them fresh, every job runs on GitHub-hosted
runners, and `kit-health --ci` runs against a blank store without writing anything.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
BIN = KIT / "context-db" / "bin"
WORKFLOWS = KIT / ".github" / "workflows"
KIT_HEALTH = KIT / "skills" / "kit-health" / "kit-health.py"
USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.M)
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")


def workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


class Pins(unittest.TestCase):
    def test_every_uses_is_a_full_sha(self):
        seen = 0
        for wf in workflows():
            for m in USES.finditer(wf.read_text(encoding="utf-8")):
                seen += 1
                self.assertRegex(m.group(1), PINNED, f"{wf.name}: `uses: {m.group(1)}` is not pinned to a 40-hex commit")
        self.assertGreater(seen, 0)

    def test_each_pin_carries_the_human_readable_version_comment(self):
        for wf in workflows():
            for line in wf.read_text(encoding="utf-8").splitlines():
                if "uses:" in line and "@" in line:
                    self.assertRegex(line, r"#\s*v\d", f"{wf.name}: pin without a `# vN` comment: {line.strip()}")

    def test_dependabot_watches_github_actions(self):
        cfg = (KIT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn("package-ecosystem: github-actions", cfg)
        self.assertIn("ci(deps)", cfg)


class Hosting(unittest.TestCase):
    """A public repository runs every job on GitHub-hosted runners — no workflow names a self-hosted runner, so
    neither a pull request's code nor a push can reach a maintainer's machine."""

    @staticmethod
    def code(wf: Path) -> str:
        """The workflow without its comment lines (they may name the runner they explain)."""
        return "\n".join(l for l in wf.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))

    @staticmethod
    def triggers(text: str) -> str:
        return text.split("\njobs:", 1)[0]

    def test_no_workflow_runs_self_hosted(self):
        seen = 0
        for wf in workflows():
            text = self.code(wf)
            seen += 1
            self.assertNotIn("self-hosted", text, f"{wf.name}: names a self-hosted runner")  # inline list or `- self-hosted`
            for runner in re.findall(r"runs-on:\s*(.+)", text):
                self.assertRegex(runner.strip(), r"^(ubuntu|macos|windows)-", f"{wf.name}: runs-on {runner.strip()} is not a GitHub-hosted label")
        self.assertGreater(seen, 0)

    def test_comment_triggered_jobs_only_answer_writers(self):
        """A comment event runs main's copy with the secrets: every job it can start checks the commenter's
        author_association, so a stranger's "@claude" on a public repo starts nothing."""
        seen = 0
        for wf in workflows():
            text = self.code(wf)
            if not re.search(r"\b(issue_comment|pull_request_review_comment)\b", self.triggers(text)):
                continue
            for job in re.split(r"\n  (?=[\w-]+:\n)", text.split("\njobs:", 1)[1])[1:]:
                if "comment" in job.split("runs-on:", 1)[0]:
                    seen += 1
                    self.assertIn("github.event.comment.author_association", job, f"{wf.name}: a comment-triggered job answers anyone")
        self.assertGreater(seen, 0)


class EngineMakefile(unittest.TestCase):
    def test_context_value_has_no_trailing_blanks(self):
        """an inline `# comment` after `CONTEXT := …` leaves the blanks before it in the value, and every recipe
        then reads a store literally named `.context  `."""
        # neither CONTEXT nor CONTEXT_ROOT inherited: the assertion is about what the Makefile derives, not the caller's store
        env = {k: v for k, v in os.environ.items() if k not in ("CONTEXT", "CONTEXT_ROOT")}
        p = subprocess.run(["make", "-C", str(KIT / "context-db"), "-s", "--eval", "show-context: ; @printf '%s|' \"$(CONTEXT)\"", "show-context"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        value = p.stdout.rsplit("|", 1)[0]
        self.assertEqual(value, value.strip(), f"CONTEXT carries whitespace: {value!r}")
        self.assertTrue(value.endswith(".context"), value)


class KitHealthCI(unittest.TestCase):
    def test_ci_mode_on_a_blank_store_is_green_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".context"
            env = {k: v for k, v in os.environ.items() if not k.startswith("WORKSPACE_")}
            env["CONTEXT_ROOT"] = str(root)
            subprocess.run([sys.executable, str(BIN / "kb.py"), "init", "--blank"], env=env, check=True, capture_output=True)
            before = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())
            p = subprocess.run([sys.executable, str(KIT_HEALTH), "--ci"], env=env, capture_output=True, text=True, cwd=KIT)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("CI mode", p.stdout)
            self.assertIn("**GREEN** (CI)", p.stdout)
            self.assertNotIn("HEALTH.md", "\n".join(a for a, _ in before))
            after = sorted((q.relative_to(root).as_posix(), q.stat().st_mtime_ns) for q in root.rglob("*") if q.is_file())
            self.assertEqual(before, after)

    def test_ci_mode_quiet_prints_only_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".context"
            env = {k: v for k, v in os.environ.items() if not k.startswith("WORKSPACE_")}
            env["CONTEXT_ROOT"] = str(root)
            subprocess.run([sys.executable, str(BIN / "kb.py"), "init", "--blank"], env=env, check=True, capture_output=True)
            p = subprocess.run([sys.executable, str(KIT_HEALTH), "--ci", "--quiet"], env=env, capture_output=True, text=True, cwd=KIT)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual(len(p.stdout.strip().splitlines()), 1)
            self.assertTrue(p.stdout.startswith("**GREEN** (CI)"), p.stdout)


if __name__ == "__main__":
    unittest.main()
