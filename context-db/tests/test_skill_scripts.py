"""The pure functions of two skill scripts: pr-review/scripts/trivial-check.py (version-bump shapes) and
pr-open/diagram-plan.py (path → facet classes, the plan, the marker). Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT / "context-db" / "bin"))
sys.path.insert(0, str(KIT / "context-db"))
from tests import hermetic_env  # noqa: E402


def load_script(path: Path, name: str, env: dict | None = None):
    saved = {k: os.environ.get(k) for k in (env or {})}
    os.environ.update(env or {})
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TrivialCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # the script reads .context/state/pr-review/config.json at import — give it a minimal one
        cls.tmp = tempfile.TemporaryDirectory()
        Path(cls.tmp.name, "config.json").write_text(json.dumps({"login": "someone", "auto_approve": {"mode": "off"}}), encoding="utf-8")
        cls.tc = load_script(KIT / "skills" / "pr-review" / "scripts" / "trivial-check.py", "trivial_check", {"PR_REVIEW_HOME": cls.tmp.name})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_bump_kind(self):
        b = self.tc.bump_kind
        self.assertEqual(b("1.2.3", "1.2.4"), "patch")
        self.assertEqual(b("1.2.3", "1.3.0"), "minor")
        self.assertEqual(b("1.2.3", "2.0.0"), "major")
        self.assertEqual(b("v1.2.3", "v1.2.3"), "none")
        self.assertEqual(b("1.2.4", "1.2.3"), "downgrade")
        self.assertEqual(b("1.2", "1.3"), "minor")  # x.y counts as x.y.0
        self.assertEqual(b("latest", "1.0.0"), "unknown")

    def test_pair_lines_and_version_only_change(self):
        patch = "@@ -1,3 +1,3 @@\n-requests==2.31.0\n+requests==2.31.1\n context\n-a==1.0.0\n+a==1.0.1\n"
        pairs = self.tc.pair_lines(patch)
        self.assertEqual(pairs, [("requests==2.31.0", "requests==2.31.1"), ("a==1.0.0", "a==1.0.1")])
        self.assertEqual(self.tc.version_only_change(*pairs[0]), ("2.31.0", "2.31.1"))
        self.assertIsNone(self.tc.version_only_change("requests==2.31.0", "httpx==2.31.1"))  # the name changed too
        self.assertIsNone(self.tc.version_only_change("x: 1.0.0 to 1.0.1", "x: 1.0.1 to 1.0.2"))  # two literals moved
        self.assertIsNone(self.tc.version_only_change("no version", "still none"))
        with self.assertRaises(ValueError):
            self.tc.pair_lines("@@\n-a\n-b\n+c\n")  # unbalanced hunk

    def test_pair_lines_keeps_content_lines_that_start_with_double_markers(self):
        # a removed `--flag` line reads `---flag` and an added `++x` reads `+++x`: both are changes, not file headers
        pairs = self.tc.pair_lines("@@ -1,2 +1,2 @@\n-a==1.0.0\n---flag\n+a==1.0.1\n+++x\n")
        self.assertEqual(pairs, [("a==1.0.0", "a==1.0.1"), ("--flag", "++x")])
        self.assertIsNone(self.tc.version_only_change("--flag", "++x"))
        # a bump that also drops a `--require-hashes` line must not pair up as a clean bump
        with self.assertRaises(ValueError):
            self.tc.pair_lines("@@ -1,2 +1,1 @@\n-requests==2.31.0\n---require-hashes\n+requests==2.31.1\n")
        # git file headers before the first hunk are still skipped
        self.assertEqual(self.tc.pair_lines("--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1 +1 @@\n-a==1.0.0\n+a==1.0.1\n"),
                         [("a==1.0.0", "a==1.0.1")])

    def test_pair_lines_refuses_patches_that_do_not_fit_their_hunk_headers(self):
        for bad in ("@@ -1,2 +1,2 @@\n-a==1.0.0\n+a==1.0.1\n",               # truncated: counts not consumed
                    "@@ -1 +1 @@\n-a==1.0.0\n+a==1.0.1\n+b==2.0.0\n",       # more lines than the header says
                    "-a==1.0.0\n+a==1.0.1\n",                                # no hunk header at all
                    "@@ -1 +1 @@\n-a==1.0.0\n+a==1.0.1\nstray\n"):           # a line outside any hunk
            with self.assertRaises(ValueError, msg=bad):
                self.tc.pair_lines(bad)
        self.assertEqual(self.tc.pair_lines("@@ -1 +1 @@\n-a==1.0.0\n\\ No newline at end of file\n+a==1.0.1\n"),
                         [("a==1.0.0", "a==1.0.1")])

    def test_dep_name_accepts_only_dependency_pin_shapes(self):
        d = self.tc.dep_name
        pins = {
            ("requirements.txt", "requests==2.31.0"): "requests",
            ("sub/requirements-dev.txt", "ruff>=0.4.1  # lint"): "ruff",
            ("pyproject.toml", '    "httpx[http2]==0.27.0",'): "httpx",
            ("pyproject.toml", 'requests = "^2.31.0"'): "requests",
            ("web/package.json", '    "@types/node": "^20.11.1",'): "@types/node",
            ("go.mod", "\tgolang.org/x/text v0.14.0 // indirect"): "golang.org/x/text",
            ("Dockerfile", "FROM python:3.12.1-slim AS base"): "python",
            ("Dockerfile.ci", "ARG RUFF_VERSION=0.4.1"): "RUFF_VERSION",
            (".pre-commit-config.yaml", "    rev: v0.4.1"): "?",
            ("packages.yml", "    version: 1.1.1"): "?",
            (".tool-versions", "python 3.12.1"): "python",
            ("Cargo.toml", 'serde = "1.0.197"'): "serde",
            (".python-version", "3.12.1"): "python",
        }
        for (path, line), want in pins.items():
            self.assertEqual(d(path, line), want, (path, line))
        host = ".".join(["10", "0", "0", "1"])  # an address, assembled at run time
        not_pins = [
            ("requirements.txt", "# pinned on 2026.9.1"),       # a date in a comment
            ("Dockerfile", f"ENV UPSTREAM_HOST={host}"),         # an IP
            ("Dockerfile", "LABEL release=1.2.3"),               # a label, not a base image
            ("package.json", '  "version": "1.2.3",'),          # the package's own version
            ("pyproject.toml", 'version = "0.1.0"'),
            ("pyproject.toml", 'python = "^3.11"'),               # the runtime, not a dependency
            ("pyproject.toml", 'requires-python = ">=3.11"'),
            ("go.mod", "go 1.22"),
            ("requirements.txt", "a==1.0.0; python_version >= '3.8'"),  # two literals
            ("setup.cfg", "requests==2.31.0"),                    # no shape known for this manifest
        ]
        for path, line in not_pins:
            self.assertIsNone(d(path, line), (path, line))

    def test_head_arg_fails_closed(self):
        script = KIT / "skills" / "pr-review" / "scripts" / "trivial-check.py"
        sha = "ab" * 20
        with tempfile.TemporaryDirectory() as ctx:
            env = dict(os.environ, PR_REVIEW_HOME=self.tmp.name, CONTEXT_ROOT=ctx)
            run = lambda *a: subprocess.run([sys.executable, str(script), "o/r", "1", *a], capture_output=True, text=True, env=env)
            for bad in (["--head", ""], ["--head"], ["--head", sha[:12]], ["--head", sha.upper()],
                        ["--head", sha, "--head", ""]):
                r = run(*bad)
                self.assertEqual(r.returncode, 2, (bad, r.stdout, r.stderr))
                self.assertNotIn('"eligible"', r.stdout, bad)
            # a full sha (and no --head) still parse; the minimal config has mode off, so no API call is made
            for ok in (["--head", sha], []):
                r = run(*ok)
                self.assertEqual(r.returncode, 0, (ok, r.stderr))
                self.assertIn("auto_approve.mode=off", r.stdout)

    def test_reference_and_command_files_are_never_docs(self):
        example = json.loads((KIT / "pr-review" / "config.example.json").read_text(encoding="utf-8"))["auto_approve"]
        for path in ("skills/pr-review/reference/scope.md", "kit/skills/x/reference/deep/a.md",
                     ".claude/commands/ship.md", "commands/review.md", "plugin/commands/x.md"):
            self.assertFalse(self.tc.is_docs_path(path, example), path)

    def test_own_version_keys_are_not_dependency_pins(self):
        d = self.tc.dep_name
        for path, line in (("pyproject.toml", 'current_version = "1.2.3"'), ("pyproject.toml", '__version__ = "1.2.3"'),
                           ("pyproject.toml", 'project_version = "1.2.3"'), ("Dockerfile", "ARG APP_VERSION=1.2.3"),
                           ("Dockerfile", "ENV PROJECT_VERSION=1.2.3"), ("Dockerfile", "ARG RELEASE_VERSION=1.2.3"),
                           ("Dockerfile.prod", 'ARG IMAGE_VERSION="1.2.3"'), ("Dockerfile", "ARG CURRENT_VERSION=1.2.3")):
            self.assertIsNone(d(path, line), (path, line))
        self.assertEqual(d("Dockerfile", "ARG RUFF_VERSION=0.4.1"), "RUFF_VERSION")  # a tool pin still is one

    def test_head_equals_form_and_unknown_flags(self):
        script = KIT / "skills" / "pr-review" / "scripts" / "trivial-check.py"
        sha = "cd" * 20
        with tempfile.TemporaryDirectory() as ctx:
            env = dict(os.environ, PR_REVIEW_HOME=self.tmp.name, CONTEXT_ROOT=ctx)
            run = lambda *a: subprocess.run([sys.executable, str(script), *a], capture_output=True, text=True, env=env)
            for bad in (["o/r", "1", "--head="], ["o/r", "1", "--head=" + sha[:12]], ["o/r", "1", f"--head={sha}", "--head", sha],
                        ["o/r", "1", "--dry-run"], ["--skip-ci", "o/r", "1"]):
                r = run(*bad)
                self.assertEqual(r.returncode, 2, (bad, r.stdout, r.stderr))
                self.assertNotIn('"eligible"', r.stdout, bad)
            r = run("o/r", "1", f"--head={sha}")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("auto_approve.mode=off", r.stdout)

    def test_agent_instructions_are_never_docs(self):
        example = json.loads((KIT / "pr-review" / "config.example.json").read_text(encoding="utf-8"))["auto_approve"]
        legacy = {"docs_globs": example["docs_globs"]}  # a config seeded before the exclusions existed
        for aa in (example, legacy):
            for path in ("skills/pr-open/SKILL.md", "agents/auto-runner.md", "plugin/agents/x.md", "WORKSPACE.md",
                         "CLAUDE.md", "sub/AGENTS.md"):
                self.assertFalse(self.tc.is_docs_path(path, aa), path)
            for path in ("README.md", "docs/guide.md", "skills/pr-open/README.md"):
                self.assertTrue(self.tc.is_docs_path(path, aa), path)
        # the example config documents the exclusion itself, not only the script
        for g in ("**/SKILL.md", "**/agents/**", "**/WORKSPACE.md"):
            self.assertIn(g, example["docs_exclude_globs"])

    def test_glob_any(self):
        g = self.tc.glob_any
        self.assertTrue(g("docs/guide.md", ["docs/**", "*.md"]))
        self.assertTrue(g("src/pkg/README.md", ["**/README.md"]))
        self.assertFalse(g("src/main.py", ["docs/**", "*.md"]))

    def test_gh_timeout_is_bounded_and_exits_2(self):
        # a hung `gh api` used to block trivial-check.py forever; it must now fail closed with a
        # distinct exit code and a clear reason, not hang the auto-approve gate.
        with mock.patch.object(self.tc.subprocess, "run",
                                side_effect=self.tc.subprocess.TimeoutExpired(cmd=["gh"], timeout=60)):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    self.tc.gh("repos/acme/widgets/pulls/1")
            self.assertEqual(cm.exception.code, 2)
        self.assertIn("timed out after 60s", buf.getvalue())

    def test_gh_timeout_with_allow_fail_returns_none_not_raises(self):
        with mock.patch.object(self.tc.subprocess, "run",
                                side_effect=self.tc.subprocess.TimeoutExpired(cmd=["gh"], timeout=60)):
            self.assertIsNone(self.tc.gh("repos/acme/widgets/pulls/1", allow_fail=True))

    def test_missing_config_prints_ok_false_and_exits_3(self):
        # a missing config.json used to traceback (FileNotFoundError at import time); it must now fail
        # closed with the documented setup-problem shape — distinct from both the eligible=false and
        # the error=true shapes a PR-level or API-level failure prints.
        script = KIT / "skills" / "pr-review" / "scripts" / "trivial-check.py"
        with tempfile.TemporaryDirectory() as tmp:
            home = str(Path(tmp, "home"))
            env = hermetic_env(tmp)
            env["PR_REVIEW_HOME"] = home
            r = subprocess.run([sys.executable, str(script), "acme/widgets", "1"],
                                capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 3, (r.stdout, r.stderr))
            out = json.loads(r.stdout)
            self.assertEqual(out["ok"], False)
            self.assertIn("no config.json", out["reasons"][0])
            self.assertIn(home, out["reasons"][0])
            self.assertIn(home, r.stderr)

    def test_unparsable_config_prints_ok_false_and_exits_3(self):
        script = KIT / "skills" / "pr-review" / "scripts" / "trivial-check.py"
        with tempfile.TemporaryDirectory() as tmp:
            home = str(Path(tmp, "home"))
            os.makedirs(home)
            Path(home, "config.json").write_text("{not json", encoding="utf-8")
            env = hermetic_env(tmp)
            env["PR_REVIEW_HOME"] = home
            r = subprocess.run([sys.executable, str(script), "acme/widgets", "1"],
                                capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 3, (r.stdout, r.stderr))
            out = json.loads(r.stdout)
            self.assertEqual(out["ok"], False)
            self.assertIn("unreadable", out["reasons"][0])

    @staticmethod
    def _fake_run(responses):
        """`responses`: {needle-in-the-"gh api ..."-command: (returncode, stdout)}; the first matching
        needle wins. Mirrors a real `gh` failure (non-zero exit, empty stdout) or success (0, JSON)."""
        def run(cmd, **kwargs):
            joined = " ".join(cmd)
            for needle, (rc, out) in responses.items():
                if needle in joined:
                    return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="" if rc == 0 else "boom")
            raise AssertionError(f"unexpected command in this test: {cmd}")
        return run

    def test_required_checks_prefers_rulesets_over_protection(self):
        rules = json.dumps([{"type": "required_status_checks",
                              "parameters": {"required_status_checks": [{"context": "ci"}, {"context": "lint"}]}}])
        responses = {
            "rules/branches/main": (0, rules),
            "protection/required_status_checks": (0, json.dumps({"contexts": ["should-not-be-used"]})),
        }
        with mock.patch.object(self.tc.subprocess, "run", side_effect=self._fake_run(responses)):
            required, source, readable = self.tc.required_checks("acme/widgets", "main")
        self.assertEqual(required, ["ci", "lint"])
        self.assertEqual(source, "rulesets")
        self.assertTrue(readable)

    def test_required_checks_falls_back_to_protection_when_ruleset_names_none(self):
        responses = {
            "rules/branches/main": (0, "[]"),
            "protection/required_status_checks": (0, json.dumps({"contexts": ["ci"], "checks": []})),
        }
        with mock.patch.object(self.tc.subprocess, "run", side_effect=self._fake_run(responses)):
            required, source, readable = self.tc.required_checks("acme/widgets", "main")
        self.assertEqual(required, ["ci"])
        self.assertEqual(source, "protection")
        self.assertTrue(readable)

    def test_required_checks_unreadable_when_neither_source_reachable(self):
        # neither endpoint could be read (both calls fail) — a reason on its own, never a vacuous pass.
        responses = {
            "rules/branches/main": (1, ""),
            "protection/required_status_checks": (1, ""),
        }
        with mock.patch.object(self.tc.subprocess, "run", side_effect=self._fake_run(responses)):
            required, source, readable = self.tc.required_checks("acme/widgets", "main")
        self.assertEqual(required, [])
        self.assertIsNone(source)
        self.assertFalse(readable)

    def test_required_checks_readable_with_no_required_checks_when_protection_unreadable(self):
        # rulesets read fine and named no required check (a ruleset-governed repo with none configured);
        # protection is unreadable (404, likely "not configured") — still a successful, readable result.
        responses = {
            "rules/branches/main": (0, "[]"),
            "protection/required_status_checks": (1, ""),
        }
        with mock.patch.object(self.tc.subprocess, "run", side_effect=self._fake_run(responses)):
            required, source, readable = self.tc.required_checks("acme/widgets", "main")
        self.assertEqual(required, [])
        self.assertEqual(source, "rulesets")
        self.assertTrue(readable)

    def test_combined_status_none_is_a_documented_error_not_a_crash(self):
        # a 200 with an empty body from the combined-status endpoint used to be indexed blind
        # (st["state"]) — a traceback waiting to happen. It must print the documented error=true shape.
        pr_json = {
            "head": {"sha": "a" * 40}, "user": {"login": "someone-else"}, "title": "t",
            "html_url": "https://example.test/pr/1", "state": "open", "draft": False,
            "base": {"ref": "main", "repo": {"default_branch": "main"}},
            "requested_reviewers": [{"login": "someone"}], "requested_teams": [], "changed_files": 0,
        }
        threads_empty = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}}

        def fake_gh(*args, paginate=False, allow_fail=False):
            path = args[0]
            if path.endswith("/pulls/1"): return pr_json
            if "/pulls/1/files" in path: return []
            if "/pulls/1/reviews" in path: return []
            if path == "graphql": return threads_empty
            if "/check-runs" in path: return []
            if path.endswith("/status"): return None  # the empty-body edge case under test
            raise AssertionError(f"unexpected gh call in this test: {args}")

        buf = io.StringIO()
        with mock.patch.object(self.tc, "gh", side_effect=fake_gh), \
             mock.patch.object(self.tc, "AA", {"mode": "live", "classes": []}), \
             mock.patch.object(sys, "argv", ["trivial-check.py", "acme/widgets", "1"]):
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    self.tc.main()
        self.assertEqual(cm.exception.code, 1)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["error"])
        self.assertIn("commits/", out["reasons"][0])
        self.assertIn("/status", out["reasons"][0])


class DiagramPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dp = load_script(KIT / "skills" / "pr-open" / "diagram-plan.py", "diagram_plan")

    def facet(self, path: str) -> str | None:
        by_facet, _lines, _ignored = self.dp.classify([(path, 10)], None)
        facets = list(by_facet)
        return facets[0] if facets else None

    def test_path_classes(self):
        cases = {
            "models/marts/fct_orders.sql": "dbt-model",
            "dbt_project.yml": "dbt-model",
            "dags/ingest_orders.py": "dag",
            "src/migrations/0002_backfill.py": "migration",
            "schemas/order.avsc": "event-schema",
            "app/api/orders.py": "api",
            "web/src/components/Button.tsx": "ui",
            "Jenkinsfile": "ci",
            "infra/terraform/main.tf": "infra",
            "etl/load.py": "pipeline",
            "skills/pr-open/SKILL.md": "skill",
            "requirements.txt": "deps",
            "README.md": "docs",
            "config/settings.yaml": "config",
            "src/service/orders.py": "service",
            "tests/test_orders.py": "tests",
        }
        for path, want in cases.items():
            self.assertEqual(self.facet(path), want, path)

    def test_dot_prefixed_paths_keep_their_dot(self):
        # `.github/workflows/ci.yml` used to lose its leading dot (`lstrip("./")` strips characters, not a prefix)
        # and never classified as ci; `./README.md` still loses the explicit `./` prefix.
        self.assertEqual(self.facet(".github/workflows/ci.yml"), "ci")
        self.assertEqual(self.facet(".github/actions/setup/action.yml"), "ci")
        self.assertEqual(self.facet("./README.md"), "docs")
        self.assertEqual(self.facet(".env.example"), "config")
        self.assertTrue(self.dp.match(".github/workflows/ci.yml", [".github/workflows/**"]))

    def test_repo_dir_mode_and_explain(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "clone"
            repo.mkdir()
            git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True,
                                            env=hermetic_env(repo))
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.invalid"); git("config", "user.name", "t")
            git("remote", "add", "origin", "git@github.com:acme/widgets.git")
            (repo / "README.md").write_text("x\n"); git("add", "."); git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
            git("checkout", "-q", "-b", "feat")
            (repo / ".github" / "workflows").mkdir(parents=True)
            (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
            git("add", "."); git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "ci")
            self.assertEqual(self.dp.resolve_repo(str(repo)), (repo.resolve(), "acme/widgets"))
            self.assertEqual(self.dp.resolve_repo("acme/widgets"), (None, "acme/widgets"))
            self.assertEqual(self.dp.resolve_repo(None), (None, None))
            with self.assertRaises(SystemExit):
                self.dp.resolve_repo(str(Path(tmp) / "nope"))
            script = KIT / "skills" / "pr-open" / "diagram-plan.py"
            # run from a directory that is NOT a git repo, as a session does from the workspace root
            r = subprocess.run([sys.executable, str(script), "--repo", str(repo), "--base", "main"], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("facets:   ci(1", r.stdout)
            self.assertIn("dominant: ci", r.stdout)
            git("remote", "remove", "origin")
            r = subprocess.run([sys.executable, str(script), "--repo", str(repo), "--base", "main"], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("note:     no `origin` remote", r.stdout)  # the overlay fallback is announced, not silent
            r = subprocess.run([sys.executable, str(script), "--base", "main"], cwd=tmp, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)  # the cwd is not a repo — a named failure, not a traceback
            self.assertNotIn("Traceback", r.stderr)
            r = subprocess.run([sys.executable, str(script), "--explain"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            for facet, _ in self.dp.DEFAULT_FACETS:
                self.assertIn(f"  {facet:<13}", r.stdout)
            for facet in self.dp.MATRIX:
                self.assertIn(f"  {facet}\n    WHERE", r.stdout)

    def test_sh_timeout_is_bounded_and_exits_2(self):
        # a hung `gh`/`git` call inside sh() used to block diagram-plan.py forever; it must now
        # fail closed with a distinct exit code instead of hanging pr-open.
        with mock.patch.object(self.dp.subprocess, "run",
                                side_effect=self.dp.subprocess.TimeoutExpired(cmd=["gh"], timeout=60)):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                with self.assertRaises(SystemExit) as cm:
                    self.dp.sh(["gh", "api", "repos/acme/widgets"])
            self.assertEqual(cm.exception.code, 2)
        self.assertIn("timed out after 60s", buf.getvalue())

    def test_repo_slug_timeout_is_bounded_and_exits_2(self):
        with mock.patch.object(self.dp.subprocess, "run",
                                side_effect=self.dp.subprocess.TimeoutExpired(cmd=["git"], timeout=60)):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                with self.assertRaises(SystemExit) as cm:
                    self.dp.repo_slug(Path("/tmp"))
            self.assertEqual(cm.exception.code, 2)
        self.assertIn("timed out after 60s", buf.getvalue())

    def test_files_from_git_timeout_is_bounded_and_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)  # not a .git dir, so files_from_git probes it via `git rev-parse --git-dir`
            with mock.patch.object(self.dp.subprocess, "run",
                                    side_effect=self.dp.subprocess.TimeoutExpired(cmd=["git"], timeout=60)):
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    with self.assertRaises(SystemExit) as cm:
                        self.dp.files_from_git("main", cwd)
                self.assertEqual(cm.exception.code, 2)
            self.assertIn("timed out after 60s", buf.getvalue())

    def test_variants_and_match(self):
        self.assertIn("dags/*.py", self.dp.variants("**/dags/**/*.py"))
        self.assertTrue(self.dp.match("a/b/dags/x/y.py", ["**/dags/**/*.py"]))
        self.assertTrue(self.dp.match("Makefile", ["Makefile"]))
        self.assertFalse(self.dp.match("src/x.py", ["**/*.sql"]))

    def test_overlay_key_with_a_dot_in_the_repo_name(self):
        # `diagrams.repos.<owner/site.io>` went through a dotted-key lookup that split the name at its `.`
        repos = {"acme/site.io": {"facets": {"pipeline": ["assets/**"]}, "ignore": ["*.svg"]}}
        saved = self.dp.env_get
        self.dp.env_get = lambda key, default: repos if key == "diagrams.repos" else default
        try:
            self.assertEqual(self.dp.env_overlay("acme/site.io"), ([("pipeline", ["assets/**"])], ["*.svg"]))
            self.assertEqual(self.dp.env_overlay("acme/other"), ([], []))
            self.assertEqual(self.dp.env_overlay(None), ([], []))
            self.dp.env_get = lambda key, default: "not an object"
            self.assertEqual(self.dp.env_overlay("acme/site.io"), ([], []))
            self.dp.env_get = lambda key, default: {"acme/site.io": "a bare value"}  # the slug's entry itself is not an object
            self.assertEqual(self.dp.env_overlay("acme/site.io"), ([], []))
        finally:
            self.dp.env_get = saved

    def test_overlay_and_ignore_via_config(self):
        by_facet, lines, ignored = self.dp.classify([("assets/lineage/x.txt", 3), ("badge.svg", 1)], None)
        self.assertEqual(set(by_facet), {"docs", "other"})  # x.txt is docs by default, an svg matches nothing
        saved = self.dp.env_overlay
        self.dp.env_overlay = lambda repo: ([("pipeline", ["assets/lineage/**"])], ["*.svg"])
        try:
            by_facet, lines, ignored = self.dp.classify([("assets/lineage/x.txt", 3), ("badge.svg", 1)], "acme/repo")
        finally:
            self.dp.env_overlay = saved
        self.assertEqual(list(by_facet), ["pipeline"])
        self.assertEqual(ignored, ["badge.svg"])

    def test_plan_and_marker_round_trip(self):
        files = [("dags/x.py", 40), ("README.md", 5), ("tests/test_x.py", 20)]
        by_facet, lines, _ = self.dp.classify(files, None)
        p = self.dp.plan(by_facet, lines, "feature")
        self.assertIn("dag", p["facets"])
        mk = self.dp.marker(p)
        self.assertTrue(mk.startswith("<!-- diagram-plan: ") and mk.endswith(" -->"))
        self.assertEqual(self.dp.marker_from_body(f"body\n\n{mk}\n"), mk[len("<!-- diagram-plan: "):-len(" -->")])
        self.assertIsNone(self.dp.marker_from_body("no marker here"))
        text = self.dp.render(p, [], mk)
        self.assertIn(mk, text)

    def test_files_from_list_reads_numstat_and_plain_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "files.txt"
            f.write_text("# comment\n3\t1\tdags/x.py\nM README.md\nsrc/y.py\n", encoding="utf-8")
            self.assertEqual(self.dp.files_from_list(str(f)), [("dags/x.py", 4), ("README.md", 0), ("src/y.py", 0)])

    def test_malformed_marker_line_is_distinguished_from_no_marker(self):
        # a hand-edited marker that no longer parses must not be reported the same as a body with none at all (#166)
        self.assertIsNone(self.dp.malformed_marker_line("no marker here"))
        good = "<!-- diagram-plan: facets=api dominant=api intent=feature where=api:x -->"
        self.assertIsNone(self.dp.malformed_marker_line(f"body\n{good}\n"))
        trailing = "<!-- diagram-plan: facets=api dominant=api intent=feature where=api:x -->extra"
        self.assertEqual(self.dp.malformed_marker_line(f"body\n{trailing}\n"), trailing)
        truncated = "<!-- diagram-plan: facets=api dominant=api"
        self.assertEqual(self.dp.malformed_marker_line(f"body\n{truncated}\n"), truncated)
        # prose that quotes the marker shape mid-line is not a marker, broken or otherwise
        self.assertIsNone(self.dp.malformed_marker_line("the plan lives in a `<!-- diagram-plan: … -->` line\n"))

    def test_check_reports_malformed_marker_distinctly_and_exits_3(self):
        saved = self.dp.files_from_pr
        files = [("app/api/orders.py", 40)]
        broken = "<!-- diagram-plan: facets=api dominant=api intent=feature where=api:x -->trailing"
        self.dp.files_from_pr = lambda repo, n: (files, [], f"body\n{broken}\n")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = self.dp.main(["prog", "--pr", "o/r", "1", "--check"])
            self.assertEqual(rc, 3)
            self.assertIn("MALFORMED MARKER", buf.getvalue())
            self.assertIn(broken, buf.getvalue())
        finally:
            self.dp.files_from_pr = saved

    def test_check_reports_no_marker_when_truly_absent(self):
        saved = self.dp.files_from_pr
        files = [("app/api/orders.py", 40)]
        self.dp.files_from_pr = lambda repo, n: (files, [], "plain body, nothing here\n")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = self.dp.main(["prog", "--pr", "o/r", "1", "--check"])
            self.assertEqual(rc, 3)
            self.assertIn("NO MARKER", buf.getvalue())
            self.assertNotIn("MALFORMED", buf.getvalue())
        finally:
            self.dp.files_from_pr = saved

    def test_trivial_lines_override_admits_a_facet_the_default_excludes(self):
        files = [("src/service/orders.py", 20), ("models/marts/fct_orders.sql", 5)]
        by_facet, lines, _ = self.dp.classify(files, None)
        p_default = self.dp.plan(by_facet, lines, "feature")
        self.assertNotIn("what", p_default["questions"])  # dbt-model (5 lines) filtered out by the default threshold
        p_override = self.dp.plan(by_facet, lines, "feature", trivial_lines=3)
        self.assertEqual(p_override["questions"]["what"]["facet"], "dbt-model")

    def test_secondary_share_override_admits_a_lighter_secondary_facet(self):
        files = [("src/service/orders.py", 20), ("models/marts/fct_orders.sql", 4)]
        by_facet, lines, _ = self.dp.classify(files, None)
        p_default = self.dp.plan(by_facet, lines, "feature", trivial_lines=1)  # both admitted as substantive
        self.assertNotIn("what", p_default["questions"])  # 4/20 = 20% < the default 25% share
        p_override = self.dp.plan(by_facet, lines, "feature", trivial_lines=1, secondary_share=0.1)
        self.assertEqual(p_override["questions"]["what"]["facet"], "dbt-model")

    def test_cli_trivial_lines_flag_is_wired_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "files.txt"
            f.write_text("20\t0\tsrc/service/orders.py\n5\t0\tmodels/marts/fct_orders.sql\n", encoding="utf-8")
            script = KIT / "skills" / "pr-open" / "diagram-plan.py"
            run = lambda *a: subprocess.run([sys.executable, str(script), "--files-from", str(f), "--json", *a],
                                             capture_output=True, text=True)
            r = run()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("what", json.loads(r.stdout)["questions"])
            r2 = run("--trivial-lines", "3")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertEqual(json.loads(r2.stdout)["questions"]["what"]["facet"], "dbt-model")

    def test_files_flag_takes_changed_paths_directly_not_a_list_file(self):
        # #348: `--files` used to be a LIST FILE (a path to a file of paths), not the changed paths
        # themselves — the flag name and the skill text read as if it took paths. `--files-from` is now the
        # list-file form (covered by test_cli_trivial_lines_flag_is_wired_through and
        # test_files_from_list_reads_numstat_and_plain_lines above); this is the direct-paths form.
        script = KIT / "skills" / "pr-open" / "diagram-plan.py"
        r = subprocess.run([sys.executable, str(script), "--files", "app/api/orders.py", "app/api/users.py",
                             "--type", "feature"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("facets:   api(2)", r.stdout)
        self.assertIn("dominant: api", r.stdout)

    def test_repo_dir_mode_sees_the_working_tree_before_the_first_commit(self):
        # the other half of #348: a plan run before the first commit (the moment the PR body is written) used
        # to see only `--base...HEAD` (committed history) and report no changed files. The default now also
        # diffs the working tree against HEAD and lists untracked files; `--committed-only` opts back out.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "clone"
            repo.mkdir()
            git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True,
                                            env=hermetic_env(repo))
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.invalid"); git("config", "user.name", "t")
            (repo / "README.md").write_text("x\n"); git("add", "."); git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
            git("checkout", "-q", "-b", "feat")
            (repo / "app" / "api").mkdir(parents=True)
            (repo / "app" / "api" / "orders.py").write_text("def handler():\n    pass\n")
            git("add", "app/api/orders.py")  # staged, never committed
            (repo / "app" / "api" / "users.py").write_text("def other():\n    pass\n")  # untracked, on purpose
            script = KIT / "skills" / "pr-open" / "diagram-plan.py"
            r = subprocess.run([sys.executable, str(script), "--repo", str(repo), "--base", "main", "--type", "feature"],
                                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("facets:   api(2", r.stdout)
            r_committed = subprocess.run([sys.executable, str(script), "--repo", str(repo), "--base", "main",
                                           "--type", "feature", "--committed-only"], capture_output=True, text=True)
            self.assertEqual(r_committed.returncode, 2, r_committed.stdout)
            self.assertIn("no changed files", r_committed.stderr)

    def test_help_documents_exit_codes_and_new_flags(self):
        script = KIT / "skills" / "pr-open" / "diagram-plan.py"
        r = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Exit codes:", r.stdout)
        self.assertIn("--trivial-lines", r.stdout)
        self.assertIn("--secondary-share", r.stdout)

    def test_skill_md_documents_exit_codes_and_malformed_marker(self):
        text = (KIT / "skills" / "pr-open" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Exit codes", text)
        self.assertIn("MALFORMED MARKER", text)


if __name__ == "__main__":
    unittest.main()
