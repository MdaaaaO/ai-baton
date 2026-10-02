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


def run_block(workflow_text: str, step_name: str) -> str:
    """The dedented body of one `- name: <step_name>` step's `run: |` block — no YAML parser, just the same
    marker-splitting the rest of this file already uses, so this test file stays dependency-free."""
    marker = f"- name: {step_name}\n"
    after = workflow_text[workflow_text.index(marker) + len(marker):]
    body = after[after.index("run: |\n") + len("run: |\n"):]
    indent = None
    out = []
    for line in body.splitlines():
        if not line.strip():
            out.append("")
            continue
        cur = len(line) - len(line.lstrip(" "))
        if indent is None:
            indent = cur
        if cur < indent:
            break
        out.append(line[indent:])
    return "\n".join(out)


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


class ReleaseManifestHygiene(unittest.TestCase):
    def test_the_manifest_is_attested_before_the_release_is_published(self):
        # a published Release without its attestation is the state a verifier cannot tell from a forged one
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        build = text.index("release_manifest.py build")
        attest = text.index("uses: actions/attest-build-provenance@")
        publish = text.index("gh release edit")
        self.assertTrue(build < attest < publish, "order must be build, attest, publish")
        for perm in ("attestations: write", "id-token: write"):
            self.assertIn(perm, text)

    def test_only_a_missing_release_is_created(self):
        # any other gh failure (auth, network) must fail the step, not be read as "no release yet"
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertIn('"release not found"', text)
        self.assertNotIn("|| echo missing", text)


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


class RunBlocksParse(unittest.TestCase):
    def test_every_run_block_parses_as_bash(self):
        # a quote left open inside a `${VAR:-…}` default is a parse error only at run time: the step dies before
        # its first command, and a workflow that starts by hand shows it on the first paid run
        for wf in sorted(WORKFLOWS.glob("*.yml")):
            text = wf.read_text(encoding="utf-8")
            for n, (_, body) in enumerate(re.findall(r"(?m)^( +)run: \|\n((?:\1  .*\n|\n)+)", text)):
                r = subprocess.run(["bash", "-n"], input=body, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{wf.name}, run block {n}:\n{r.stderr}")


class EvalsHygiene(unittest.TestCase):
    TEXT = (WORKFLOWS / "evals.yml").read_text(encoding="utf-8")

    def test_timeout_is_not_the_old_90_minutes(self):
        self.assertNotIn("timeout-minutes: 90", self.TEXT)

    def test_no_comment_says_the_credential_was_never_configured(self):
        self.assertNotIn("has never been configured", self.TEXT)

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

    def test_model_is_never_passed_unconditionally(self):
        # #489: the CLI's own default model took several turns where sonnet took one and hit a case's max_turns;
        # the make target now pins MODEL ?= sonnet, so the workflow must only override it when an input names a
        # model (the same MODEL="$model" gotcha PR #490 already fixed for JUDGE/JUDGE_MODEL on this line)
        run_line = next(line for line in self.TEXT.splitlines() if "make -C context-db eval " in line)
        self.assertIn('${model:+"MODEL=$model"}', run_line)
        self.assertNotRegex(run_line, r'(?<![+"])MODEL="')

    def test_the_eval_sandbox_is_probed_and_no_second_scrub_is_nested(self):
        # #489: `claude plugin eval` sandboxes each tool call with bwrap itself and keeps the credential out of the
        # agent's environment; CLAUDE_CODE_SUBPROCESS_ENV_SCRUB on top nested a second bwrap that could not mask the
        # run's `.eval-artifacts` in its read-only cwd, so every case's Bash failed in the second measured run
        self.assertNotIn("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB:", self.TEXT)
        step = self.TEXT.split("name: Run the suite", 1)[1].split("\n      - ", 1)[0]
        self.assertIn("command -v bwrap", step)
        # an installed bwrap that cannot start (ubuntu-24.04's AppArmor userns restriction) failed every case's
        # Bash in the first measured run; the probe starts bwrap once before any case spends the credential
        self.assertIn("bwrap --ro-bind / / --dev /dev --proc /proc --unshare-all true", step)
        self.assertIn("kernel.apparmor_restrict_unprivileged_userns=0", self.TEXT.split("name: Install the Claude Code CLI", 1)[1].split("\n      - ", 1)[0])

    def test_the_result_json_is_uploaded_as_an_artifact_even_on_failure(self):
        step = self.TEXT.split("name: Upload the result JSON", 1)[1].split("\n      - ", 1)[0]
        self.assertIn("always()", self.TEXT.split("name: Upload the result JSON", 1)[0].rsplit("- if:", 1)[1].split("\n", 1)[0])
        self.assertIn("actions/upload-artifact@", step)
        self.assertIn("if-no-files-found: ignore", step)


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


class MacosLeg(unittest.TestCase):
    """The BSD/macOS fallbacks in skills/_lib/portable.sh (no flock, no setsid, BSD date) were exercised by
    PATH-manipulation tests on a Linux runner only, never on a real macOS interpreter — a real macos-latest job
    proves the fallbacks, not just the tests that stand in for them, and must be gated so it doesn't run (and
    cost) on every unrelated PR."""

    TEXT = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    def test_declares_a_macos_job(self):
        self.assertIn("runs-on: macos-latest", self.TEXT)

    def test_macos_job_runs_the_engine_suite(self):
        job = self.TEXT.split("\n  macos:\n", 1)[1].split("\n  forced-signing:\n", 1)[0]
        self.assertIn("make -C context-db test", job)

    def test_the_engine_suite_is_a_required_check_not_advisory(self):
        # the suite passes on macOS now (the make/locale/path fixes around it): a red run must fail the job,
        # the same bar ForcedSigningLeg holds its own engine-suite step to.
        job = self.TEXT.split("\n  macos:\n", 1)[1].split("\n  forced-signing:\n", 1)[0]
        self.assertNotIn("continue-on-error", job)

    def test_macos_job_is_gated_by_changed_paths(self):
        job = self.TEXT.split("\n  macos:\n", 1)[1].split("\n  forced-signing:\n", 1)[0]
        self.assertIn("needs: changed-paths", job)
        self.assertIn("needs.changed-paths.outputs.portability", job)

    def test_changed_paths_job_covers_the_scoped_globs(self):
        # docs claim: sh scripts, their tests, skills/_lib, hooks/, and ci.yml itself — every place a shell
        # portability bug (or the legs' own routing logic) can hide
        job = self.TEXT.split("\n  changed-paths:\n", 1)[1].split("\n  macos:\n", 1)[0]
        self.assertIn(r"\.sh$", job)
        self.assertIn("context-db/tests/", job)
        self.assertIn("skills/_lib/", job)
        self.assertIn(r"^hooks/", job)
        self.assertIn(r"^\.github/workflows/ci\.yml$", job)

    def test_a_push_to_main_is_never_silently_skipped(self):
        # the path filter only bounds a PR's own iterating pushes; a merge to main always gets the full matrix
        job = self.TEXT.split("\n  changed-paths:\n", 1)[1].split("\n  macos:\n", 1)[0]
        self.assertIn('"$EVENT_NAME" != "pull_request"', job)

    def test_shell_parse_steps_pick_the_interpreter_by_shebang(self):
        # sh -n/dash -n reject bash syntax (`< <(...)`, arrays) outright — a bash-shebang script must be
        # routed to `bash -n` (the macOS SYSTEM bash, 3.2) instead, never lumped in with the sh/dash scripts
        job = self.TEXT.split("\n  macos:\n", 1)[1].split("\n  forced-signing:\n", 1)[0]
        self.assertIn("head -1", job, "no shebang inspection in the macos job's shell-parse steps")
        self.assertIn("*bash*", job, "no bash-shebang match in the macos job's shell-parse steps")
        self.assertIn("bash -n", job)
        self.assertIn("sh -n", job)

    def test_shell_parse_steps_pass_against_the_real_repo_today(self):
        # runs the two step scripts verbatim (extracted from ci.yml, not re-typed) against THIS checkout —
        # proves the shebang routing actually clears every tracked .sh/hook today, not just in theory
        for step_name in (
            "Bash-shebang scripts parse under the macOS system bash (3.2)",
            "sh/no-shebang scripts parse under sh (and dash, where installed)",
        ):
            script = run_block(self.TEXT, step_name)
            self.assertTrue(script.strip(), f"could not extract the '{step_name}' step's script")
            # a CI runner may install dash; a developer's `make test` must never install anything, so the
            # package-manager line is dropped before the step runs locally
            script = "\n".join(l for l in script.splitlines() if "brew install" not in l and "apt-get install" not in l)
            self.assertNotRegex(script, r"\b(brew|apt-get|pip3?) install\b", "a local test run must never install a package")
            r = subprocess.run(["bash", "-c", script], cwd=KIT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{step_name}:\n{r.stdout}{r.stderr}")


class ForcedSigningLeg(unittest.TestCase):
    """test_hermetic_fixtures.py checks the tests against a hostile ~/.gitconfig built inside one test, never
    across a whole suite run whose runner itself has commit.gpgsign=true (what a contributor with SSH-signed
    commits on by default actually has) — a CI leg that forces it ambiently, with a signing key that cannot
    exist, and requires the suite to stay green anyway."""

    TEXT = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    def test_declares_the_job(self):
        self.assertIn("forced-signing:", self.TEXT)

    def job_text(self) -> str:
        return self.TEXT.split("\n  forced-signing:\n", 1)[1].split("\n  python-floor:\n", 1)[0]

    def test_forces_commit_signing_through_a_missing_key(self):
        job = self.job_text()
        self.assertIn("git config --global commit.gpgsign true", job)
        self.assertIn("git config --global gpg.format ssh", job)
        self.assertIn("git config --global user.signingkey", job)

    def test_the_engine_suite_runs_after_the_hostile_config_and_is_not_allowed_to_fail(self):
        job = self.job_text()
        self.assertNotIn("continue-on-error", job)
        signing_at = job.index("git config --global commit.gpgsign true")
        test_at = job.index("make -C context-db test")
        self.assertLess(signing_at, test_at, "the suite must run AFTER signing is forced, not before")

    def test_is_gated_by_changed_paths_like_macos(self):
        job = self.job_text()
        self.assertIn("needs: changed-paths", job)
        self.assertIn("needs.changed-paths.outputs.portability", job)


class MermaidDepsPinned(unittest.TestCase):
    """pr-open's mermaid-check.mjs used to be run after `npm i --no-audit --no-fund mermaid@11 jsdom dompurify` —
    two of the three packages unpinned, no lockfile, so the Mermaid validation result depended on whatever npm
    resolved that day. skills/pr-open now carries its own package.json + package-lock.json (installed with
    `npm ci`, so the exact versions in the lockfile are what runs), and Dependabot tracks that directory."""

    PR_OPEN = KIT / "skills" / "pr-open"

    def test_package_json_and_lockfile_exist(self):
        self.assertTrue((self.PR_OPEN / "package.json").is_file(), "no skills/pr-open/package.json")
        self.assertTrue((self.PR_OPEN / "package-lock.json").is_file(), "no skills/pr-open/package-lock.json")

    def test_package_json_pins_all_three_deps_to_exact_versions(self):
        import json
        deps = json.loads((self.PR_OPEN / "package.json").read_text(encoding="utf-8"))["dependencies"]
        for name in ("mermaid", "jsdom", "dompurify"):
            self.assertIn(name, deps, f"package.json is missing {name}")
            self.assertRegex(deps[name], r"^\d+\.\d+\.\d+$", f"{name}: {deps[name]!r} is not an exact version")

    def test_lockfile_versions_match_package_json(self):
        import json
        pkg = json.loads((self.PR_OPEN / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((self.PR_OPEN / "package-lock.json").read_text(encoding="utf-8"))
        root_deps = lock["packages"][""]["dependencies"]
        self.assertEqual(pkg["dependencies"], root_deps)

    def test_skill_md_no_longer_tells_readers_to_npm_i_unpinned(self):
        # the body and its reference files together — the validate-before-publishing step lives in reference/
        files = [self.PR_OPEN / "SKILL.md", *sorted((self.PR_OPEN / "reference").glob("*.md"))]
        text = "\n".join(f.read_text(encoding="utf-8") for f in files)
        self.assertNotIn("npm i --no-audit --no-fund mermaid@11 jsdom dompurify", text)
        self.assertIn("npm ci", text)

    def test_dependabot_tracks_the_pr_open_npm_manifest(self):
        cfg = (KIT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn("package-ecosystem: npm", cfg)
        self.assertIn("directory: /skills/pr-open", cfg)

    def test_dependabot_ignores_semver_major_for_mermaid_and_jsdom(self):
        # the validator stays on the mermaid major GitHub renders diagrams with; jsdom only hosts mermaid so it
        # follows the same rule. dompurify is not pinned to a major, so it is not expected in the ignore list.
        cfg = (KIT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        npm_block = cfg[cfg.index("package-ecosystem: npm"):]
        for name in ("mermaid", "jsdom"):
            m = re.search(r'dependency-name:\s*"?' + name + r'"?\s*\n\s*update-types:\s*\[[^\]]*"version-update:semver-major"[^\]]*\]', npm_block)
            self.assertIsNotNone(m, f"no semver-major ignore rule for {name} in the pr-open npm entry")


class DependabotManifestSkipBump(unittest.TestCase):
    """A Dependabot npm PR only ever touches a unit's package.json/package-lock.json — it cannot also bump
    SKILL.md's metadata.version, so ci.yml's kit-verify job grants it the same --skip-bump treatment as a
    wording-only PR, computed from the actor and the changed paths (never a silent exemption inside
    review_gate.py itself, which has no manifest-only carve-out — see test_review_gate.py's Bumps tests)."""

    TEXT = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    def job_text(self) -> str:
        return self.TEXT.split("\n  kit-verify:\n", 1)[1].split("\n  changed-paths:\n", 1)[0]

    def test_the_step_names_both_the_actor_and_the_path_check(self):
        job = self.job_text()
        step = job.split("Dependabot manifest-only diff", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("dependabot[bot]", step)  # the author condition
        # the PR's author, never github.actor: a re-run by a person would otherwise flip the decision
        self.assertIn("github.event.pull_request.user.login", step)
        self.assertNotIn("github.actor", step)
        self.assertIn("skills/[^/]+/package(-lock)?", step)  # the path check: only skills/*/package(-lock).json
        self.assertIn("git diff --name-only", step)  # computed from the diff, not claimed
        self.assertIn("bump check skipped: Dependabot manifest-only change", step)  # visible, not silent

    def test_review_gate_step_still_honours_the_wording_only_path(self):
        job = self.job_text()
        step = job.split("Review gate (tier 0", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("dependabot-manifest.outputs.skip", step)
        self.assertIn("contains(github.event.pull_request.labels.*.name, 'wording')", step)
        self.assertIn("[skip-bump]", step)

    def test_review_gate_py_has_no_silent_manifest_exemption(self):
        # the exemption lives in ci.yml (computed, visible in the run's log), not as an unlabeled shape
        # review_gate.py matches on its own — grep the module, not just this workflow.
        text = (BIN / "review_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("lockfile_only", text)
        self.assertNotIn("NPM_MANIFESTS", text)


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
        # `--eval` is GNU make 4+ only (Apple ships 3.81); an included wrapper makefile works on both.
        real_makefile = KIT / "context-db" / "Makefile"
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "show-context.mk"
            wrapper.write_text(f"include {real_makefile}\nshow-context: ; @printf '%s|' \"$(CONTEXT)\"\n", encoding="utf-8")
            p = subprocess.run(["make", "-C", str(KIT / "context-db"), "-s", "-f", str(wrapper), "show-context"],
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


class AutoMergeCheckPick(unittest.TestCase):
    """auto-merge.yml's required-check loop, run for real on synthetic check-run rows: a head can carry a
    `skipped` claude-review run (a review-comment event) next to the real one, and the loop must judge the
    newest non-skipped run, wait on anything still running, and still wait for the owner when every run of
    a name was skipped."""

    LOOP = None

    @classmethod
    def setUpClass(cls):
        text = (WORKFLOWS / "auto-merge.yml").read_text(encoding="utf-8")
        start = text.index("          IFS=',' read -ra required")
        end = text.index("          done", start) + len("          done")
        cls.LOOP = "\n".join(line[10:] for line in text[start:end].splitlines())

    def decide(self, rows: list[tuple[str, str, str, str]], required: str = "claude-review") -> str:
        runs = "\n".join("\t".join(r) for r in rows)
        script = ('skip() { echo "SKIP: $*"; exit 0; }\n'
                  f'REQUIRED={required!r}\nruns=$(cat)\n{self.LOOP}\necho MERGE\n')
        r = subprocess.run(["bash", "-c", script], input=runs, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_a_later_skipped_run_does_not_hide_the_real_success(self):
        out = self.decide([("claude-review", "completed", "success", "2026-09-28T09:00:00Z"),
                           ("claude-review", "completed", "skipped", "2026-09-28T09:05:00Z")])
        self.assertEqual(out, "MERGE")

    def test_the_newest_non_skipped_run_wins(self):
        out = self.decide([("claude-review", "completed", "success", "2026-09-28T09:00:00Z"),
                           ("claude-review", "completed", "failure", "2026-09-28T09:10:00Z"),
                           ("claude-review", "completed", "skipped", "2026-09-28T09:20:00Z")])
        self.assertIn("concluded failure", out)

    def test_every_run_skipped_still_waits_for_the_owner(self):
        out = self.decide([("claude-review", "completed", "skipped", "2026-09-28T09:00:00Z")])
        self.assertIn("concluded skipped", out)

    def test_a_run_still_going_waits_even_next_to_a_success(self):
        out = self.decide([("claude-review", "completed", "success", "2026-09-28T09:00:00Z"),
                           ("claude-review", "in_progress", "null", "")])
        self.assertIn("still in_progress", out)

    def test_a_missing_required_check_waits(self):
        out = self.decide([("kit-verify", "completed", "success", "2026-09-28T09:00:00Z")])
        self.assertIn("has not run on this head", out)


class TreeReviewPrompt(unittest.TestCase):
    """`--allowed-tools` takes a variable number of values: a prompt passed as the next argument is swallowed
    as a tool name and `claude -p` runs with no input. tree-review.yml must feed the prompt on stdin."""

    def test_the_prompt_goes_on_stdin_not_after_a_variadic_flag(self):
        text = (WORKFLOWS / "tree-review.yml").read_text(encoding="utf-8")
        self.assertIn('printf \'%s\' "$prompt" | claude "$@"', text)
        self.assertNotIn('claude "$@" "$prompt"', text)


class CodeOwners(unittest.TestCase):
    """A script a workflow runs is as privileged as the workflow: both paths need the owner's review."""

    def test_every_github_path_a_workflow_runs_has_a_code_owner(self):
        owned = [ln.split()[0] for ln in (KIT / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.startswith("#")]
        called = set()
        for wf in workflows():
            called.update(re.findall(r"\.github/[\w./-]+\.(?:sh|py|mjs|js)\b", wf.read_text(encoding="utf-8")))
        self.assertTrue(called, "no workflow calls a script under .github/ - drop this test or fix its regex")
        for path in sorted(called):
            self.assertTrue(any(("/" + path).startswith(o) for o in owned),
                            f"{path} is run by a workflow but no CODEOWNERS entry covers it")


if __name__ == "__main__":
    unittest.main()
