"""submit-review.sh's `--auto-comment` flag — the unattended auto-COMMENT path for direct review
requests (`pr-review/SKILL.md` § "Unattended auto-COMMENT path for direct review requests"): opt-in via
`auto_comment.mode` (default `off`), COMMENT-only, refused on the user's own PR. Mirrors the existing
`--auto` (trivial-approve) gate shape but for this new path.

Against a stub `gh` on PATH — no network. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
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
REVIEW_ID = 555001

GH_STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
[ "$1" = api ] || { echo "stub gh: unexpected call: $*" >&2; exit 9; }
shift
path=""; method=""; accept=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) method=$2; shift 2 ;;
    --paginate) shift ;;
    -H) accept=$2; shift 2 ;;
    -f|-F) shift 2 ;;
    --input) shift 2 ;;
    *) [ -z "$path" ] && path=$1; shift ;;
  esac
done
case "$path" in
  repos/*/pulls/*/reviews)
    if [ "$method" = "POST" ]; then
      cat >/dev/null   # consume the --input - payload
      printf '{"id": %s, "state": "COMMENTED", "html_url": "%s"}\n' "$STUB_REVIEW_ID" "$STUB_REVIEW_URL"
      exit 0
    fi
    echo "stub gh: unexpected non-POST on $path" >&2; exit 9 ;;
  */pulls/*/reviews\?per_page=100)
    printf '%s\n' "${STUB_REVIEWS_JSON:-[]}"
    exit 0 ;;
  */pulls/*/comments\?per_page=100)
    printf '%s\n' "${STUB_REVIEW_COMMENTS_JSON:-[]}"
    exit 0 ;;
  */pulls/*/files\?per_page=100)
    printf '%s\n' "${STUB_FILES_JSON:-[]}"
    exit 0 ;;
  */pulls/*)
    printf '%s\n' "$STUB_PR_JSON"
    exit 0 ;;
  *) echo "stub gh: unhandled api path: $path" >&2; exit 9 ;;
esac
"""


def pr_json(author: str) -> dict:
    return {"head": {"sha": HEAD}, "base": {"sha": BASE}, "state": "open", "user": {"login": author}}


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class AutoCommentSubmit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prreview-autocomment-test."))
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
        self.log = self.tmp / "gh.log"

    def write_config(self, auto_comment_mode: str) -> None:
        self.state_dir.joinpath("config.json").write_text(json.dumps({
            "login": ME, "footer": "", "submitted_retention_days": 14,
            "auto_approve": {"mode": "off"},
            "auto_comment": {"mode": auto_comment_mode, "prios": [1], "max_per_tick": 3},
        }))

    def base_env(self, pr_author: str = "alice") -> dict:
        return {
            **{k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "PR_REVIEW_HOME")},
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "CONTEXT_ROOT": str(self.tmp / "ws" / ".context"),
            "STUB_LOG": str(self.log),
            "STUB_PR_JSON": json.dumps(pr_json(pr_author)),
            "STUB_REVIEW_ID": str(REVIEW_ID),
            "STUB_REVIEW_URL": f"https://example.test/{REPO}/pull/{PR}#review-{REVIEW_ID}",
            # the post-submit verification GET must already see the review it just POSTed
            "STUB_REVIEWS_JSON": json.dumps([{"id": REVIEW_ID, "state": "COMMENTED",
                                               "html_url": f"https://example.test/{REPO}/pull/{PR}#review-{REVIEW_ID}"}]),
        }

    def write_request(self, name: str, payload: dict) -> str:
        p = self.tmp / name
        p.write_text(json.dumps(payload))
        return str(p)

    def ledger_rows(self) -> list[dict]:
        ledger = self.state_dir / "ledger.jsonl"
        if not ledger.exists():
            return []
        return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]

    # --- refusal when mode != live: the default `shadow` mode must never let a post through ---

    def test_refuses_when_auto_comment_mode_is_shadow(self):
        self.write_config(auto_comment_mode="shadow")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "Checked: looks fine.", "comments": []})
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                             "--request", req, "--auto-comment"], env=self.base_env(),
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn("auto_comment.mode", r.stderr)
        self.assertIn("shadow", r.stderr)
        self.assertNotIn("digest:", r.stdout)
        self.assertEqual(self.log.read_text() if self.log.exists() else "", "")  # never even reached `gh`

    # --- refusal when mode is the kit default (off) too ---

    def test_refuses_when_auto_comment_mode_is_off(self):
        self.write_config(auto_comment_mode="off")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "Checked: looks fine.", "comments": []})
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                             "--request", req, "--auto-comment"], env=self.base_env(),
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn("not 'live'", r.stderr)

    # --- --auto-comment is COMMENT-only: never APPROVE/REQUEST_CHANGES, whatever mode says ---

    def test_refuses_a_non_comment_event(self):
        self.write_config(auto_comment_mode="live")
        req = self.write_request("req.json", {"event": "APPROVE", "body": "lgtm", "comments": []})
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                             "--request", req, "--auto-comment"], env=self.base_env(),
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("only for COMMENT", r.stderr)

    # --- never the user's own PR, even in live mode with a clean COMMENT request ---

    def test_refuses_the_users_own_pr(self):
        self.write_config(auto_comment_mode="live")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "Checked: looks fine.", "comments": []})
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                             "--request", req, "--auto-comment"], env=self.base_env(pr_author=ME),
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn("own PR", r.stderr)

    # --- --auto and --auto-comment are mutually exclusive ---

    def test_auto_and_auto_comment_are_mutually_exclusive(self):
        self.write_config(auto_comment_mode="live")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "x", "comments": []})
        r = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                             "--request", req, "--auto", "--auto-comment"], env=self.base_env(),
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("mutually exclusive", r.stderr)

    # --- ledger status: a live, clean --auto-comment submit writes status "auto_commented" ---

    def test_live_submit_writes_auto_commented_ledger_status(self):
        self.write_config(auto_comment_mode="live")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "Checked: looks fine.", "comments": []})
        env = self.base_env(pr_author="alice")
        preview = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                                   "--request", req, "--auto-comment"], env=env,
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        digest = next(l.split()[-1] for l in preview.stdout.splitlines() if l.startswith("digest:"))
        submit = subprocess.run(["bash", str(SUBMIT), "submit", "--repo", REPO, "--pr", PR, "--head", HEAD,
                                  "--request", req, "--auto-comment", "--confirm", digest], env=env,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(submit.returncode, 0, submit.stdout + submit.stderr)
        self.assertIn("status: submitted", submit.stdout)
        rows = self.ledger_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["status"], "auto_commented")
        self.assertEqual(rows[0]["event"], "COMMENT")
        self.assertEqual(rows[0]["repo"], REPO)
        self.assertEqual(rows[0]["pr"], int(PR))

    # --- an ordinary (non --auto-comment) submit keeps writing "reviewed", never "auto_commented" ---

    def test_plain_submit_still_writes_reviewed(self):
        self.write_config(auto_comment_mode="live")
        req = self.write_request("req.json", {"event": "COMMENT", "body": "Checked: looks fine.", "comments": []})
        env = self.base_env(pr_author="alice")
        preview = subprocess.run(["bash", str(SUBMIT), "preview", "--repo", REPO, "--pr", PR, "--head", HEAD,
                                   "--request", req], env=env, capture_output=True, text=True, timeout=30)
        digest = next(l.split()[-1] for l in preview.stdout.splitlines() if l.startswith("digest:"))
        submit = subprocess.run(["bash", str(SUBMIT), "submit", "--repo", REPO, "--pr", PR, "--head", HEAD,
                                  "--request", req, "--confirm", digest], env=env,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(submit.returncode, 0, submit.stdout + submit.stderr)
        rows = self.ledger_rows()
        self.assertEqual(rows[0]["status"], "reviewed")


if __name__ == "__main__":
    unittest.main()
