"""pr-merge.sh's final gate against a stub `gh` (#89): a repo that requires no review reports `reviewDecision` as "",
which a space split lost (`set -u` then killed the script before any merge). With the `|` split, an empty decision
merges only when each reviewer's latest decisive review allows it (an APPROVED on the current head, no latest
CHANGES_REQUESTED); otherwise it stops for a human. No-bot mode (the env
store's `github.review_bot` is ""), a CLEAN PR, no open threads. Stdlib unittest, no network. Run: make -C
.claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SCRIPT = KIT / "skills" / "pr-watch" / "pr-merge.sh"
FULL = "a" * 40

GH_STUB = r"""#!/bin/bash
# answers exactly the calls pr-merge.sh makes; every call is logged
echo "$*" >> "$STUB_LOG"
case "$*" in
  *"--json headRefOid,mergeStateStatus,reviewDecision"*)  # join with the separator the -q filter asks for, as gh would
    case "$*" in *'join("|")'*) sep='|' ;; *) sep=' ' ;; esac
    printf '%s%s%s%s%s\n' "${FULL:0:9}" "$sep" CLEAN "$sep" "$STUB_RD" ;;
  *"--json headRefOid -q .headRefOid[0:9]"*) echo "${FULL:0:9}" ;;
  *"--json headRefOid -q .headRefOid"*) echo "$FULL" ;;
  *"--json mergeStateStatus"*) echo CLEAN ;;
  *"api graphql"*) echo '{"data":{"repository":{"pullRequest":{"reviewThreads":{"pageInfo":{"hasNextPage":false,"endCursor":null},"nodes":[]}}}}}' ;;
  *"--jq .merged_at"*) echo "${STUB_MERGED_AT:-null}" ;;
  *"/reviews"*) echo "$STUB_REVIEWS" ;;
  "pr merge"*)
    if [ -n "${STUB_MERGE_FAIL:-}" ]; then echo "gh: merge blocked (simulated)" >&2; exit 1; fi
    echo "merged" ;;
  *) echo "stub gh: unexpected call: $*" >&2; exit 9 ;;
esac
"""

# A stub whose mergeStateStatus starts BEHIND and flips to CLEAN once `update-branch` shows up in the call
# log (inspecting the stub's own log is simpler than a second temp file for a one-shot phase transition).
GH_STUB_BEHIND = r"""#!/bin/bash
echo "$*" >> "$STUB_LOG"
state() { grep -q "update-branch" "$STUB_LOG" && echo CLEAN || echo BEHIND; }
case "$*" in
  *"--json headRefOid,mergeStateStatus,reviewDecision"*)
    case "$*" in *'join("|")'*) sep='|' ;; *) sep=' ' ;; esac
    printf '%s%s%s%s%s\n' "${FULL:0:9}" "$sep" "$(state)" "$sep" "$STUB_RD" ;;
  *"--json headRefOid -q .headRefOid[0:9]"*) echo "${FULL:0:9}" ;;
  *"--json headRefOid -q .headRefOid"*) echo "$FULL" ;;
  *"--json mergeStateStatus"*) state ;;
  *"api graphql"*) echo '{"data":{"repository":{"pullRequest":{"reviewThreads":{"pageInfo":{"hasNextPage":false,"endCursor":null},"nodes":[]}}}}}' ;;
  *"--jq .merged_at"*) echo "${STUB_MERGED_AT:-null}" ;;
  *"-X PUT"*"update-branch"*) exit 0 ;;
  *"-X DELETE"*"requested_reviewers"*) exit 0 ;;
  *"-X POST"*"requested_reviewers"*) exit 0 ;;
  *"pulls/7/reviews?per_page=100"*) echo "$STUB_REVIEWS" ;;
  "pr merge"*) echo "merged" ;;
  *) echo "stub gh: unexpected call: $*" >&2; exit 9 ;;
esac
"""


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class EmptyReviewDecision(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prmerge-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        env_dir = self.tmp / "ws" / ".context" / "reference" / "env"
        env_dir.mkdir(parents=True)
        (env_dir / "config.json").write_text(json.dumps({"github": {"review_bot": "", "sandbox_token_prefix": ""}}))
        self.log = self.tmp / "gh.log"

    def run_merge(self, rd: str, reviews: list | None = None, *, merge_fail: bool = False,
                  merged_at: str | None = "2026-01-01T12:00:00Z") -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("PR_WATCH_BOT_LOGIN", "GH_TOKEN")}
        env.update(PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", CONTEXT_ROOT=str(self.tmp / "ws" / ".context"),
                   STUB_LOG=str(self.log), STUB_RD=rd, STUB_REVIEWS=json.dumps(reviews or []), FULL=FULL,
                   STUB_MERGED_AT=merged_at or "null", STUB_MERGE_FAIL="1" if merge_fail else "")
        return subprocess.run(["bash", str(SCRIPT), "o/r", "7"], env=env, capture_output=True, text=True, timeout=60)

    @staticmethod
    def review(user: str, state: str, at: str, commit: str = FULL) -> dict:
        return {"user": {"login": user}, "state": state, "submitted_at": at, "commit_id": commit}

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def assert_merged(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MERGED on", r.stdout)
        self.assertIn("pr merge 7", self.calls())
        self.assertIn("--jq .merged_at", self.calls())

    def assert_stopped(self, r, decision: str):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"decision '{decision}'", r.stdout)
        self.assertNotIn("pr merge", self.calls())

    def test_empty_decision_with_an_approval_on_head_merges(self):
        r = self.run_merge("", [self.review("ann", "APPROVED", "2026-01-01T10:00:00Z"),
                                self.review("ann", "COMMENTED", "2026-01-01T11:00:00Z")])  # a later comment decides nothing
        self.assert_merged(r)
        self.assertNotIn("unbound variable", r.stderr)

    def test_empty_decision_without_an_approval_needs_a_human(self):
        self.assert_stopped(self.run_merge("", []), "NONE")

    def test_an_approval_on_an_older_head_does_not_count(self):
        self.assert_stopped(self.run_merge("", [self.review("ann", "APPROVED", "2026-01-01T10:00:00Z", "b" * 40)]), "NONE")

    def test_a_reviewers_later_change_request_on_the_same_head_blocks(self):
        # review of #108: counting APPROVED objects merged a PR its reviewer had just rejected without a new push
        r = self.run_merge("", [self.review("ann", "APPROVED", "2026-01-01T10:00:00Z"),
                                self.review("ann", "CHANGES_REQUESTED", "2026-01-01T11:00:00Z"),
                                self.review("bob", "APPROVED", "2026-01-01T12:00:00Z")])
        self.assert_stopped(r, "CHANGES_REQUESTED")

    def test_required_review_approved_merges_without_the_lookup(self):
        r = self.run_merge("APPROVED")
        self.assert_merged(r)
        self.assertNotIn("/reviews", self.calls())

    def test_a_failed_merge_call_exits_nonzero_not_zero(self):
        # `gh pr merge`'s own exit status decides this, not the exit status of a `tail` piped after it — the
        # piped form reported "MERGE ATTEMPTED" and exited 0 whether or not the merge actually went through.
        r = self.run_merge("APPROVED", merge_fail=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("MERGE FAILED", r.stdout)
        self.assertNotIn("MERGED on", r.stdout)

    def test_a_reported_success_without_merged_at_is_not_read_as_merged(self):
        r = self.run_merge("APPROVED", merged_at=None)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("merged_at is still unset", r.stdout)
        self.assertNotIn("MERGED on", r.stdout)


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class ForceReviewOnBehind(unittest.TestCase):
    """The BEHIND path: once `update-branch` has run, re-requesting the bot's review removes then
    re-adds it as a requested reviewer (DELETE, then POST `pulls/N/requested_reviewers`) instead of flipping
    the PR to draft and back (`gh pr ready --undo` / `gh pr ready`) — that toggle is visible to every other
    reviewer and cancels a `ready_for_review`-triggered run for no benefit here. Exercises the real BEHIND ->
    update-branch -> re-request -> CLEAN sequence, so it pays the script's one real `sleep 30` between the
    update-branch call and the re-check."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prmerge-behind-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(GH_STUB_BEHIND)
        gh.chmod(0o755)
        env_dir = self.tmp / "ws" / ".context" / "reference" / "env"
        env_dir.mkdir(parents=True)
        (env_dir / "config.json").write_text(json.dumps({"github": {"review_bot": "test-review-bot", "sandbox_token_prefix": ""}}))
        self.log = self.tmp / "gh.log"

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def test_behind_re_requests_the_bot_instead_of_toggling_draft(self):
        reviews = json.dumps([{"user": {"login": "test-review-bot"}, "commit_id": FULL,
                               "body": "### Assessment: 🟢 looks good"}])
        env = {k: v for k, v in os.environ.items() if k not in ("PR_WATCH_BOT_LOGIN", "GH_TOKEN")}
        env.update(PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", CONTEXT_ROOT=str(self.tmp / "ws" / ".context"),
                   STUB_LOG=str(self.log), STUB_REVIEWS=reviews, FULL=FULL, STUB_RD="APPROVED",
                   STUB_MERGED_AT="2026-01-01T12:00:00Z")
        r = subprocess.run(["bash", str(SCRIPT), "o/r", "7"], env=env, capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MERGED on", r.stdout)
        calls = self.calls()
        self.assertIn("update-branch", calls)
        self.assertIn("-X DELETE repos/o/r/pulls/7/requested_reviewers", calls)
        self.assertIn("-X POST repos/o/r/pulls/7/requested_reviewers", calls)
        self.assertNotIn("pr ready", calls)


if __name__ == "__main__":
    unittest.main()
