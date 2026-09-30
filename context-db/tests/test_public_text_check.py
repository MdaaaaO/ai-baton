"""`kit_profile.py public-text-check <file> --repo <owner/repo>` (#357): scans a body file a session is about
to post for THIS environment's own private values before it reaches a public, non-tracker.repos repo. Every
value here is assembled at run time (an email-shaped string, a 12-digit number or a real zone name trips the
leak gate the kit runs on itself) against a throw-away env store — never the workspace's.
Stdlib unittest, no network (the `gh` lookup is only exercised via a stub on PATH). Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402


def run(*args: str, root: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in ("WORKSPACE_TZ", "CLAUDE_CODE_SESSION_ID")}
    e.update({"CONTEXT_ROOT": str(root), **(env or {})})
    return subprocess.run([sys.executable, str(BIN / "kit_profile.py"), "public-text-check", *args],
                          env=e, cwd=BIN, capture_output=True, text=True)


def write_config(root: Path, cfg: dict) -> None:
    d = root / "reference" / "env"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


class UsageErrors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_args_is_exit_2(self):
        r = run(root=self.root)
        self.assertEqual(r.returncode, 2)
        self.assertIn("needs <file>", r.stderr)

    def test_bad_repo_slug_is_exit_2(self):
        f = Path(self.tmp.name) / "body.md"
        f.write_text("hello\n", encoding="utf-8")
        r = run(str(f), "--repo", "not-a-slug", root=self.root)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not an <owner>/<repo> slug", r.stderr)

    def test_missing_file_is_exit_2(self):
        r = run(str(Path(self.tmp.name) / "nope.md"), "--repo", "a/b", root=self.root)
        self.assertEqual(r.returncode, 2)

    def test_public_and_private_together_is_exit_2(self):
        f = Path(self.tmp.name) / "body.md"
        f.write_text("hello\n", encoding="utf-8")
        r = run(str(f), "--repo", "a/b", "--public", "--private", root=self.root)
        self.assertEqual(r.returncode, 2)


class Applicability(unittest.TestCase):
    """The check applies only when the repo is public AND not one of this environment's own tracker.repos."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        self.body = Path(self.tmp.name) / "body.md"
        self.body.write_text("mentions " + "acme" + "corp/widgets" + "-app freely\n", encoding="utf-8")
        write_config(self.root, {"environment": "t", "tracker": {"kind": "github", "repos": ["acmecorp/widgets-app"]}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_own_tracker_repo_is_skipped_even_with_hits(self):
        r = run(str(self.body), "--repo", "acmecorp/widgets-app", "--public", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own tracker.repos", r.stdout)

    def test_own_tracker_repo_match_is_case_insensitive(self):
        r = run(str(self.body), "--repo", "AcmeCorp/Widgets-App", "--public", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_forced_private_is_skipped(self):
        r = run(str(self.body), "--repo", "other/pub", "--private", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not public", r.stdout)

    def test_forced_public_with_no_store_still_runs_clean(self):
        clean = Path(self.tmp.name) / "clean.md"
        clean.write_text("nothing sensitive here\n", encoding="utf-8")
        r = run(str(clean), "--repo", "other/pub", "--public", root=Path(self.tmp.name) / "no-store")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class Scan(unittest.TestCase):
    """The scan itself — same value set kit-health's leak scan uses, plus a case-insensitive tracker key and
    the cross-org `<org>/<repo>#<n>` shape (#357's second-sweep note)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        write_config(self.root, {
            "environment": "t",
            "tracker": {"kind": "jira", "repos": ["acme" + "corp/widgets" + "-app"], "key_regex": r"\bKEY-\d+\b"},
            "github": {"org": "acme" + "corp"},
        })

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str) -> Path:
        p = Path(self.tmp.name) / "body.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_clean_text_is_exit_0(self):
        f = self.write("nothing sensitive in this body at all\n")
        r = run(str(f), "--repo", "other/pub", "--public", root=self.root)
        self.assertEqual((r.returncode, "clean" in r.stdout), (0, True), r.stderr)

    def test_bare_repo_name_hash_number_is_a_hit(self):
        # #357 second-sweep note: "a bare `<repo>#<n>` with no org"
        f = self.write("see " + "widgets-app" + "#42 for the fix\n")
        r = run(str(f), "--repo", "other/pub", "--public", root=self.root)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("widgets-app", r.stdout)

    def test_session_footer_embedding_a_lane_tracker_key_lower_cased_is_a_hit(self):
        # #357 second-sweep note: tracker keys lower-cased inside a lane name, in the session footer
        key = "KEY" + "-42"
        f = self.write(f"work continues.\n\nsession `{key.lower()}-migration`\n")
        r = run(str(f), "--repo", "other/pub", "--public", root=self.root)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(key.lower(), r.stdout.lower())

    def test_cross_org_hash_reference_is_a_hit(self):
        f = self.write("tracked in " + "foreign" + "-org/internal#7\n")
        r = run(str(f), "--repo", "other/pub", "--public", root=self.root)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("other-org", r.stdout)

    def test_same_org_hash_reference_to_the_target_repo_is_not_flagged_by_cross_org(self):
        f = self.write("see other/thing#7\n")
        r = run(str(f), "--repo", "other/pub", "--public", root=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_hit_lines_are_one_per_line_number_what_matched(self):
        f = self.write("line one is clean\nline two names " + "acmecorp/widgets" + "-app directly\n")
        r = run(str(f), "--repo", "other/pub", "--public", root=self.root)
        self.assertEqual(r.returncode, 1, r.stderr)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertTrue(all(ln.split(":", 1)[0] == "2" for ln in lines), r.stdout)


class RepoIsPublicStub(unittest.TestCase):
    """The `gh api` fallback used only when --public/--private are both absent — a stub `gh` on PATH, no network."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        write_config(self.root, {"environment": "t", "tracker": {"repos": []}})
        self.stub_dir = Path(self.tmp.name) / "stubbin"
        self.stub_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_stub(self, private_value: str) -> None:
        stub = self.stub_dir / "gh"
        stub.write_text(f"#!/bin/sh\necho '{private_value}'\n", encoding="utf-8")
        stub.chmod(0o755)

    def env_with_stub(self) -> dict:
        return {"PATH": f"{self.stub_dir}{os.pathsep}{os.environ['PATH']}"}

    def test_public_repo_via_gh_api_is_scanned(self):
        self.write_stub("false")
        f = Path(self.tmp.name) / "body.md"
        f.write_text("clean text\n", encoding="utf-8")
        r = run(str(f), "--repo", "other/pub", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("clean", r.stdout)

    def test_private_repo_via_gh_api_is_skipped(self):
        self.write_stub("true")
        f = Path(self.tmp.name) / "body.md"
        f.write_text("clean text\n", encoding="utf-8")
        r = run(str(f), "--repo", "other/priv", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not public", r.stdout)

    def test_gh_unavailable_assumes_public_rather_than_silently_skipping(self):
        f = Path(self.tmp.name) / "body.md"
        f.write_text("clean text\n", encoding="utf-8")
        no_gh_path = str(Path(self.tmp.name) / "empty-bin")
        os.makedirs(no_gh_path, exist_ok=True)
        r = run(str(f), "--repo", "other/pub", root=self.root, env={"PATH": no_gh_path})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("scanning anyway", r.stderr)
        self.assertIn("clean", r.stdout)


if __name__ == "__main__":
    unittest.main()
