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
  - the watcher's own update-branch merge used to come back as a HEAD MOVED wake-up (after the SYNCED one had
    already said so), and the review-bot re-request that head needs was left to the session — two model turns
    for nothing the session could decide. It is now tracked silently and, in bot mode, re-requested by the
    watcher; a real push after a sync still reports HEAD MOVED, and a failed re-request is a line.
  - an expiry re-arm with the arming command reused verbatim (its original head) after the watcher had already
    followed a head move used to reset the state: HEAD MOVED again, the bot verdict replayed.
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
    */pulls/*/requested_reviewers)
      [ -n "${STUB_RR_FAIL:-}" ] && { echo "stub gh: simulated re-request failure" >&2; exit 1; }
      exit 0 ;;
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
        self.env_dir = self.tmp / "ws" / ".context" / "reference" / "env"
        self.env_dir.mkdir(parents=True)
        self.write_config()
        self.log = self.tmp / "gh.log"

    def write_config(self, *, review_bot: str = "", bots: list | None = None) -> None:
        (self.env_dir / "config.json").write_text(json.dumps({
            "github": {"review_bot": review_bot, "sandbox_token_prefix": "",
                       "bots": ["github-actions[bot]"] if bots is None else bots},
        }))

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def run_watch(self, *, state="MERGED", rollup="[]", reviews="[]", comments="[]", issue_comments="[]",
                  compare_behind=0, me_login="tester", identity_env: dict | None = None,
                  extra_env: dict | None = None, timeout=30, live_head: str = FULL, arg_head: str = HEAD9,
                  commit: dict | None = None) -> subprocess.CompletedProcess:
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
            STUB_PR_JSON=json.dumps({"head": {"sha": live_head}, "base": {"ref": "main"}, "mergeable_state": "clean"}),
            STUB_COMMIT_JSON=json.dumps(commit if commit is not None else {
                "parents": [{"sha": "b" * 40}], "committer": {"login": "someone"},
                "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}),
            TMPDIR=str(self.tmp),  # per-test state dir, never the shared ${TMPDIR:-/tmp}/pr-watch-* of a live watcher
            STUB_ME_JSON=json.dumps({"login": me_login}),
            PR_WATCH_SYNC_COOLDOWN="0",
        )
        if identity_env is not None:
            env.update(identity_env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(["bash", str(SCRIPT), REPO, PR, arg_head], env=env, capture_output=True, text=True, timeout=timeout)

    def state_dir(self) -> Path:
        return self.tmp / f"pr-watch-{REPO.replace('/', '-')}-{PR}"

    def seed_state(self, **files: str) -> None:
        """A state dir as a previous run of the watcher left it (a re-arm, not a first arming)."""
        d = self.state_dir()
        d.mkdir(parents=True, exist_ok=True)
        base = {"init": "1", "seen_bot": "0", "seen_c": "", "seen_r": "", "seen_i": "", "last_sync": "0"}
        for name, value in {**base, **files}.items():
            (d / name).write_text(value + "\n" if value else "")

    @staticmethod
    def merge_commit(first_parent: str, committer: str = "web-flow") -> dict:
        return {"parents": [{"sha": first_parent}, {"sha": "c" * 40}], "committer": {"login": committer},
                "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}

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

    # --- the auto-merge identity must come from the configured `github.bots` list, never a hardcoded login ---

    def test_a_login_in_the_configured_bots_list_is_excluded_even_if_not_github_actions(self):
        self.write_config(bots=["custom-ci[bot]"])  # deliberately NOT github-actions[bot]
        reviews = [{"user": {"login": "custom-ci[bot]"}, "state": "APPROVED", "commit_id": FULL}]
        r = self.run_watch(reviews=json.dumps(reviews), compare_behind=3, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} SYNCED with main", r.stdout)
        self.assertNotIn("but APPROVED", r.stdout)

    def test_github_actions_is_not_hardcoded_only_the_configured_list_is_excluded(self):
        # bots list configured WITHOUT github-actions[bot]: a hardcoded exclusion would still sync here,
        # which is exactly the bug the review thread flagged — the login must come from the config, not a literal.
        self.write_config(bots=["some-other-bot[bot]"])
        reviews = [{"user": {"login": "github-actions[bot]"}, "state": "APPROVED", "commit_id": FULL}]
        r = self.run_watch(reviews=json.dumps(reviews), compare_behind=3, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("but APPROVED", r.stdout)
        self.assertNotIn("SYNCED", r.stdout)

    def test_a_missing_bots_key_is_a_startup_error_not_a_silent_empty_list(self):
        (self.env_dir / "config.json").write_text(json.dumps({"github": {"review_bot": "", "sandbox_token_prefix": ""}}))
        r = self.run_watch(identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ERROR", r.stdout)
        self.assertIn("github.bots", r.stdout)

    # --- the watcher's own update-branch: no HEAD MOVED wake-up, the bot re-requested by the watcher itself ---

    OLD = "d" * 40      # the head the watcher synced FROM
    NEW = FULL          # the merge commit update-branch landed (the live head)

    def test_a_sync_records_the_head_it_synced_from_and_promises_no_head_moved(self):
        r = self.run_watch(compare_behind=3, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} SYNCED with main", r.stdout)
        self.assertIn("no HEAD MOVED follows", r.stdout)
        self.assertEqual((self.state_dir() / "sync_from").read_text().strip(), FULL)

    def test_own_sync_merge_head_is_silent_and_the_bot_is_re_requested(self):
        self.write_config(review_bot="rev-bot[bot]")
        self.seed_state(head=self.OLD[:9], sync_from=self.OLD)
        r = self.run_watch(arg_head=self.OLD[:9], commit=self.merge_commit(self.OLD),
                           identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("HEAD MOVED", r.stdout)
        self.assertNotIn("RE-REQUEST", r.stdout)
        calls = self.calls().splitlines()
        rr = [c for c in calls if "requested_reviewers" in c]
        self.assertEqual(len(rr), 2, calls)
        self.assertIn("-X DELETE", rr[0]); self.assertIn("-X POST", rr[1])
        self.assertTrue(all("reviewers[]=rev-bot[bot]" in c for c in rr), rr)
        self.assertEqual((self.state_dir() / "head").read_text().strip(), self.NEW[:9])
        self.assertFalse((self.state_dir() / "sync_from").exists())

    def test_own_sync_merge_head_in_no_bot_mode_is_silent_and_requests_nobody(self):
        self.seed_state(head=self.OLD[:9], sync_from=self.OLD)
        r = self.run_watch(arg_head=self.OLD[:9], commit=self.merge_commit(self.OLD),
                           identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("HEAD MOVED", r.stdout)
        self.assertNotIn("requested_reviewers", self.calls())

    def test_a_failed_re_request_after_the_sync_is_a_line_not_silence(self):
        self.write_config(review_bot="rev-bot[bot]")
        self.seed_state(head=self.OLD[:9], sync_from=self.OLD)
        r = self.run_watch(arg_head=self.OLD[:9], commit=self.merge_commit(self.OLD),
                           identity_env={"PR_WATCH_SELF": "tester"}, extra_env={"STUB_RR_FAIL": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} RE-REQUEST of rev-bot[bot] on {self.NEW[:9]} failed", r.stdout)
        self.assertIn("simulated re-request failure", r.stdout)
        self.assertNotIn("HEAD MOVED", r.stdout)

    def test_a_push_after_a_sync_is_still_head_moved_and_clears_the_sync_marker(self):
        # the new head is NOT the sync's merge commit (one parent: someone pushed) — a real move, reported;
        # the marker must not linger and misclassify a later move either, and nobody is re-requested.
        self.write_config(review_bot="rev-bot[bot]")
        self.seed_state(head=self.OLD[:9], sync_from=self.OLD)
        push = {"parents": [{"sha": self.OLD}], "committer": {"login": "tester"},
                "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}
        r = self.run_watch(arg_head=self.OLD[:9], commit=push, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} HEAD MOVED to {self.NEW[:9]}", r.stdout)
        self.assertNotIn("requested_reviewers", self.calls())
        self.assertFalse((self.state_dir() / "sync_from").exists())

    def test_a_merge_head_by_someone_else_is_still_head_moved(self):
        # a merge commit whose first parent is NOT the head this watcher synced from (someone merged by hand
        # from another head) — not the watcher's own sync.
        self.seed_state(head=self.OLD[:9], sync_from="e" * 40)
        r = self.run_watch(arg_head=self.OLD[:9], commit=self.merge_commit(self.OLD),
                           identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} HEAD MOVED to {self.NEW[:9]}", r.stdout)
        self.assertFalse((self.state_dir() / "sync_from").exists())

    # --- an expiry re-arm with the original command must stay silent once the watcher followed the move ---

    def test_re_arm_with_the_original_head_after_a_followed_move_is_silent(self):
        self.write_config(review_bot="rev-bot[bot]")
        # the watcher already followed the move to NEW and reported its bot verdict there
        self.seed_state(head=self.NEW[:9], seen_bot="1")
        green = [{"user": {"login": "rev-bot[bot]"}, "state": "COMMENTED", "commit_id": self.NEW,
                  "body": "### Assessment: " + chr(0x1F7E2) + " good"}]  # the green circle, built at run time
        r = self.run_watch(arg_head=self.OLD[:9], reviews=json.dumps(green), identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("HEAD MOVED", r.stdout)
        self.assertNotIn("BOT REVIEW", r.stdout)
        self.assertEqual((self.state_dir() / "head").read_text().strip(), self.NEW[:9])


if __name__ == "__main__":
    unittest.main()
