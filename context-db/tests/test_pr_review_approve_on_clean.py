"""submit-review.sh `--auto-comment` with `event: APPROVE` — the clean-sheet gate behind
`auto_comment.approve_on_clean` (`off | shadow | live`, default `off`). The script re-reads, on the exact
head: the runner's triage.json (zero findings) and its sidecar sheet.json (bound to this repo/pr/head,
RECOMMEND APPROVE-if-user-agrees, NEEDS nothing, no unverified trap, no demoted lens claim) + traps.json;
the base branch's required checks (classic protection + rulesets + the branch summary) each a completed
`success`, every other check green; dismiss stale reviews on; not a draft, no unresolved thread, no
standing CHANGES_REQUESTED, mergeable_state clean (or blocked only by the missing approval); the live
author (not the login) — and refuses with every reason on one line otherwise. `shadow` posts the COMMENT
`off` would post and ledgers the gate's verdict beside it.

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
MOVED = "c" * 40
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
fail_http(){ printf '{"message":"stub"}\n'; echo "gh: stub (HTTP $1)" >&2; exit 1; }
case "$path" in
  graphql) printf '%s\n' "$STUB_THREADS_JSON"; exit 0 ;;
  repos/*/rules/branches/*) [ -n "${STUB_RULES_HTTP:-}" ] && fail_http "$STUB_RULES_HTTP"; printf '%s\n' "$STUB_RULES_JSON"; exit 0 ;;
  repos/*/branches/*/protection) [ -n "${STUB_PROTECTION_HTTP:-}" ] && fail_http "$STUB_PROTECTION_HTTP"; printf '%s\n' "$STUB_PROTECTION_JSON"; exit 0 ;;
  repos/*/branches/*) printf '%s\n' "$STUB_BRANCH_JSON"; exit 0 ;;
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
  */pulls/*)
    n=0; [ -f "$STUB_PR_CTR" ] && n=$(cat "$STUB_PR_CTR"); n=$((n + 1)); echo "$n" > "$STUB_PR_CTR"
    if [ -n "${STUB_PR_MOVE_AT:-}" ] && [ "$n" -ge "$STUB_PR_MOVE_AT" ]; then printf '%s\n' "$STUB_PR_MOVED_JSON"; exit 0; fi
    printf '%s\n' "$STUB_PR_JSON"; exit 0 ;;
  *) echo "stub gh: unhandled api path: $path" >&2; exit 9 ;;
esac
"""

GREEN_CHECKS = {"check_runs": [{"name": "lint", "status": "completed", "conclusion": "success"},
                               {"name": "tests", "status": "completed", "conclusion": "skipped"}]}
GREEN_STATUS = {"state": "success", "total_count": 0, "statuses": []}
BRANCH = {"name": "main", "protected": True,
          "protection": {"enabled": True, "required_status_checks": {"enforcement_level": "non_admins", "contexts": ["lint"]}}}
PROTECTION = {"required_status_checks": {"strict": False, "contexts": ["lint"], "checks": [{"context": "lint", "app_id": None}]},
              "required_pull_request_reviews": {"dismiss_stale_reviews": True, "required_approving_review_count": 1}}
THREADS = {"data": {"repository": {"pullRequest": {"reviewThreads": {
    "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"isResolved": True}]}}}}}
SHEET = {"repo": REPO, "pr": 9, "head": HEAD, "recommend": "APPROVE-if-user-agrees", "needs": "nothing",
         "unverified_traps": 0, "demoted": 0}
TRAPS = [{"trap": "timezone", "result": "n/a"}]
FINDING = {"n": 1, "sev": "NIT", "class": "style", "path": "a.py", "line": 3, "side": "RIGHT",
           "finding": "x", "evidence": "y", "comment": "z", "decision": None}
POSTED_REVIEW = {"id": REVIEW_ID, "state": "APPROVED", "user": {"login": ME},
                 "html_url": "https://example.test/review"}
UNSET = object()


def pr_json(head=HEAD, author="alice", **extra) -> str:
    return json.dumps({"head": {"sha": head}, "base": {"sha": BASE, "ref": "main"}, "state": "open",
                       "user": {"login": author}, "draft": False, "mergeable_state": "clean", **extra})


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
        self.ctx = self.tmp / "ctx"  # the runner's per-PR $CTX: triage.json + sheet.json + traps.json
        self.ctx.mkdir()
        self.log = self.tmp / "gh.log"
        self.post = self.tmp / "post.json"

    def config(self, approve_on_clean=None, mode="live") -> None:
        ac = {"mode": mode, "prios": [1], "max_per_tick": 3}
        if approve_on_clean is not None:
            ac["approve_on_clean"] = approve_on_clean
        self.state_dir.joinpath("config.json").write_text(json.dumps({
            "login": ME, "footer": "", "submitted_retention_days": 14,
            "auto_approve": {"mode": "off"}, "auto_comment": ac}))

    def env(self, author="alice", checks=None, status=None, reviews=None, pr=None, branch=None, protection=None,
            rules=None, threads=None, **extra) -> dict:
        return {
            **{k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "PR_REVIEW_HOME")},
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "CONTEXT_ROOT": str(self.tmp / "ws" / ".context"),
            "STUB_LOG": str(self.log), "STUB_POST": str(self.post), "STUB_PR_CTR": str(self.tmp / "prctr"),
            "STUB_PR_JSON": pr if pr is not None else pr_json(author=author),
            "STUB_PR_MOVED_JSON": pr_json(head=MOVED, author=author),
            "STUB_REVIEW_ID": str(REVIEW_ID),
            "STUB_CHECKS_JSON": json.dumps(GREEN_CHECKS if checks is None else checks),
            "STUB_STATUS_JSON": json.dumps(GREEN_STATUS if status is None else status),
            "STUB_REVIEWS_JSON": json.dumps([POSTED_REVIEW] if reviews is None else reviews),
            "STUB_BRANCH_JSON": json.dumps(BRANCH if branch is None else branch),
            "STUB_PROTECTION_JSON": json.dumps(PROTECTION if protection is None else protection),
            "STUB_RULES_JSON": json.dumps([] if rules is None else rules),
            "STUB_THREADS_JSON": json.dumps(THREADS if threads is None else threads),
            **extra,
        }

    def put(self, name: str, payload) -> str:
        p = self.ctx / name
        if payload is UNSET:
            p.unlink(missing_ok=True)
        else:
            p.write_text(json.dumps(payload))
        return str(p)

    def run_cmd(self, cmd, env, event="APPROVE", findings=None, comments=None, triage=True, sheet=None,
                traps=None, extra=()):
        req = self.tmp / "req.json"
        req.write_text(json.dumps({"event": event, "body": "Checked: nothing to flag.", "comments": comments or []}))
        args = ["bash", str(SUBMIT), cmd, "--repo", REPO, "--pr", PR, "--head", HEAD, "--request", str(req), "--auto-comment"]
        self.put("sheet.json", SHEET if sheet is None else sheet)
        self.put("traps.json", TRAPS if traps is None else traps)
        if triage:
            args += ["--triage", self.put("triage.json", findings if findings is not None else [])]
        args += list(extra)
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)

    def submit(self, env, submit_env=None, **kw):
        pv = self.run_cmd("preview", env, **kw)
        if pv.returncode != 0:
            return pv
        digest = next(l.split()[-1] for l in pv.stdout.splitlines() if l.startswith("digest:"))
        return self.run_cmd("submit", submit_env or env, extra=("--confirm", digest), **kw)

    def ledger(self) -> list[dict]:
        f = self.state_dir / "ledger.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []

    def assertRefused(self, r, *needles, rc=8):
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        for n in needles:
            self.assertIn(n, r.stderr)
        self.assertFalse(self.post.exists(), "a refused review must never be POSTed")
        self.assertEqual(self.ledger(), [])

    def assertGatePasses(self, env, **kw):
        r = self.run_cmd("preview", env, **kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("approve_on_clean gate passed", r.stderr)

    # --- accept ---

    def test_live_clean_sheet_posts_approve_and_ledgers_auto_approved_clean(self):
        self.config("live")
        r = self.submit(self.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("status: submitted", r.stdout)
        posted = json.loads(self.post.read_text())
        self.assertEqual((posted["event"], posted["commit_id"]), ("APPROVE", HEAD))  # pinned to the gated head
        rows = self.ledger()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["status"], rows[0]["event"], rows[0]["head"]), ("auto_approved_clean", "APPROVE", HEAD))

    def test_a_cleared_changes_requested_does_not_block(self):
        self.config("live")
        reviews = [{"id": 1, "state": "CHANGES_REQUESTED", "user": {"login": "bob"}},
                   {"id": 2, "state": "APPROVED", "user": {"login": "bob"}}, POSTED_REVIEW]
        r = self.submit(self.env(reviews=reviews))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_required_context_reported_as_a_commit_status_counts(self):
        self.config("live")
        checks = {"check_runs": [{"name": "tests", "status": "completed", "conclusion": "neutral"}]}
        status = {"state": "success", "total_count": 1, "statuses": [{"context": "lint", "state": "success"}]}
        self.assertGatePasses(self.env(checks=checks, status=status))

    def test_blocked_only_by_the_missing_approval_passes(self):
        self.config("live")
        self.assertGatePasses(self.env(pr=pr_json(mergeable_state="blocked"), reviews=[]))

    def test_ruleset_only_branch_supplies_required_checks_and_dismiss_stale(self):
        self.config("live")
        rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "lint"}]}},
                 {"type": "pull_request", "parameters": {"dismiss_stale_reviews_on_push": True,
                                                         "required_approving_review_count": 1}}]
        env = self.env(branch={"name": "main", "protected": False}, rules=rules, STUB_PROTECTION_HTTP="404")
        self.assertGatePasses(env)

    # --- refuse: config and request shape ---

    def test_refuses_when_approve_on_clean_is_not_live(self):
        for value in (None, "off", "shadow"):
            with self.subTest(value=value):
                self.config(value)
                r = self.run_cmd("preview", self.env())
                self.assertRefused(r, "approve_on_clean", "'%s'" % (value or "off"))

    def test_refuses_when_auto_comment_mode_is_not_live(self):
        self.config("live", mode="shadow")
        self.assertRefused(self.run_cmd("preview", self.env()), "auto_comment.mode")

    def test_refuses_request_changes_on_this_path(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(), event="REQUEST_CHANGES"), "only for COMMENT", rc=2)

    def test_triage_flag_is_refused_on_a_comment_unless_shadow(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(), event="COMMENT"), "--triage", rc=2)

    def test_refuses_inline_comments_on_an_approve(self):
        self.config("live")
        r = self.run_cmd("preview", self.env(), comments=[{"path": "a.py", "line": 1, "body": "x"}])
        self.assertRefused(r, "inline comments")

    def test_refuses_the_users_own_pr(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(author=ME)), "own PR")

    # --- refuse: the runner's sheet (triage + sidecar + traps) ---

    def test_refuses_a_sheet_with_a_finding_even_a_nit(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(), findings=[FINDING]), "triage has 1 finding")

    def test_refuses_when_triage_is_missing_or_not_a_findings_array(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(), triage=False), "--triage", rc=2)
        self.assertRefused(self.run_cmd("preview", self.env(), findings={"n": 0}), "not a findings array")

    def test_refuses_a_missing_or_stale_sidecar(self):
        self.config("live")
        cases = (
            (UNSET, "no sheet.json"),
            ({**SHEET, "head": "d" * 40}, "stale sheet"),
            ({**SHEET, "pr": 10}, "not acme/widgets#9"),
            ({**SHEET, "recommend": "COMMENT"}, "sheet recommends 'COMMENT'"),
            ({**SHEET, "needs": "gh reauth"}, "sheet NEEDS 'gh reauth'"),
            ({**SHEET, "unverified_traps": 1}, "unverified_traps is 1"),
            ({**SHEET, "demoted": 2}, "sheet demoted is 2"),
            ({k: v for k, v in SHEET.items() if k != "demoted"}, "sheet demoted is missing"),
        )
        for sheet, needle in cases:
            with self.subTest(needle=needle):
                self.assertRefused(self.run_cmd("preview", self.env(), sheet=sheet), needle)

    def test_refuses_an_unverified_or_missing_traps_file(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(), traps=[{"trap": "t", "result": "unverified"}]),
                           "traps.json has 1 unverified trap")
        self.assertRefused(self.run_cmd("preview", self.env(), traps=UNSET), "no traps.json")

    # --- refuse: checks against the base branch's required set ---

    def test_refuses_a_red_or_pending_check(self):
        self.config("live")
        for conclusion, status in (("failure", "completed"), (None, "in_progress")):
            with self.subTest(conclusion=conclusion, status=status):
                checks = {"check_runs": [{"name": "lint", "status": "completed", "conclusion": "success"},
                                         {"name": "tests", "status": status, "conclusion": conclusion}]}
                self.assertRefused(self.run_cmd("preview", self.env(checks=checks)), "checks not green: tests")

    def test_a_skipped_or_neutral_required_check_is_not_green(self):
        self.config("live")
        for conclusion in ("skipped", "neutral"):
            with self.subTest(conclusion=conclusion):
                checks = {"check_runs": [{"name": "lint", "status": "completed", "conclusion": conclusion}]}
                self.assertRefused(self.run_cmd("preview", self.env(checks=checks)),
                                   f"required checks not green: lint {conclusion}")

    def test_refuses_a_required_check_not_reported_yet(self):
        self.config("live")
        prot = {**PROTECTION, "required_status_checks": {"contexts": ["lint", "e2e"], "checks": []}}
        self.assertRefused(self.run_cmd("preview", self.env(protection=prot)), "e2e not reported yet")

    def test_refuses_when_the_head_has_no_check_runs(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(checks={"check_runs": []})), "no check-runs")

    def test_refuses_a_red_combined_status(self):
        self.config("live")
        st = {"state": "failure", "total_count": 1, "statuses": [{"context": "ci", "state": "failure"}]}
        self.assertRefused(self.run_cmd("preview", self.env(status=st)), "combined status failure")

    def test_an_unreadable_protection_is_not_a_green_gate(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(STUB_PROTECTION_HTTP="403")), "unreadable (HTTP 403)")
        # 404 on a branch the summary calls protected = no admin read, not "unprotected"
        self.assertRefused(self.run_cmd("preview", self.env(STUB_PROTECTION_HTTP="404")), "unreadable (HTTP 404)")

    def test_refuses_when_the_rulesets_cannot_be_read(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(STUB_RULES_HTTP="500")), "cannot read the rulesets on main")

    # --- refuse: dismiss stale reviews (finding: an APPROVE must not outlive a later push) ---

    def test_refuses_live_when_dismiss_stale_reviews_is_off(self):
        self.config("live")
        prot = {**PROTECTION, "required_pull_request_reviews": {"dismiss_stale_reviews": False,
                                                                 "required_approving_review_count": 1}}
        self.assertRefused(self.run_cmd("preview", self.env(protection=prot)),
                           "dismiss stale reviews is off on main", "keep approve_on_clean: shadow")

    def test_an_unprotected_branch_has_no_dismiss_stale_either(self):
        self.config("live")
        env = self.env(branch={"name": "main", "protected": False}, STUB_PROTECTION_HTTP="404")
        self.assertRefused(self.run_cmd("preview", env), "dismiss stale reviews is off on main")

    # --- refuse: PR state ---

    def test_refuses_a_draft(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(pr=pr_json(draft=True))), "PR is a draft")

    def test_refuses_an_unresolved_thread(self):
        self.config("live")
        threads = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"isResolved": True}, {"isResolved": False}]}}}}}
        self.assertRefused(self.run_cmd("preview", self.env(threads=threads)), "1 unresolved review thread")

    def test_refuses_when_the_threads_cannot_be_read(self):
        self.config("live")
        self.assertRefused(self.run_cmd("preview", self.env(threads={"errors": [{"message": "x"}]})),
                           "cannot read the review threads")

    def test_refuses_a_mergeable_state_other_than_clean(self):
        self.config("live")
        for ms in ("dirty", "behind", "unstable", "unknown"):
            with self.subTest(ms=ms):
                self.assertRefused(self.run_cmd("preview", self.env(pr=pr_json(mergeable_state=ms))), f"mergeable_state {ms}")

    def test_refuses_blocked_when_the_approvals_are_already_there(self):
        self.config("live")
        reviews = [{"id": 2, "state": "APPROVED", "user": {"login": "bob"}}]
        self.assertRefused(self.run_cmd("preview", self.env(pr=pr_json(mergeable_state="blocked"), reviews=reviews)),
                           "not by a missing approval")

    def test_refuses_a_standing_changes_requested_review(self):
        self.config("live")
        reviews = [{"id": 1, "state": "CHANGES_REQUESTED", "user": {"login": "bob"}},
                   {"id": 3, "state": "COMMENTED", "user": {"login": "bob"}}, POSTED_REVIEW]
        self.assertRefused(self.run_cmd("preview", self.env(reviews=reviews)), "CHANGES_REQUESTED standing from: bob")

    def test_every_reason_lands_on_one_stderr_line(self):
        self.config("live")
        r = self.run_cmd("preview", self.env(pr=pr_json(draft=True)), findings=[FINDING], sheet=UNSET)
        self.assertRefused(r, "PR is a draft", "triage has 1 finding", "no sheet.json")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)

    def test_a_head_moved_between_gate_and_post_posts_nothing(self):
        self.config("live")
        # preview reads pulls/9 once; submit reads it for the gate (call 1) and again right before the POST (call 2)
        submit_env = self.env(STUB_PR_CTR=str(self.tmp / "prctr-submit"), STUB_PR_MOVE_AT="2")
        r = self.submit(self.env(), submit_env=submit_env)
        self.assertRefused(r, "head moved", "nothing posted", rc=3)

    # --- shadow: the COMMENT `off` would post, plus the gate's verdict beside it ---

    def test_shadow_posts_the_comment_and_ledgers_would_approve(self):
        self.config("shadow")
        r = self.submit(self.env(), event="COMMENT")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would approve (clean)", r.stdout)
        self.assertEqual(json.loads(self.post.read_text())["event"], "COMMENT")
        rows = self.ledger()
        self.assertEqual([x["status"] for x in rows], ["auto_commented", "shadow_approve_clean"], rows)
        self.assertEqual(rows[1]["comment_review_id"], REVIEW_ID)

    def test_shadow_with_a_failing_gate_still_posts_the_comment(self):
        self.config("shadow")
        r = self.submit(self.env(), event="COMMENT", findings=[FINDING])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(self.post.read_text())["event"], "COMMENT")
        rows = self.ledger()
        self.assertEqual([x["status"] for x in rows], ["auto_commented", "shadow_approve_refused"], rows)
        self.assertIn("triage has 1 finding", rows[1]["reason"])

    def test_shadow_names_the_dismiss_stale_gap_without_refusing(self):
        self.config("shadow")
        prot = {**PROTECTION, "required_pull_request_reviews": {"dismiss_stale_reviews": False}}
        r = self.submit(self.env(protection=prot), event="COMMENT")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.ledger()
        self.assertEqual(rows[1]["status"], "shadow_approve_clean", rows)
        self.assertIn("live would also refuse: dismiss stale reviews is off", rows[1]["reason"])

    # --- the existing COMMENT path is untouched ---

    def test_comment_path_still_writes_auto_commented(self):
        self.config("live")
        r = self.submit(self.env(), event="COMMENT", triage=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([x["status"] for x in self.ledger()], ["auto_commented"])
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
        for needle in ("auto_approved_clean", "shadow_approve_clean", "shadow_approve_refused", "sheet.json",
                       "dismiss stale reviews", "required checks", "mergeable_state"):
            self.assertIn(needle, runner)
        agent = text("agents", "review-runner.md")
        self.assertIn("sheet.json", agent)


if __name__ == "__main__":
    unittest.main()
