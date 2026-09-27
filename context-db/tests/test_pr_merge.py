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
  *"/reviews"*) echo "$STUB_REVIEWS" ;;
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

    def run_merge(self, rd: str, reviews: list | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("PR_WATCH_BOT_LOGIN", "GH_TOKEN")}
        env.update(PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", CONTEXT_ROOT=str(self.tmp / "ws" / ".context"),
                   STUB_LOG=str(self.log), STUB_RD=rd, STUB_REVIEWS=json.dumps(reviews or []), FULL=FULL)
        return subprocess.run(["bash", str(SCRIPT), "o/r", "7"], env=env, capture_output=True, text=True, timeout=60)

    @staticmethod
    def review(user: str, state: str, at: str, commit: str = FULL) -> dict:
        return {"user": {"login": user}, "state": state, "submitted_at": at, "commit_id": commit}

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def assert_merged(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MERGE ATTEMPTED", r.stdout)
        self.assertIn("pr merge 7", self.calls())

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

if __name__ == "__main__":
    unittest.main()
