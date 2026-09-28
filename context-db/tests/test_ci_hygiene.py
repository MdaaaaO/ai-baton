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

    def test_labels_are_ones_the_repo_documents(self):
        # docs/contributing.md § Labels lists no `dependencies` — a Dependabot PR gets the type label the table
        # does carry (`enhancement`: a version bump is a `feat`), not one the repo has never created. The `labels:`
        # line itself is what Dependabot reads; a mention of the word in a comment above it is not the same claim.
        cfg = (KIT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        labels_line = next(line for line in cfg.splitlines() if line.strip().startswith("labels:"))
        self.assertNotIn("dependencies", labels_line)
        self.assertIn("enhancement", labels_line)


class VersionsSinglePlace(unittest.TestCase):
    """Every third-party tool version the kit installs at run time — the Claude Code CLI, conventional-release —
    is pinned once, in .github/versions.env, and every workflow (or workspace.mk) that needs it sources that file
    rather than repeating the literal, so the copies cannot drift from each other by hand."""

    VERSIONS_ENV = KIT / ".github" / "versions.env"

    def test_versions_env_declares_every_pin(self):
        text = self.VERSIONS_ENV.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^CLAUDE_CODE_VERSION=\S+$", "no CLAUDE_CODE_VERSION= line")
        self.assertRegex(text, r"(?m)^CONVENTIONAL_RELEASE_VERSION=\S+$", "no CONVENTIONAL_RELEASE_VERSION= line")

    def test_no_workflow_hardcodes_the_claude_code_version(self):
        for wf in workflows():
            text = wf.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"CLAUDE_CODE_VERSION:\s*[\"']?\d",
                               f"{wf.name}: hardcodes a version instead of sourcing .github/versions.env")

    def test_every_cli_install_sources_the_pinned_file_first(self):
        seen = 0
        install = 'npm install -g "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION"'
        for wf in workflows():
            text = wf.read_text(encoding="utf-8")
            for m in re.finditer(re.escape(install), text):
                seen += 1
                step_start = text.rfind("run:", 0, m.start())
                self.assertIn(".github/versions.env", text[step_start:m.start()],
                             f"{wf.name}: installs the CLI without sourcing versions.env in the same step")
        self.assertGreater(seen, 0)

    def test_no_floating_range_for_conventional_release(self):
        for path in [*workflows(), KIT / "workspace.mk"]:
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"conventional-release[><=]=?\s*[\d.]+\s*,",
                               f"{path.name}: a floating version range for conventional-release, not the exact pin")

    def test_conventional_release_workflows_use_the_exact_pin(self):
        for name in ("release.yml", "pr-title.yml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn("conventional-release==$CONVENTIONAL_RELEASE_VERSION", text,
                         f"{name}: not pinned to the exact version sourced from versions.env")


class PrTitleHygiene(unittest.TestCase):
    def test_has_a_concurrency_group(self):
        # pushes in quick succession (repeated `synchronize`) otherwise queue redundant runs
        text = (WORKFLOWS / "pr-title.yml").read_text(encoding="utf-8")
        self.assertIn("concurrency:", text.split("\njobs:", 1)[0])

    def test_versions_env_bootstrap_fallback_matches_the_pin(self):
        # pr-title checks out the base sha, which predates .github/versions.env for any PR opened before it
        # merged (a bootstrap gap, not a second place the pin can drift) — its `:=` fallback must equal the
        # exact value versions.env pins, or the two diverge the day someone bumps one and not the other.
        wf_text = (WORKFLOWS / "pr-title.yml").read_text(encoding="utf-8")
        m = re.search(r'CONVENTIONAL_RELEASE_VERSION:=([^}]+)\}', wf_text)
        self.assertIsNotNone(m, "pr-title.yml: no CONVENTIONAL_RELEASE_VERSION bootstrap fallback found")
        env_text = (KIT / ".github" / "versions.env").read_text(encoding="utf-8")
        pinned = re.search(r"(?m)^CONVENTIONAL_RELEASE_VERSION=(\S+)$", env_text)
        self.assertIsNotNone(pinned, "no CONVENTIONAL_RELEASE_VERSION= line in versions.env")
        self.assertEqual(m.group(1), pinned.group(1),
                         "pr-title.yml's bootstrap fallback has drifted from .github/versions.env's pin")


class EvalsHygiene(unittest.TestCase):
    TEXT = (WORKFLOWS / "evals.yml").read_text(encoding="utf-8")

    def test_timeout_is_not_the_old_90_minutes(self):
        self.assertNotIn("timeout-minutes: 90", self.TEXT)

    def test_a_missing_credential_fails_the_job(self):
        # a run with the secret missing must not conclude success — a required check satisfied by a run that
        # evaluated nothing (the earlier shape: every later step guarded and skipped, nothing left to fail)
        tail = self.TEXT.rsplit("enabled != 'true'", 1)
        self.assertEqual(len(tail), 2, "no final step gated on the credential being unavailable")
        self.assertIn("exit 1", tail[1][:300])

    def test_no_hardcoded_claude_context_db_path(self):
        # ci.yml checks out the whole workspace one level under `.claude`; evals.yml only itself, so `working-directory:
        # .claude` + a relative `context-db` path, not a literal `.claude/context-db` repeated in two run: blocks.
        # Comments (this file's own header included) may still describe ci.yml's unrelated layout in prose.
        code = "\n".join(line for line in self.TEXT.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn(".claude/context-db", code)


class PythonFloorLeg(unittest.TestCase):
    """ci.yml's python-floor job (the 3.9 leg) is a real merge gate, not decoration — auto-merge.yml must wait on
    it like every other required check, or a 3.9 regression could still get squash-merged."""

    def test_ci_yml_declares_the_job(self):
        text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: python-floor (3.9)", text)
        self.assertIn('python-version: "3.9"', text)

    def test_auto_merge_requires_it(self):
        text = (WORKFLOWS / "auto-merge.yml").read_text(encoding="utf-8")
        required = next(line for line in text.splitlines() if line.strip().startswith("REQUIRED:"))
        self.assertIn("python-floor (3.9)", required)


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


class VersionsEnvIntoGithubEnv(unittest.TestCase):
    def test_no_workflow_appends_the_raw_file(self):
        # $GITHUB_ENV accepts only NAME=value (or heredoc) lines; versions.env carries comments, so `cat` into it fails
        # the step — a release push would never tag
        for wf in workflows():
            text = Hosting.code(wf)
            self.assertIsNone(re.search(r"cat\s+[^|;&\n]*versions\.env[^|;&\n]*>>\s*\"?\$GITHUB_ENV", text),
                              f"{wf.name}: appends versions.env raw to $GITHUB_ENV — copy only the NAME=value lines")

    def test_the_assignment_filter_keeps_every_pin(self):
        env = (WORKFLOWS.parent / "versions.env").read_text(encoding="utf-8").splitlines()
        pins = [l for l in env if re.match(r"^[A-Z_][A-Z0-9_]*=", l)]
        self.assertTrue(pins)
        self.assertEqual(len(pins), len([l for l in env if "=" in l and not l.startswith("#")]))


class ApprovalTrust(unittest.TestCase):
    """auto-merge.yml trusts an APPROVED review by the shared Actions identity (#122): a pull_request job holding
    pull-requests: write must not run the PR's code with a token in reach, and the approval must name the
    claude-review run that auto-merge verifies."""

    def pr_write_workflows(self) -> list[tuple[Path, str]]:
        found = []
        for wf in workflows():
            text = Hosting.code(wf)
            if re.search(r"\bpull_request:", Hosting.triggers(text)) and re.search(r"pull-requests:\s*write", text):
                found.append((wf, text))
        self.assertTrue(found, "no pull_request workflow with pull-requests: write — the test lost its target")
        return found

    def test_checkouts_keep_no_credentials(self):
        for wf, text in self.pr_write_workflows():
            checkouts = len(re.findall(r"uses:\s*actions/checkout@", text))
            self.assertEqual(len(re.findall(r"persist-credentials:\s*false", text)), checkouts,
                             f"{wf.name}: a checkout leaves the pull-requests: write token in .git/config")

    def test_no_script_runs_from_the_pr_checkout(self):
        for wf, text in self.pr_write_workflows():
            hit = re.search(r"\b(python3?|bash|sh)\s+(\./)?(context-db|skills|hooks|bin|scripts)/|make\s+-C\s+(\./)?[\w.-]", text)
            self.assertIsNone(hit, f"{wf.name}: runs the PR's copy of a script ({hit and hit.group(0)}) — run the base branch's")

    def test_approval_is_bound_to_its_run(self):
        review = (WORKFLOWS / "claude-review.yml").read_text(encoding="utf-8")
        merge = Hosting.code(WORKFLOWS / "auto-merge.yml")
        self.assertIn("claude-review run $GITHUB_RUN_ID", review)
        self.assertIn('capture("claude-review run (?<id>[0-9]+)")', merge)
        self.assertIn(".github/workflows/claude-review.yml pull_request", merge)
        self.assertIn("grep -q '^\\.github/'", merge, "auto-merge must leave a PR that edits .github/ to the owner")
        for judge in ("review_gate", "leak_shapes", "review_evidence", "allow\\.txt", "docs/REVIEW\\.md"):
            self.assertIn(judge, merge.split("judges=", 1)[1].split("\n", 1)[0], f"auto-merge must leave a PR that edits {judge} to the owner")


class EngineMakefile(unittest.TestCase):
    def test_t_selects_one_module_else_full_discovery(self):
        """`make test T=<module>` runs one file instead of the whole suite (docs/contributing.md § Testing)."""
        def recipe(**make_vars: str) -> str:
            p = subprocess.run(["make", "-C", str(KIT / "context-db"), "-n", "test", *(f"{k}={v}" for k, v in make_vars.items())],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            return p.stdout
        self.assertIn("python3 -m unittest discover -s tests -t .", recipe(ISOLATED="1"))
        self.assertNotIn("tests.test_kb ", recipe(ISOLATED="1"))
        self.assertIn("python3 -m unittest tests.test_kb -v", recipe(ISOLATED="1", T="test_kb"))

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
