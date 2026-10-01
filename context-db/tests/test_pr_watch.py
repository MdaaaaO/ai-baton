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
  - a human's stale review (any state, not just APPROVED) used to be dropped to stderr like a bot's — but a
    human does not automatically re-review after a push, so it is now emitted, marked with the head it is on;
    only a stale verdict from the configured review bot or a login in `github.bots` is still dropped.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import hermetic_env  # noqa: E402

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
    */compare/*)
      [ -n "${STUB_COMPARE_FAIL:-}" ] && { echo "stub gh: simulated compare failure" >&2; exit 1; }
      if [ -n "${STUB_COMPARE_FAIL_COUNT:-}" ]; then
        cf="${STUB_LOG}.compare_calls"
        n=$(( $(cat "$cf" 2>/dev/null || echo 0) + 1 )); printf '%s' "$n" >"$cf"
        if [ "$n" -le "$STUB_COMPARE_FAIL_COUNT" ]; then echo "stub gh: simulated transient compare failure" >&2; exit 1; fi
      fi
      body=$STUB_COMPARE_JSON ;;
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
    statusCheckRollup)
      [ -n "${STUB_ROLLUP_FAIL:-}" ] && { echo "stub gh: simulated rollup failure" >&2; exit 1; }
      body="{\"statusCheckRollup\": $STUB_ROLLUP_JSON}" ;;
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
            PR_WATCH_RETRY_DELAY="0",  # the retry backoff would otherwise really sleep between attempts
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
        self.assertIn(f"PR {PR} LOOKUP FAILED: review comments", r.stdout)
        self.assertIn("simulated comments failure", r.stdout)

    def test_unresolvable_identity_exits_2_never_falls_back_to_unknown(self):
        r = self.run_watch(me_login="", extra_env={"STUB_ME_FAIL": "1"}, identity_env={})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("ERROR", r.stdout)
        self.assertNotIn("unknown", r.stdout)

    # --- a failed lookup is UNKNOWN, never read as red or green; repeated red backs off additively ---

    def test_a_failed_behind_by_lookup_is_unknown_not_a_silent_skip(self):
        r = self.run_watch(extra_env={"STUB_COMPARE_FAIL": "1"}, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("LOOKUP FAILED"), 1, r.stdout)
        self.assertIn(f"PR {PR} LOOKUP FAILED: behind-by check", r.stdout)
        self.assertIn("simulated compare failure", r.stdout)
        self.assertNotIn("BEHIND", r.stdout)
        self.assertNotIn("CHECK NOT GREEN", r.stdout)

    def test_a_transient_behind_by_failure_that_recovers_on_retry_emits_nothing(self):
        r = self.run_watch(extra_env={"STUB_COMPARE_FAIL_COUNT": "1"}, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, f"PR {PR} MERGED\n")

    def test_a_failed_check_status_lookup_is_unknown_never_a_silent_green(self):
        r = self.run_watch(extra_env={"STUB_ROLLUP_FAIL": "1"}, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("LOOKUP FAILED"), 1, r.stdout)
        self.assertIn(f"PR {PR} LOOKUP FAILED: check status", r.stdout)
        self.assertIn("simulated rollup failure", r.stdout)
        self.assertNotIn("CHECK NOT GREEN", r.stdout)
        self.assertNotIn("BEHIND", r.stdout)

    def test_a_persisting_lookup_failure_is_announced_once_across_cycles(self):
        # dedup: the SAME failure persisting across cycles (two separate invocations against the same,
        # surviving state dir — like a real re-arm) announces once, not on every cycle.
        kwargs = dict(extra_env={"STUB_COMPARE_FAIL": "1"}, identity_env={"PR_WATCH_SELF": "tester"})
        r1 = self.run_watch(**kwargs)
        self.assertIn("LOOKUP FAILED", r1.stdout)
        r2 = self.run_watch(**kwargs)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertNotIn("LOOKUP FAILED", r2.stdout)

    def test_a_recovered_lookup_then_a_new_failure_is_announced_again(self):
        fail = dict(extra_env={"STUB_COMPARE_FAIL": "1"}, identity_env={"PR_WATCH_SELF": "tester"})
        ok = dict(identity_env={"PR_WATCH_SELF": "tester"})
        r1 = self.run_watch(**fail)
        self.assertIn("LOOKUP FAILED", r1.stdout)
        r2 = self.run_watch(**ok)
        self.assertNotIn("LOOKUP FAILED", r2.stdout)
        r3 = self.run_watch(**fail)
        self.assertIn("LOOKUP FAILED", r3.stdout)

    def test_check_not_green_backoff_is_silent_within_the_window_then_reannounced(self):
        rollup = json.dumps([self.check_run("unit-tests", "FAILURE")])
        self.seed_state(head=HEAD9)  # init=1 already: every call below is a re-arm, state is kept, not reset
        now0 = 1_700_000_000
        r1 = self.run_watch(rollup=rollup, identity_env={"PR_WATCH_SELF": "tester"},
                             extra_env={"PR_WATCH_NOW": str(now0)})
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        self.assertIn(f"PR {PR} CHECK NOT GREEN: unit-tests: FAILURE", r1.stdout)
        sd = self.state_dir()
        self.assertEqual((sd / "notgreen_step").read_text().strip(), "3600")
        self.assertEqual((sd / "notgreen_last").read_text().strip(), str(now0))

        # within the first 1h window: silent
        r2 = self.run_watch(rollup=rollup, identity_env={"PR_WATCH_SELF": "tester"},
                             extra_env={"PR_WATCH_NOW": str(now0 + 1800)})
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertNotIn("CHECK NOT GREEN", r2.stdout)

        # past the window: re-announced, the next window grows by +2h (1h -> 3h)
        r3 = self.run_watch(rollup=rollup, identity_env={"PR_WATCH_SELF": "tester"},
                             extra_env={"PR_WATCH_NOW": str(now0 + 3700)})
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        self.assertIn(f"PR {PR} CHECK NOT GREEN: unit-tests: FAILURE", r3.stdout)
        self.assertEqual((sd / "notgreen_step").read_text().strip(), "10800")

    def test_check_not_green_backoff_caps_at_pr_watch_backoff_max(self):
        last = 1_700_000_000
        self.seed_state(head=HEAD9, notgreen=FULL, notgreen_bad="unit-tests: FAILURE;",
                         notgreen_last=str(last), notgreen_step="82800")
        r = self.run_watch(rollup=json.dumps([self.check_run("unit-tests", "FAILURE")]),
                            identity_env={"PR_WATCH_SELF": "tester"},
                            extra_env={"PR_WATCH_NOW": str(last + 82800 + 1)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} CHECK NOT GREEN: unit-tests: FAILURE;", r.stdout)
        self.assertEqual((self.state_dir() / "notgreen_step").read_text().strip(), "86400")

    def test_a_due_check_not_green_reads_the_checks_again_and_lists_what_is_red_now(self):
        last = 1_700_000_000
        self.seed_state(head=HEAD9, notgreen=FULL, notgreen_bad="unit-tests: FAILURE;",
                         notgreen_last=str(last), notgreen_step="3600")
        r = self.run_watch(rollup=json.dumps([self.check_run("unit-tests", "SUCCESS"), self.check_run("lint", "FAILURE")]),
                            identity_env={"PR_WATCH_SELF": "tester"}, extra_env={"PR_WATCH_NOW": str(last + 3601)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} CHECK NOT GREEN: lint: FAILURE;", r.stdout)
        self.assertNotIn("unit-tests", r.stdout)

    def test_a_due_check_not_green_whose_checks_went_green_is_not_repeated(self):
        last = 1_700_000_000
        self.seed_state(head=HEAD9, notgreen=FULL, notgreen_bad="unit-tests: FAILURE;",
                         notgreen_last=str(last), notgreen_step="3600")
        r = self.run_watch(rollup=json.dumps([self.check_run("unit-tests", "SUCCESS")]),
                            identity_env={"PR_WATCH_SELF": "tester"}, extra_env={"PR_WATCH_NOW": str(last + 3601)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("CHECK NOT GREEN", r.stdout)
        self.assertIn("no longer red", r.stderr)
        self.assertFalse((self.state_dir() / "notgreen_bad").exists())

    def test_a_due_check_not_green_with_a_rerun_in_progress_waits(self):
        last = 1_700_000_000
        self.seed_state(head=HEAD9, notgreen=FULL, notgreen_bad="unit-tests: FAILURE;",
                         notgreen_last=str(last), notgreen_step="3600")
        rerun = {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None, "name": "unit-tests"}
        r = self.run_watch(rollup=json.dumps([rerun]), identity_env={"PR_WATCH_SELF": "tester"},
                            extra_env={"PR_WATCH_NOW": str(last + 3601)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("CHECK NOT GREEN", r.stdout)
        self.assertEqual((self.state_dir() / "notgreen_step").read_text().strip(), "3600")

    def test_a_lookup_failure_that_persists_is_announced_again_after_the_backoff_window(self):
        now0 = 1_700_000_000
        fail = {"STUB_COMPARE_FAIL": "1"}
        r1 = self.run_watch(extra_env={**fail, "PR_WATCH_NOW": str(now0)}, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r1.stdout.count("LOOKUP FAILED"), 1, r1.stdout)
        r2 = self.run_watch(extra_env={**fail, "PR_WATCH_NOW": str(now0 + 1800)}, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertNotIn("LOOKUP FAILED", r2.stdout)
        r3 = self.run_watch(extra_env={**fail, "PR_WATCH_NOW": str(now0 + 3601)}, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertIn(f"PR {PR} LOOKUP FAILED: behind-by check", r3.stdout)
        self.assertIn("(still failing)", r3.stdout)
        r4 = self.run_watch(extra_env={**fail, "PR_WATCH_NOW": str(now0 + 3601 + 3600)}, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertNotIn("LOOKUP FAILED", r4.stdout)

    def test_a_failed_reviews_or_issue_comments_listing_is_a_lookup_failure(self):
        for var, what in (("STUB_REVIEWS_FAIL", "reviews"), ("STUB_ISSUE_FAIL", "issue comments")):
            with self.subTest(var):
                r = self.run_watch(identity_env={"PR_WATCH_SELF": "tester"}, extra_env={var: "1", "PR_WATCH_SYNC": "0"})
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(f"PR {PR} LOOKUP FAILED: {what}", r.stdout)

    def test_no_temp_file_is_left_behind(self):
        self.run_watch(extra_env={"STUB_COMPARE_FAIL": "1"}, identity_env={"PR_WATCH_SELF": "tester"})
        left = [p.name for p in self.tmp.iterdir() if p.is_file() and p != self.log]  # mktemp writes into TMPDIR
        self.assertEqual(left, [])

    # --- #225: a bot-only approval must not block the auto-sync ---

    def test_an_approval_by_the_auto_merge_identity_alone_still_syncs(self):
        reviews = [{"user": {"login": "github-actions[bot]"}, "state": "APPROVED", "commit_id": FULL}]
        r = self.run_watch(reviews=json.dumps(reviews), compare_behind=3, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} SYNCED with main", r.stderr)  # bookkeeping, not a decision — stderr only
        self.assertNotIn("SYNCED", r.stdout)
        self.assertNotIn("but APPROVED", r.stdout)

    def test_a_human_approval_still_blocks_the_sync(self):
        reviews = [{"user": {"login": "carol"}, "state": "APPROVED", "commit_id": FULL}]
        r = self.run_watch(reviews=json.dumps(reviews), compare_behind=3, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("but APPROVED", r.stdout)
        self.assertNotIn("SYNCED", r.stdout)
        self.assertNotIn("SYNCED", r.stderr)

    # --- the auto-merge identity must come from the configured `github.bots` list, never a hardcoded login ---

    def test_a_login_in_the_configured_bots_list_is_excluded_even_if_not_github_actions(self):
        self.write_config(bots=["custom-ci[bot]"])  # deliberately NOT github-actions[bot]
        reviews = [{"user": {"login": "custom-ci[bot]"}, "state": "APPROVED", "commit_id": FULL}]
        r = self.run_watch(reviews=json.dumps(reviews), compare_behind=3, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} SYNCED with main", r.stderr)
        self.assertNotIn("SYNCED", r.stdout)
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
        self.assertNotIn("SYNCED", r.stderr)

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
        self.assertIn(f"PR {PR} SYNCED with main", r.stderr)
        self.assertIn("no HEAD MOVED follows", r.stderr)
        self.assertNotIn("SYNCED", r.stdout)
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

    # --- only the events a session can act on reach stdout; bookkeeping stays on stderr ---

    def git(self, *args: str, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, env=hermetic_env(self.tmp), check=True,
                               capture_output=True, text=True)

    def make_worktree_commit(self) -> tuple[Path, str]:
        """A real git object a `PR_WATCH_WORKTREE` check can `cat-file -e` — own-push detection shells out to
        real git, not the stubbed `gh`."""
        wt = self.tmp / "worktree"
        wt.mkdir()
        self.git("init", "-q", cwd=wt)
        (wt / "f.txt").write_text("x")
        self.git("add", ".", cwd=wt)
        self.git("commit", "-q", "-m", "x", cwd=wt)
        return wt, self.git("rev-parse", "HEAD", cwd=wt).stdout.strip()

    def test_a_422_after_the_pr_merged_in_the_gap_emits_only_merged(self):
        r = self.run_watch(compare_behind=1, extra_env={"STUB_UPDATE_BRANCH_FAIL": "HTTP 422: head ref does not exist"},
                            identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, f"PR {PR} MERGED\n")
        self.assertNotIn("BEHIND", r.stdout)

    def test_a_humans_stale_changes_requested_is_emitted_marked_with_its_head(self):
        # a human does not automatically re-review after a push, and CHANGES_REQUESTED keeps blocking the
        # merge regardless of head — dropping it would leave the session unaware the merge is still blocked.
        old_head = "c" * 40
        self.seed_state(head=HEAD9)
        reviews = [{"id": 1, "user": {"login": "alice"}, "state": "CHANGES_REQUESTED", "commit_id": old_head},
                   {"id": 2, "user": {"login": "bob"}, "state": "APPROVED", "commit_id": FULL}]
        r = self.run_watch(reviews=json.dumps(reviews), identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(
            f"PR {PR} NEW: review CHANGES_REQUESTED by alice (on older head ccccccccc); review APPROVED by bob",
            r.stdout)
        self.assertNotIn("dropping stale review", r.stderr)

    def test_a_humans_stale_commented_review_is_emitted_marked_with_its_head(self):
        old_head = "c" * 40
        self.seed_state(head=HEAD9)
        reviews = [{"id": 1, "user": {"login": "alice"}, "state": "COMMENTED", "commit_id": old_head}]
        r = self.run_watch(reviews=json.dumps(reviews), identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} NEW: review COMMENTED by alice (on older head ccccccccc)", r.stdout)
        self.assertNotIn("dropping stale review", r.stderr)

    def test_a_stale_verdict_from_a_configured_bots_login_is_dropped_to_stderr_only(self):
        self.write_config(bots=["custom-ci[bot]"])  # not the configured review bot, but still on github.bots
        old_head = "c" * 40
        self.seed_state(head=HEAD9)
        reviews = [{"id": 1, "user": {"login": "custom-ci[bot]"}, "state": "CHANGES_REQUESTED", "commit_id": old_head}]
        r = self.run_watch(reviews=json.dumps(reviews), identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("custom-ci", r.stdout)
        self.assertNotIn("NEW:", r.stdout)
        self.assertIn("dropping stale review 1 (CHANGES_REQUESTED by custom-ci[bot])", r.stderr)

    def test_an_approval_on_an_old_head_is_still_emitted_marked_with_its_head(self):
        # it keeps counting toward the merge unless the branch rule dismisses stale reviews — dropping it would
        # leave a mergeable PR waiting on a session that was never told
        old_head = "c" * 40
        self.seed_state(head=HEAD9)
        reviews = [{"id": 1, "user": {"login": "alice"}, "state": "APPROVED", "commit_id": old_head}]
        r = self.run_watch(reviews=json.dumps(reviews), identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} NEW: review APPROVED by alice (on older head ccccccccc)", r.stdout)
        self.assertNotIn("dropping stale review", r.stderr)

    def test_an_own_push_named_by_worktree_and_login_is_tracked_silently(self):
        wt, sha = self.make_worktree_commit()
        push = {"parents": [{"sha": self.OLD}], "committer": {"login": "tester"},
                "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}
        r = self.run_watch(arg_head=self.OLD[:9], live_head=sha, commit=push, me_login="tester",
                            identity_env={"PR_WATCH_SELF": "tester", "PR_WATCH_WORKTREE": str(wt)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("HEAD MOVED", r.stdout)
        self.assertIn("own push", r.stderr)
        self.assertEqual((self.state_dir() / "head").read_text().strip(), sha[:9])

    def test_a_push_with_no_worktree_hint_is_still_head_moved(self):
        push = {"parents": [{"sha": self.OLD}], "committer": {"login": "tester"},
                "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}
        r = self.run_watch(arg_head=self.OLD[:9], commit=push, identity_env={"PR_WATCH_SELF": "tester"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} HEAD MOVED to {self.NEW[:9]}", r.stdout)

    def test_a_push_by_someone_else_is_still_head_moved_even_with_a_worktree_hint(self):
        wt, sha = self.make_worktree_commit()
        push = {"parents": [{"sha": self.OLD}], "committer": {"login": "carol"},
                "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}
        r = self.run_watch(arg_head=self.OLD[:9], live_head=sha, commit=push, me_login="tester",
                            identity_env={"PR_WATCH_SELF": "tester", "PR_WATCH_WORKTREE": str(wt)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"PR {PR} HEAD MOVED to {sha[:9]}", r.stdout)

    # --- the scripted lifecycle: open, reviewed on an old head, pushed, approved on the new head, merged ---

    def test_pr_lifecycle_open_review_push_approve_merge_prints_only_actionable_lines(self):
        wt, new_head = self.make_worktree_commit()
        old_head = "c" * 40
        # init=1 but seen_r is empty: this is the first cycle the watcher actually sees review id 1, so alice's
        # CHANGES_REQUESTED from before the push is new information this cycle too (a human does not
        # automatically re-review after a push) — it fires on the same NEW line as bob's approval of the push.
        self.seed_state(head=old_head[:9])
        push = {"parents": [{"sha": old_head}], "committer": {"login": "tester"},
                "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}
        reviews = [
            {"id": 1, "user": {"login": "alice"}, "state": "CHANGES_REQUESTED", "commit_id": old_head},
            {"id": 2, "user": {"login": "bob"}, "state": "APPROVED", "commit_id": new_head},
        ]
        rollup = [self.check_run("unit-tests", "FAILURE")]
        r = self.run_watch(arg_head=old_head[:9], live_head=new_head, commit=push, reviews=json.dumps(reviews),
                            rollup=json.dumps(rollup), me_login="tester", state="MERGED",
                            identity_env={"PR_WATCH_SELF": "tester", "PR_WATCH_WORKTREE": str(wt)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, (
            f"PR {PR} CHECK NOT GREEN: unit-tests: FAILURE;\n"
            f"PR {PR} NEW: review CHANGES_REQUESTED by alice (on older head ccccccccc); review APPROVED by bob\n"
            f"PR {PR} MERGED\n"
        ))
        self.assertNotIn("HEAD MOVED", r.stdout)
        self.assertIn("own push", r.stderr)
        self.assertNotIn("dropping stale review", r.stderr)


if __name__ == "__main__":
    unittest.main()
