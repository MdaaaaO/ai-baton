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
import kb  # noqa: E402
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
        # the value itself is never printed — only the kind and a redacted (leak_shapes.redact()) prefix
        self.assertNotIn("widgets-app", r.stdout)
        self.assertIn("wi…", r.stdout)

    def test_session_footer_embedding_a_lane_tracker_key_lower_cased_is_a_hit(self):
        # #357 second-sweep note: tracker keys lower-cased inside a lane name, in the session footer
        key = "KEY" + "-42"
        f = self.write(f"work continues.\n\nsession `{key.lower()}-migration`\n")
        r = run(str(f), "--repo", "other/pub", "--public", root=self.root)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn(key.lower(), r.stdout.lower())
        self.assertIn("ke…", r.stdout.lower())

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
        # #357 review: never the value verbatim — redacted (leak_shapes.redact()) instead
        self.assertNotIn("acmecorp/widgets-app", r.stdout)
        self.assertNotIn("widgets-app", r.stdout)


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


class SameOwnerOrgPath(unittest.TestCase):
    """An `<org>/<repo>` path naming THIS environment's own `github.org` is a hit only when `<repo>` is not
    itself a public repo of that org — `cross_org_shapes` already carries the different-org case, this is the
    `github.org `<org>/<repo>` path` shape `configured_values` adds. A stub `gh` on PATH answers the per-repo
    lookup (`repo_is_public`'s own `gh api repos/<o>/<x> --jq .private` call), never the network; the `--repo`
    under test is always forced `--public` so only the repo-lookup under test touches the stub."""

    ORG = "acme" + "corp"  # assembled: this file is itself scanned by the kit's own leak check

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        write_config(self.root, {"environment": "t", "tracker": {"kind": "github", "repos": []},
                                 "github": {"org": self.ORG}})
        self.stub_dir = Path(self.tmp.name) / "stubbin"
        self.stub_dir.mkdir()
        self.call_log = Path(self.tmp.name) / "calls.log"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str) -> Path:
        p = Path(self.tmp.name) / "body.md"
        p.write_text(text, encoding="utf-8")
        return p

    def write_stub(self) -> None:
        # real `gh api repos/o/x --jq .private` prints true/false on stdout and exits 0; a repo it cannot
        # resolve exits non-zero with an error on stderr (here: a 404, the shape of a repo this token can't see)
        stub = self.stub_dir / "gh"
        stub.write_text(
            "#!/bin/sh\n"
            'echo "$2" >> "$CALL_LOG"\n'
            'case "$2" in\n'
            "  */pubrepo) echo false ;;\n"
            "  */privrepo) echo true ;;\n"
            '  */flakyrepo) echo "gh: Not Found (HTTP 404)" 1>&2; exit 1 ;;\n'
            "  *) echo false ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

    def env_with_stub(self) -> dict:
        return {"PATH": f"{self.stub_dir}{os.pathsep}{os.environ['PATH']}", "CALL_LOG": str(self.call_log)}

    def test_same_owner_public_repo_passes(self):
        self.write_stub()
        f = self.write(f"see {self.ORG}/pubrepo for details\n")
        r = run(str(f), "--repo", f"{self.ORG}/target", "--public", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("clean", r.stdout)

    def test_a_closing_period_or_git_suffix_is_not_part_of_the_repo_name(self):
        self.write_stub()
        f = self.write(f"the store side is {self.ORG}/pubrepo.\nclone {self.ORG}/pubrepo.git\n")
        r = run(str(f), "--repo", f"{self.ORG}/target", "--public", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = [ln for ln in self.call_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(calls, [f"repos/{self.ORG}/pubrepo"])

    def test_a_team_handle_stays_a_hit_even_when_a_public_repo_has_its_name(self):
        self.write_stub()
        f = self.write(f"cc @{self.ORG}/pubrepo for a look\n")
        r = run(str(f), "--repo", f"{self.ORG}/target", "--public", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ac…", r.stdout)
        self.assertFalse(self.call_log.exists(), "a team handle is never looked up as a repo")

    def test_a_public_tracker_repo_of_the_same_owner_passes(self):
        write_config(self.root, {"environment": "t", "github": {"org": self.ORG},
                                 "tracker": {"kind": "github", "repos": [f"{self.ORG}/pubrepo"]}})
        self.write_stub()
        f = self.write(f"tracked in {self.ORG}/pubrepo, see https://github.com/{self.ORG}/pubrepo/issues/7\n")
        r = run(str(f), "--repo", f"{self.ORG}/target", "--public", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = [ln for ln in self.call_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(calls, [f"repos/{self.ORG}/pubrepo"])

    def test_a_private_tracker_repo_of_the_same_owner_fails(self):
        write_config(self.root, {"environment": "t", "github": {"org": self.ORG},
                                 "tracker": {"kind": "github", "repos": [f"{self.ORG}/privrepo"]}})
        self.write_stub()
        f = self.write(f"tracked in {self.ORG}/privrepo\n")
        r = run(str(f), "--repo", f"{self.ORG}/target", "--public", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("tracker.repos", r.stdout)
        self.assertNotIn("lookup failed", r.stdout)

    def test_a_tracker_repo_slug_is_unresolved_for_a_foreign_target(self):
        # no stub on PATH: the same-owner lookup must not run when the text goes to another org's repo
        write_config(self.root, {"environment": "t", "github": {"org": self.ORG},
                                 "tracker": {"kind": "github", "repos": [f"{self.ORG}/pubrepo"]}})
        f = self.write(f"tracked in {self.ORG}/pubrepo\n")
        r = run(str(f), "--repo", "other" + "org/project", "--public", root=self.root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("tracker.repos", r.stdout)
        self.assertNotIn("lookup failed", r.stdout)

    def test_same_owner_private_repo_fails(self):
        self.write_stub()
        f = self.write(f"see {self.ORG}/privrepo for details\n")
        r = run(str(f), "--repo", f"{self.ORG}/target", "--public", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ac…", r.stdout)
        self.assertNotIn("lookup failed", r.stdout)

    def test_lookup_failure_keeps_the_hit_and_says_so(self):
        self.write_stub()
        f = self.write(f"see {self.ORG}/flakyrepo for details\n")
        r = run(str(f), "--repo", f"{self.ORG}/target", "--public", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("lookup failed", r.stdout)

    def test_foreign_org_target_is_unaffected(self):
        # posting to a DIFFERENT org's repo: this environment's own org path is still a hit, same-owner
        # exception does not apply, and the lookup it would need is never run (no stub on PATH here)
        f = self.write(f"see {self.ORG}/pubrepo for details\n")
        r = run(str(f), "--repo", "other" + "org/project", "--public", root=self.root)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ac…", r.stdout)
        self.assertNotIn("lookup failed", r.stdout)

    def test_lookup_runs_once_per_distinct_repo(self):
        self.write_stub()
        f = self.write(f"see {self.ORG}/pubrepo twice: {self.ORG}/pubrepo again\n")
        r = run(str(f), "--repo", f"{self.ORG}/target", "--public", root=self.root, env=self.env_with_stub())
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = [ln for ln in self.call_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(calls, [f"repos/{self.ORG}/pubrepo"])


class LoaderErrors(unittest.TestCase):
    """#357 review: a loader/value-set error must never fall through to exit 0 "clean" — that is a false
    negative dressed up as a clean scan. `kb.all_facts()` failing is exit 3, distinct from exit 0 (clean) and
    exit 1 (hits); every calling skill treats exit 3 like a hit — do not post. In-process (not subprocess,
    unlike the rest of this file) so `kb.all_facts` can be stubbed directly — the same pattern
    `tests/test_kb_store.py` uses to point kit_profile at a throw-away store without touching the workspace's."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        write_config(self.root, {"environment": "t", "tracker": {"repos": []}})
        self.body = Path(self.tmp.name) / "body.md"
        self.body.write_text("nothing sensitive here\n", encoding="utf-8")
        self._saved_env_dir = kit_profile.ENV_DIR
        self._saved_all_facts = kb.all_facts
        kit_profile.ENV_DIR = self.root / "reference" / "env"
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        kb.all_facts = lambda *a, **k: (_ for _ in ()).throw(ValueError("bad table"))

    def tearDown(self):
        kb.all_facts = self._saved_all_facts
        kit_profile.ENV_DIR = self._saved_env_dir
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.tmp.cleanup()

    def test_loader_error_is_exit_3_not_exit_0(self):
        rc = kit_profile.public_text_check(str(self.body), "other/pub", public=True)
        self.assertEqual(rc, 3)

    def test_loader_error_prints_a_one_line_reason_never_clean(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = kit_profile.public_text_check(str(self.body), "other/pub", public=True)
        self.assertEqual(rc, 3)
        self.assertNotIn("clean", out.getvalue())
        self.assertIn("could not load the env store's values — not checked", err.getvalue())


if __name__ == "__main__":
    unittest.main()
