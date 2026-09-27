"""pr-merge.sh's final gate against a stub `gh` (#89): a repo that requires no review reports `reviewDecision` as "",
which a space split lost (`set -u` then killed the script before any merge). With the `|` split, an empty decision
merges only when a review on the current head is APPROVED; otherwise it stops for a human. No-bot mode (the env
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
  *"/reviews"*) echo "$STUB_APPROVALS" ;;
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

    def run_merge(self, rd: str, approvals: int) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("PR_WATCH_BOT_LOGIN", "GH_TOKEN")}
        env.update(PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", CONTEXT_ROOT=str(self.tmp / "ws" / ".context"),
                   STUB_LOG=str(self.log), STUB_RD=rd, STUB_APPROVALS=str(approvals), FULL=FULL)
        return subprocess.run(["bash", str(SCRIPT), "o/r", "7"], env=env, capture_output=True, text=True, timeout=60)

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def test_empty_decision_with_an_approval_on_head_merges(self):
        r = self.run_merge("", 1)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MERGE ATTEMPTED", r.stdout)
        self.assertNotIn("unbound variable", r.stderr)
        self.assertIn("pr merge 7", self.calls())

    def test_empty_decision_without_an_approval_needs_a_human(self):
        r = self.run_merge("", 0)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("decision 'NONE'", r.stdout)
        self.assertNotIn("pr merge", self.calls())

    def test_required_review_approved_merges_without_the_lookup(self):
        r = self.run_merge("APPROVED", 0)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("/reviews", self.calls())


if __name__ == "__main__":
    unittest.main()
