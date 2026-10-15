"""submit-review.sh `--auto-comment` with `event: APPROVE` — the clean-sheet gate behind
`auto_comment.approve_on_clean` (`off | shadow | live`, default `off`). The script re-reads, on the exact
head: the runner's triage.json (zero findings), the check-runs + combined status (all green), the PR's
reviews (no standing CHANGES_REQUESTED) and the live author (not the login), and refuses with a named
reason otherwise. `shadow` prints "would approve (clean)", ledgers `shadow_approve_clean`, posts nothing.

Against a stub `gh` on PATH — no network, a temp CONTEXT_ROOT only. Stdlib unittest."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SUBMIT = KIT / "skills" / "pr-review" / "scripts" / "submit-review.sh"
REPO = "acme/widgets"
PR = "9"
HEAD = "a" * 40
BASE = "b" * 40
ME = "tester"
REVIEW_ID = 777001

GH_STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
[ "$1" = api ] || { echo "stub gh: unexpected call: $*" >&2; exit 9; }
shift
path=""; method=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) method=$2; shift 2 ;;
    --paginate) shift ;;
    -H|-f|-F|--input) shift 2 ;;
    *) [ -z "$path" ] && path=$1; shift ;;
  esac
done
case "$path" in
  repos/*/pulls/*/reviews)
    if [ "$method" = "POST" ]; then
      cat > "$STUB_POST"
      printf '{"id": %s, "state": "APPROVED", "html_url": "https://example.test/review"}\n' "$STUB_REVIEW_ID"
      exit 0
    fi
    echo "stub gh: unexpected non-POST on $path" >&2; exit 9 ;;
  */pulls/*/reviews\?per_page=100)  printf '%s\n' "${STUB_REVIEWS_JSON:-[]}"; exit 0 ;;
  */pulls/*/comments\?per_page=100) printf '%s\n' "[]"; exit 0 ;;
  */pulls/*/files\?per_page=100)    printf '%s\n' "[]"; exit 0 ;;
  */commits/*/check-runs\?per_page=100) printf '%s\n' "$STUB_CHECKS_JSON"; exit 0 ;;
  */commits/*/status)               printf '%s\n' "$STUB_STATUS_JSON"; exit 0 ;;
  */pulls/*)                        printf '%s\n' "$STUB_PR_JSON"; exit 0 ;;
  *) echo "stub gh: unhandled api path: $path" >&2; exit 9 ;;
esac
"""

GREEN_CHECKS = {"check_runs": [{"name": "lint", "status": "completed", "conclusion": "success"},
                               {"name": "tests", "status": "completed", "conclusion": "skipped"}]}
GREEN_STATUS = {"state": "success", "total_count": 0, "statuses": []}
FINDING = {"n": 1, "sev": "NIT", "class": "style", "path": "a.py", "line": 3, "side": "RIGHT",
           "finding": "x", "evidence": "y", "comment": "z", "decision": None}
POSTED_REVIEW = {"id": REVIEW_ID, "state": "APPROVED", "user": {"login": ME},
                 "html_url": "https://example.test/review"}


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash") and shutil.which("flock"), "jq, bash, flock needed")
class ApproveOnClean(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prreview-approveclean-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        env_dir = self.tmp / "ws" / ".context" / "reference" / "env"
        env_dir.mkdir(parents=True)
        env_dir.joinpath("config.json").write_text(json.dumps({"github": {"review_bot": "", "sandbox_token_prefix": ""}}))
        self.state_dir = self.tmp / "ws" / ".context" / "state" / "pr-review"
        self.state_dir.mkdir(parents=True)
        self.log = self.tmp / "gh.log"
        self.post = self.tmp / "post.json"

    def config(self, approve_on_clean=None, mode="live") -> None:
        ac = {"mode": mode, "prios": [1], "max_per_tick": 3}
        if approve_on_clean is not None:
            ac["approve_on_clean"] = approve_on_clean
        self.state_dir.joinpath("config.json").write_text(json.dumps({
            "login": ME, "footer": "", "submitted_retention_days": 14,
            "auto_approve": {"mode": "off"}, "auto_comment": ac}))

    def env(self, author="alice", checks=None, status=None, reviews=None) -> dict:
        return {
            **{k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "PR_REVIEW_HOME")},
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "CONTEXT_ROOT": str(self.tmp / "ws" / ".context"),
            "STUB_LOG": str(self.log), "STUB_POST": str(self.post),
            "STUB_PR_JSON": json.dumps({"head": {"sha": HEAD}, "base": {"sha": BASE}, "state": "open",
                                         "user": {"login": author}}),
            "STUB_REVIEW_ID": str(REVIEW_ID),
            "STUB_CHECKS_JSON": json.dumps(GREEN_CHECKS if checks is None else checks),
            "STUB_STATUS_JSON": json.dumps(GREEN_STATUS if status is None else status),
            "STUB_REVIEWS_JSON": json.dumps([POSTED_REVIEW] if reviews is None else reviews),
        }

    def write(self, name: str, payload) -> str:
        p = self.tmp / name
        p.write_text(json.dumps(payload))
        return str(p)

    def run_cmd(self, cmd, env, event="APPROVE", findings=None, comments=None, triage=True, extra=()):
        req = self.write("req.json", {"event": event, "body": "Checked: nothing to flag.", "comments": comments or []})
        args = ["bash", str(SUBMIT), cmd, "--repo", REPO, "--pr", PR, "--head", HEAD, "--request", req, "--auto-comment"]
        if triage:
            args += ["--triage", self.write("triage.json", findings if findings is not None else [])]
        args += list(extra)
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)

    def submit(self, env, **kw):
        pv = self.run_cmd("preview", env, **kw)
        if pv.returncode != 0:
            return pv
        digest = next(l.split()[-1] for l in pv.stdout.splitlines() if l.startswith("digest:"))
        return self.run_cmd("submit", env, extra=("--confirm", digest), **kw)

    def ledger(self) -> list[dict]:
        f = self.state_dir / "ledger.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []

    def assertRefused(self, r, *needles, rc=8):
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        for n in needles:
            self.assertIn(n, r.stderr)
        self.assertFalse(self.post.exists(), "a refused review must never be POSTed")
        self.assertEqual(self.ledger(), [])

    # --- accept ---

    def test_live_clean_sheet_posts_approve_and_ledgers_auto_approved_clean(self):
        self.config("live")
        r = self.submit(self.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("status: submitted", r.stdout)
        self.assertEqual(json.loads(self.post.read_text())["event"], "APPROVE")
        rows = self.ledger()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["status"], rows[0]["event"], rows[0]["head"]), ("auto_approved_clean", "APPROVE", HEAD))

    def test_a_cleared_changes_requested_does_not_block(self):
        self.config("live")
        reviews = [{"id": 1, "state": "CHANGES_REQUESTED", "user": {"login": "bob"}},
                   {"id": 2, "state": "APPROVED", "user": {"login": "bob"}}, POSTED_REVIEW]
        r = self.submit(self.env(reviews=reviews))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # --- refuse, each with a named reason ---

    def test_refuses_when_approve_on_clean_is_off_or_unset(self):
        for value in (None, "off"):
            with self.subTest(value=value):
                self.config(value)
                r = self.run_cmd("preview", self.env())
                self.assertRefused(r, "approve_on_clean", "'%s'" % (value or "off"))

    def test_refuses_when_auto_comment_mode_is_not_live(self):
        self.config("live", mode="shadow")
        self.assertRefused(self.run_cmd("preview", self.env()), "auto_comment.mode")

    def test_refuses_a_sheet_with_a_finding_even_a_nit(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(), findings=[FINDING]), "triage has 1 finding")

    def test_refuses_when_triage_is_missing_or_not_a_findings_array(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(), triage=False), "--triage", rc=2)
        self.assertRefused(self.run_cmd("preview", self.env(), findings={"n": 0}), "not a findings array")

    def test_refuses_inline_comments_on_an_approve(self):
        self.config("live")
        r = self.run_cmd("preview", self.env(), comments=[{"path": "a.py", "line": 1, "body": "x"}])
        self.assertRefused(r, "inline comments")

    def test_refuses_a_red_or_pending_check(self):
        self.config("live")
        for conclusion, status in (("failure", "completed"), (None, "in_progress")):
            with self.subTest(conclusion=conclusion, status=status):
                checks = {"check_runs": [{"name": "tests", "status": status, "conclusion": conclusion}]}
                self.assertRefused(self.run_cmd("preview", self.env(checks=checks)), "checks not green: tests")

    def test_refuses_when_the_head_has_no_check_runs(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(checks={"check_runs": []})), "no check-runs")

    def test_refuses_a_red_combined_status(self):
        self.config("live")
        st = {"state": "failure", "total_count": 1, "statuses": [{"context": "ci", "state": "failure"}]}
        self.assertRefused(self.run_cmd("preview", self.env(status=st)), "combined status failure")

    def test_refuses_a_standing_changes_requested_review(self):
        self.config("live")
        reviews = [{"id": 1, "state": "CHANGES_REQUESTED", "user": {"login": "bob"}},
                   {"id": 3, "state": "COMMENTED", "user": {"login": "bob"}}, POSTED_REVIEW]
        self.assertRefused(self.run_cmd("preview", self.env(reviews=reviews)), "CHANGES_REQUESTED standing from: bob")

    def test_refuses_the_users_own_pr(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(author=ME)), "own PR")

    def test_refuses_request_changes_on_this_path(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(), event="REQUEST_CHANGES"), "only for COMMENT", rc=2)

    def test_triage_flag_is_refused_on_a_plain_comment(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(), event="COMMENT"), "--triage", rc=2)

    # --- shadow ---

    def test_shadow_logs_would_approve_and_posts_nothing(self):
        self.config("shadow")
        r = self.submit(self.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would approve (clean)", r.stdout)
        self.assertFalse(self.post.exists())
        rows = self.ledger()
        self.assertEqual([x["status"] for x in rows], ["shadow_approve_clean"], rows)
        self.assertNotIn("POST", self.log.read_text())

    def test_shadow_still_refuses_when_the_gate_fails(self):
        self.config("shadow")
        self.assertRefused(self.run_cmd("preview", self.env(), findings=[FINDING]), "triage has 1 finding")

    # --- the existing COMMENT path is untouched ---

    def test_comment_path_still_writes_auto_commented(self):
        self.config("live")
        r = self.submit(self.env(), event="COMMENT", triage=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ledger()[0]["status"], "auto_commented")
        self.assertEqual(json.loads(self.post.read_text())["event"], "COMMENT")


class DocsNameTheKey(unittest.TestCase):
    """The key is documented everywhere the issue lists; text-shape only."""

    def test_key_and_statuses_are_documented(self):
        def text(*parts):
            return re.sub(r"\s+", " ", KIT.joinpath(*parts).read_text(encoding="utf-8"))
        cfg = json.loads(KIT.joinpath("pr-review", "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["auto_comment"]["approve_on_clean"], "off")
        for parts in (("skills", "pr-review", "SKILL.md"), ("skills", "pr-review", "reference", "runner.md"),
                      ("skills", "pr-review", "reference", "scope.md")):
            self.assertIn("approve_on_clean", text(*parts), parts)
        runner = text("skills", "pr-review", "reference", "runner.md")
        for status in ("auto_approved_clean", "shadow_approve_clean"):
            self.assertIn(status, runner)


if __name__ == "__main__":
    unittest.main()
