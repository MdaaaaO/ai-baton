"""fetch-context.sh / submit-review.sh / reply-threads.sh against a stub `gh` on PATH — no network. Covers
two fetch-context.sh regressions (#159):
  - a filename word (built from the PR's touched files, fed to the KB-traps grep) carrying a regex
    metacharacter used to be spliced straight into a live awk pattern; an unbalanced one (a filename cut at
    its first "." can easily leave one) made the pattern invalid and awk aborted mid-file, silently dropping
    every KB section after the break — never just the one section the bad word was for.
  - a blob fetch that failed (rate-limited or otherwise) was never retried and never recorded: the file was
    simply absent from head/base with no entry anywhere a caller could discover, indistinguishable from
    "unchanged".
And one submit-review.sh / reply-threads.sh regression (#156): the bare-tracker-key lint used
`kit_profile.py get tracker.kind` to decide whether to run at all — gated on one tracker kind even though the
lint itself keys off the generic `tracker.key_regex` (so a non-Jira tracker with a configured key format got no
lint), and folded ANY nonzero exit from that lookup (a broken store, not just "unset") into "skip the lint",
posting with unlinted keys instead of stopping.
Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
FETCH = KIT / "skills" / "pr-review" / "scripts" / "fetch-context.sh"
SUBMIT = KIT / "skills" / "pr-review" / "scripts" / "submit-review.sh"
REPLY = KIT / "skills" / "pr-review" / "scripts" / "reply-threads.sh"
REPO = "owner/proj"
PR = "9"
HEAD = "a" * 40
BASE = "b" * 40

PR_JSON = json.dumps({
    "head": {"sha": HEAD, "ref": "feature"}, "base": {"sha": BASE, "ref": "main", "repo": {"default_branch": "main"}},
    "title": "t", "state": "open", "user": {"login": "author"}, "draft": False, "mergeable_state": "clean",
    "additions": 1, "deletions": 0, "changed_files": 1, "html_url": "https://example.test/pr/9", "body": "",
    "labels": [], "requested_reviewers": [], "requested_teams": [], "updated_at": "2026-01-01T00:00:00Z",
})

GH_STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
[ "$1" = api ] || { echo "stub gh: unexpected call: $*" >&2; exit 9; }
shift
path=""; accept=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) shift 2 ;;
    --paginate) shift ;;
    -H) accept=$2; shift 2 ;;
    -f|-F) shift 2 ;;
    --input) shift 2 ;;
    *) [ -z "$path" ] && path=$1; shift ;;
  esac
done
case "$path" in
  graphql) printf '%s\n' "$STUB_GRAPHQL_JSON"; exit 0 ;;
  */pulls/*/files\?per_page=100) printf '%s\n' "$STUB_FILES_JSON"; exit 0 ;;
  */pulls/*/reviews\?per_page=100) printf '%s\n' "${STUB_REVIEWS_JSON:-[]}"; exit 0 ;;
  */pulls/*/comments\?per_page=100) printf '%s\n' "${STUB_REVIEW_COMMENTS_JSON:-[]}"; exit 0 ;;
  */issues/*/comments\?per_page=100) printf '%s\n' "${STUB_ISSUE_COMMENTS_JSON:-[]}"; exit 0 ;;
  */commits/*/check-runs\?per_page=100) printf '%s\n' "${STUB_CHECKRUNS_JSON:-{\"check_runs\":[]\}}"; exit 0 ;;
  */commits/*/status) printf '%s\n' "${STUB_STATUS_JSON:-{\"state\":\"success\",\"total_count\":0,\"statuses\":[]\}}"; exit 0 ;;
  */contents/*)
    f="${path#*/contents/}"; f="${f%%\?*}"
    key=$(printf '%s' "$f" | tr -c 'A-Za-z0-9' '_')
    ctr="$STUB_COUNTERS_DIR/$key"
    n=0; [ -f "$ctr" ] && n=$(cat "$ctr"); n=$((n + 1)); echo "$n" > "$ctr"
    case "$f" in
      *gone*) echo "error: simulated 429 rate limit" >&2; exit 1 ;;
      *flaky*) if [ "$n" -lt 3 ]; then echo "error: simulated 429 rate limit" >&2; exit 1; else printf 'flaky-content'; fi ;;
      *) printf 'content-of-%s' "$f" ;;
    esac
    exit 0 ;;
  */pulls/*)
    if [ "$accept" = "Accept: application/vnd.github.diff" ]; then printf '%s' "${STUB_DIFF:-}"
    else printf '%s\n' "$STUB_PR_JSON"; fi
    exit 0 ;;
  *) echo "stub gh: unhandled api path: $path" >&2; exit 9 ;;
esac
"""

EMPTY_THREADS_GRAPHQL = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
    "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}})
MATCHING_THREAD_GRAPHQL = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
    "pageInfo": {"hasNextPage": False, "endCursor": None},
    "nodes": [{"id": "PRRT_x", "isResolved": False, "path": "a.txt", "line": 1,
               "comments": {"nodes": [{"author": {"login": "author"}, "body": "orig"}]}}]}}}}})


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class FetchContextStub(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-fetchctx-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        self.env_dir = self.tmp / "ws" / ".context" / "reference" / "env"
        self.env_dir.mkdir(parents=True)
        self.env_dir.joinpath("config.json").write_text(json.dumps(
            {"github": {"review_bot": "", "sandbox_token_prefix": ""}}))
        self.state_dir = self.tmp / "ws" / ".context" / "state" / "pr-review"
        self.state_dir.mkdir(parents=True)
        self.state_dir.joinpath("config.json").write_text(json.dumps(
            {"login": "tester", "bots": [], "bundle_max_files": 60, "bundle_max_kb": 200}))
        self.log = self.tmp / "gh.log"
        self.counters = self.tmp / "counters"
        self.counters.mkdir()

    def run_fetch(self, files, kb_content=None, extra_env=None):
        if kb_content is not None:
            kb_dir = self.tmp / "ws" / ".context" / "pr-reviews"
            kb_dir.mkdir(parents=True, exist_ok=True)
            kb_dir.joinpath(f"{REPO.split('/')[-1]}.md").write_text(kb_content)
        env = {k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "PR_REVIEW_HOME")}
        env.update(
            PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            CONTEXT_ROOT=str(self.tmp / "ws" / ".context"),
            STUB_LOG=str(self.log),
            STUB_PR_JSON=PR_JSON,
            STUB_FILES_JSON=json.dumps(files),
            STUB_GRAPHQL_JSON=EMPTY_THREADS_GRAPHQL,
            STUB_COUNTERS_DIR=str(self.counters),
            PR_REVIEW_FETCH_RETRY_DELAY="0",
        )
        if extra_env:
            env.update(extra_env)
        out = self.tmp / "out"
        r = subprocess.run(["bash", str(FETCH), REPO, PR, "--out", str(out)],
                            env=env, capture_output=True, text=True, timeout=60)
        return r, out

    # --- #159: a filename word with regex metacharacters must not break (or silently truncate) the KB grep ---

    def test_kb_traps_matches_a_word_with_unbalanced_regex_metacharacters(self):
        kb = ("## Known traps\nalways kept\n\n"
              "## foo(bar edge cases\nthis section is about a legacy parser\n")
        files = [{"filename": "src/foo(bar.txt", "status": "added", "changes": 1, "additions": 1, "deletions": 0}]
        r, out = self.run_fetch(files, kb_content=kb)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        traps = (out / "kb-traps.md").read_text()
        self.assertIn("Known traps", traps)
        self.assertIn("foo(bar edge cases", traps)
        self.assertIn("legacy parser", traps)

    # --- #159: a permanently failing blob fetch must be retried, then recorded — never just absent ---

    def test_flaky_blob_recovers_after_retries_and_gone_blob_is_recorded_skipped(self):
        files = [
            {"filename": "flaky.txt", "status": "added", "changes": 1, "additions": 1, "deletions": 0},
            {"filename": "gone.txt", "status": "added", "changes": 1, "additions": 1, "deletions": 0},
            {"filename": "ok.txt", "status": "added", "changes": 1, "additions": 1, "deletions": 0},
        ]
        r, out = self.run_fetch(files)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((out / "head" / "flaky.txt").read_text(), "flaky-content")
        self.assertEqual((out / "head" / "ok.txt").read_text(), "content-of-ok.txt")
        self.assertFalse((out / "head" / "gone.txt").exists())
        skipped = (out / "skipped.tsv").read_text()
        self.assertIn("gone.txt", skipped)
        self.assertIn("fetch-failed", skipped)
        self.assertNotIn("flaky.txt", skipped)  # it recovered — not a skip
        bundle = json.loads((out / "bundle.json").read_text())
        self.assertEqual(bundle["bundle"]["skipped_detail"], str(out / "skipped.tsv"))
        self.assertGreaterEqual(bundle["bundle"]["skipped"], 1)
        self.assertIn("skipped", r.stdout)


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class TrackerKeyLint(unittest.TestCase):
    """#156: submit-review.sh / reply-threads.sh preview, against a real (non-stub) kit_profile.py — the store
    is small and valid, no network is reached before the lint fires (a bad request is refused before step 2)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-trackerlint-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env_dir = self.tmp / "ws" / ".context" / "reference" / "env"
        self.env_dir.mkdir(parents=True)
        self.state_dir = self.tmp / "ws" / ".context" / "state" / "pr-review"
        self.state_dir.mkdir(parents=True)
        self.state_dir.joinpath("config.json").write_text(json.dumps({
            "login": "tester", "footer": "", "auto_approve": {"mode": "off"}, "submitted_retention_days": 14,
        }))
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)

    def write_env_config(self, **tracker):
        self.env_dir.joinpath("config.json").write_text(json.dumps({
            "github": {"sandbox_token_prefix": ""}, "tracker": tracker,
        }))

    def base_env(self):
        return {
            **{k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "PR_REVIEW_HOME")},
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "CONTEXT_ROOT": str(self.tmp / "ws" / ".context"),
            "STUB_LOG": str(self.tmp / "gh.log"),
            "STUB_PR_JSON": json.dumps({"head": {"sha": HEAD}, "base": {"sha": BASE}, "state": "open",
                                         "user": {"login": "author"}}),
            "STUB_FILES_JSON": "[]",
            "STUB_GRAPHQL_JSON": EMPTY_THREADS_GRAPHQL,
        }

    def write_request(self, name, payload) -> str:
        p = self.tmp / name
        p.write_text(json.dumps(payload))
        return str(p)

    # --- item 2: the gate must key off tracker.key_regex, not off `kind == "jira"` ---

    def test_submit_review_lints_a_bare_key_on_a_non_jira_tracker(self):
        # a generic (non-Jira) tracker kind whose key format still needs a link — assembled, not a real vendor name
        kind = "".join(["generic", "-tracker"])
        key_regex = "[A-Z]{3,}-[0-9]+"
        self.write_env_config(kind=kind, key_regex=key_regex, url_template="https://tracker.example/{key}")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "see ABC-123 for details", "comments": []})
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=self.base_env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("bare tracker key", r.stderr)

    def test_reply_threads_lints_a_bare_key_on_a_non_jira_tracker(self):
        kind = "".join(["generic", "-tracker"])
        key_regex = "[A-Z]{3,}-[0-9]+"
        self.write_env_config(kind=kind, key_regex=key_regex, url_template="https://tracker.example/{key}")
        req = self.write_request("req.json", {"replies": [
            {"thread_id": "PRRT_x", "body": "see ABC-123 for details", "resolve": False}]})
        r = subprocess.run(["bash", str(REPLY), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=self.base_env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("bare tracker key", r.stderr)

    # --- a GitHub tracker auto-links `#123` — the lint must not fire there, no matter what key_regex says ---

    def test_submit_review_does_not_refuse_a_github_hash_reference(self):
        key_regex = r"(?:^|[^\w/])#(\d+)\b"  # matches a bare #123 too — the kind must gate it out on GitHub
        self.write_env_config(kind="github", key_regex=key_regex)
        req = self.write_request("req.json", {"event": "COMMENT", "body": "see #123 for details", "comments": []})
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=self.base_env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("digest:", r.stdout)

    def test_reply_threads_does_not_refuse_a_github_hash_reference(self):
        key_regex = r"(?:^|[^\w/])#(\d+)\b"
        self.write_env_config(kind="github", key_regex=key_regex)
        req = self.write_request("req.json", {"replies": [
            {"thread_id": "PRRT_x", "body": "see #123 for details", "resolve": False}]})
        env = self.base_env(); env["STUB_GRAPHQL_JSON"] = MATCHING_THREAD_GRAPHQL
        r = subprocess.run(["bash", str(REPLY), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("digest:", r.stdout)

    # --- item 1: an unreadable lookup (not just "absent") must stop the script, never post unlinted ---

    def _python_shim(self, intercept_keys, rc=2, stderr_msg="kit_profile: simulated store I/O error (test)"):
        real = sys.executable
        shim_dir = self.tmp / "pyshim"
        shim_dir.mkdir(exist_ok=True)
        shim = shim_dir / "python3"
        conds = " || ".join(f'[[ "$*" == *"get {k}"* ]]' for k in intercept_keys)
        shim.write_text(f"""#!/usr/bin/env bash
if {conds}; then
  printf '%s\\n' "{stderr_msg}" >&2
  exit {rc}
fi
exec "{real}" "$@"
""")
        shim.chmod(0o755)
        return shim_dir

    def test_submit_review_stops_when_the_key_regex_lookup_itself_fails(self):
        self.write_env_config(kind="jira", key_regex="[A-Z]{3,}-[0-9]+", url_template="https://tracker.example/{key}")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "see ABC-123 for details", "comments": []})
        shim_dir = self._python_shim(["tracker.kind", "tracker.key_regex"])
        env = self.base_env()
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("digest:", r.stdout)  # never got to a printed preview
        self.assertIn("unreadable", r.stderr)

    def test_reply_threads_stops_when_the_key_regex_lookup_itself_fails(self):
        self.write_env_config(kind="jira", key_regex="[A-Z]{3,}-[0-9]+", url_template="https://tracker.example/{key}")
        req = self.write_request("req.json", {"replies": [
            {"thread_id": "PRRT_x", "body": "see ABC-123 for details", "resolve": False}]})
        shim_dir = self._python_shim(["tracker.kind", "tracker.key_regex"])
        env = self.base_env()
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        r = subprocess.run(["bash", str(REPLY), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("digest:", r.stdout)
        self.assertIn("unreadable", r.stderr)

    # --- rc 1 is ambiguous: kit_profile.py also exits 1 on its Python-floor guard and on an uncaught traceback.
    # rc 1 with stderr must be treated as a real failure, not folded into "absent" the way an empty-stderr rc 1 is. ---

    def test_submit_review_treats_rc1_with_stderr_as_fatal(self):
        self.write_env_config(kind="jira", key_regex="[A-Z]{3,}-[0-9]+", url_template="https://tracker.example/{key}")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "see ABC-123 for details", "comments": []})
        shim_dir = self._python_shim(["tracker.kind"], rc=1, stderr_msg="Traceback (most recent call last):\nSomeError: boom")
        env = self.base_env(); env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("digest:", r.stdout)
        self.assertIn("unreadable", r.stderr)

    def test_reply_threads_treats_rc1_with_stderr_as_fatal(self):
        self.write_env_config(kind="jira", key_regex="[A-Z]{3,}-[0-9]+", url_template="https://tracker.example/{key}")
        req = self.write_request("req.json", {"replies": [
            {"thread_id": "PRRT_x", "body": "see ABC-123 for details", "resolve": False}]})
        shim_dir = self._python_shim(["tracker.kind"], rc=1, stderr_msg="Traceback (most recent call last):\nSomeError: boom")
        env = self.base_env(); env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        r = subprocess.run(["bash", str(REPLY), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("digest:", r.stdout)
        self.assertIn("unreadable", r.stderr)

    # --- no env store at all is a documented, graceful state ("kit defaults apply") — never an error. `get`
    # says so with a stderr warning + exit 1; `source` (checked first, no warning) tells the two states apart. ---

    def test_submit_review_runs_without_a_store_skipping_the_lint(self):
        # setUp's env_dir has no config.json under it at all — no store, by construction
        req = self.write_request("req.json", {"event": "COMMENT", "body": "see ABC-123 for details", "comments": []})
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=self.base_env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("digest:", r.stdout)

    def test_reply_threads_runs_without_a_store_skipping_the_lint(self):
        req = self.write_request("req.json", {"replies": [
            {"thread_id": "PRRT_x", "body": "see ABC-123 for details", "resolve": False}]})
        env = self.base_env(); env["STUB_GRAPHQL_JSON"] = MATCHING_THREAD_GRAPHQL
        r = subprocess.run(["bash", str(REPLY), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("digest:", r.stdout)

    # --- a store that DOES exist but is broken (unreadable/invalid JSON) is still fatal, no-store or not ---

    def test_submit_review_a_broken_store_is_still_fatal(self):
        self.env_dir.joinpath("config.json").write_text("{not valid json")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "see ABC-123 for details", "comments": []})
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=self.base_env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("digest:", r.stdout)

    def test_reply_threads_a_broken_store_is_still_fatal(self):
        self.env_dir.joinpath("config.json").write_text("{not valid json")
        req = self.write_request("req.json", {"replies": [
            {"thread_id": "PRRT_x", "body": "see ABC-123 for details", "resolve": False}]})
        r = subprocess.run(["bash", str(REPLY), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                            "--request", req], env=self.base_env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("digest:", r.stdout)


if __name__ == "__main__":
    unittest.main()
