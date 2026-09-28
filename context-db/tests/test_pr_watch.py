"""pr-watch.sh against a stub `gh` on PATH — no network. One process, one PR, whose fake `gh pr view --json state`
answers MERGED from the first cycle so the round-robin exits after exactly one poll (no real `sleep 120`) while
every event the poll body can emit still runs first. Covers three regressions:
  - a legacy commit status (StatusContext — no `status`/`conclusion` of its own, only `state`) in
    statusCheckRollup used to be silently dropped: neither counted as pending nor as bad, so a PR whose only
    red signal was an external CI status was reported green (or reported red before a still-pending one settled).
  - a failed comments/reviews page (rate limit, a transient 5xx) used to be indistinguishable from "no new
    items" — the loop just saw an empty command substitution and moved on in silence.
  - a PR approved only by the configured review bot or by `github-actions[bot]` (the identity auto-merge.yml's
    own approval carries) used to count as a blocking human approval, so a bot-approved BEHIND PR never got
    auto-synced and so never reached a mergeable state.
  - with neither PR_WATCH_SELF nor WORKSPACE_GITHUB_LOGIN set and `gh api user` failing, the watcher used to
    fall back to the literal string "unknown" and keep running — every reply this session posted then came
    back as a NEW event forever.
Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SCRIPT = KIT / "skills" / "pr-watch" / "pr-watch.sh"
REPO = "o/r"
PR = "7"
FULL = "a" * 40
HEAD9 = FULL[:9]

GH_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_LOG"

if [ "$1" = api ]; then
  shift
  method=GET; path=""; jqf=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -X) method=$2; shift 2 ;;
      --paginate) shift ;;
      --jq) jqf=$2; shift 2 ;;
      -f|-F) shift 2 ;;
      *) [ -z "$path" ] && path=$1; shift ;;
    esac
  done
  case "$path" in
    user)
      [ -n "${STUB_ME_FAIL:-}" ] && { echo "stub gh: simulated identity failure" >&2; exit 1; }
      body=$STUB_ME_JSON ;;
    */pulls/*/reviews\?per_page=100)
      [ -n "${STUB_REVIEWS_FAIL:-}" ] && { echo "stub gh: simulated reviews failure" >&2; exit 1; }
      body=$STUB_REVIEWS_JSON ;;
    */pulls/*/comments\?per_page=100)
      [ -n "${STUB_COMMENTS_FAIL:-}" ] && { echo "stub gh: simulated comments failure" >&2; exit 1; }
      body=$STUB_COMMENTS_JSON ;;
    */issues/*/comments\?per_page=100)
      [ -n "${STUB_ISSUE_FAIL:-}" ] && { echo "stub gh: simulated issue-comments failure" >&2; exit 1; }
      body=$STUB_ISSUE_COMMENTS_JSON ;;
    */pulls/*/update-branch)
      [ "$method" = PUT ] || { echo "stub gh: unexpected method $method for $path" >&2; exit 9; }
      [ -n "${STUB_UPDATE_BRANCH_FAIL:-}" ] && { echo "${STUB_UPDATE_BRANCH_FAIL}" >&2; exit 1; }
      exit 0 ;;
    */compare/*) body=$STUB_COMPARE_JSON ;;
    */commits/*) body=$STUB_COMMIT_JSON ;;
    */pulls/*) body=$STUB_PR_JSON ;;
    *) echo "stub gh: unhandled api path: $path" >&2; exit 9 ;;
  esac
  if [ -n "$jqf" ]; then printf '%s' "$body" | jq -r "$jqf"
  else printf '%s\n' "$body"
  fi
  exit 0
fi

if [ "$1" = pr ] && [ "$2" = view ]; then
  shift 2
  n=$1; shift
  json=""; jqf=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) shift 2 ;;
      --json) json=$2; shift 2 ;;
      --jq|-q) jqf=$2; shift 2 ;;
      *) shift ;;
    esac
  done
  case "$json" in
    statusCheckRollup) body="{\"statusCheckRollup\": $STUB_ROLLUP_JSON}" ;;
    state) body="{\"state\": \"$STUB_STATE\"}" ;;
    *) echo "stub gh: unhandled pr view --json $json" >&2; exit 9 ;;
  esac
  printf '%s' "$body" | jq -r "$jqf"
  exit 0
fi

echo "stub gh: unexpected call: $*" >&2
exit 9
"""


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class PrWatchStub(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prwatch-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        env_dir = self.tmp / "ws" / ".context" / "reference" / "env"
        env_dir.mkdir(parents=True)
        (env_dir / "config.json").write_text(json.dumps({"github": {"review_bot": "", "sandbox_token_prefix": ""}}))
        self.log = self.tmp / "gh.log"

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def run_watch(self, *, state="MERGED", rollup="[]", reviews="[]", comments="[]", issue_comments="[]",
                  compare_behind=0, me_login="tester", identity_env: dict | None = None,
                  extra_env: dict | None = None, timeout=30) -> subprocess.CompletedProcess:
        drop = ("PR_WATCH_SELF", "WORKSPACE_GITHUB_LOGIN", "PR_WATCH_BOT_LOGIN", "GH_TOKEN", "PR_WATCH_SYNC_COOLDOWN")
        env = {k: v for k, v in os.environ.items() if k not in drop}
        env.update(
            PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            CONTEXT_ROOT=str(self.tmp / "ws" / ".context"),
            STUB_LOG=str(self.log),
            STUB_STATE=state,
            STUB_ROLLUP_JSON=rollup,
            STUB_REVIEWS_JSON=reviews,
            STUB_COMMENTS_JSON=comments,
            STUB_ISSUE_COMMENTS_JSON=issue_comments,
            STUB_COMPARE_JSON=json.dumps({"behind_by": compare_behind}),
            STUB_PR_JSON=json.dumps({"head": {"sha": FULL}, "base": {"ref": "main"}, "mergeable_state": "clean"}),
            STUB_COMMIT_JSON=json.dumps({"parents": [{"sha": "b" * 40}], "committer": {"login": "someone"},
                                         "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}),
            STUB_ME_JSON=json.dumps({"login": me_login}),
            PR_WATCH_SYNC_COOLDOWN="0",
        )
        if identity_env is not None:
            env.update(identity_env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(["bash", str(SCRIPT), REPO, PR, HEAD9], env=env, capture_output=True, text=True, timeout=timeout)

    @staticmethod
    def check_run(name: str, conclusion: str) -> dict:
        return {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": conclusion, "name": name}

    @staticmethod
    def status_context(context: str, state: str) -> dict:
        return {"__typename": "StatusContext", "state": state, "context": context}

    # --- #128: legacy commit statuses in statusCheckRollup ---

    def test_a_failing_legacy_status_is_reported_as_check_not_green(self):
        rollup = [self.check_run("unit-tests", "SUCCESS"), self.status_context("external-ci/lint", "FAILURE")]
        r = self.run_watch(rollup=json.dumps(rollup), identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} CHECK NOT GREEN:", r.stdout)
        self.assertIn("external-ci/lint: FAILURE", r.stdout)

    def test_a_pending_legacy_status_holds_off_the_verdict(self):
        # a real check run already failed, but the legacy status has not settled — the line must wait for it,
        # not fire early on the check run alone.
        rollup = [self.check_run("unit-tests", "FAILURE"), self.status_context("external-ci/lint", "PENDING")]
        r = self.run_watch(rollup=json.dumps(rollup), identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("CHECK NOT GREEN", r.stdout)

    # --- #160: a failed page must never read as "no new comments" ---

    def test_a_failed_comments_page_is_reported_not_silently_skipped(self):
        r = self.run_watch(identity_env={"PR_WATCH_SELF": "tester"}, extra_env={"STUB_COMMENTS_FAIL": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"ERROR {REPO}#{PR}", r.stdout)
        self.assertIn("simulated comments failure", r.stdout)

    def test_unresolvable_identity_exits_2_never_falls_back_to_unknown(self):
        r = self.run_watch(me_login="", extra_env={"STUB_ME_FAIL": "1"}, identity_env={})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("ERROR", r.stdout)
        self.assertNotIn("unknown", r.stdout)

    # --- #225: a bot-only approval must not block the auto-sync ---

    def test_an_approval_by_the_auto_merge_identity_alone_still_syncs(self):
        reviews = [{"user": {"login": "github-actions[bot]"}, "state": "APPROVED", "commit_id": FULL}]
        r = self.run_watch(reviews=json.dumps(reviews), compare_behind=3, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} SYNCED with main", r.stdout)
        self.assertNotIn("but APPROVED", r.stdout)

    def test_a_human_approval_still_blocks_the_sync(self):
        reviews = [{"user": {"login": "carol"}, "state": "APPROVED", "commit_id": FULL}]
        r = self.run_watch(reviews=json.dumps(reviews), compare_behind=3, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("but APPROVED", r.stdout)
        self.assertNotIn("SYNCED", r.stdout)


if __name__ == "__main__":
    unittest.main()
