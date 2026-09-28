"""The pure functions of two skill scripts: pr-review/scripts/trivial-check.py (version-bump shapes) and
pr-open/diagram-plan.py (path → facet classes, the plan, the marker). Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT / "context-db" / "bin"))


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
            git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
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


if __name__ == "__main__":
    unittest.main()
