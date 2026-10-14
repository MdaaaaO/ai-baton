"""pr-scan.sh's `approved_direct` switch (top-level key in `.context/state/pr-review/config.json`):
what a `new` DIRECT review request does once another human has approved it.

  keep (default, also when the key is missing) - the row stays, nothing changes
  mark  - the row stays; pr-scan.sh sets `approved_mark: true` on it and row-state.py ends its
          `state_label` with ` · approved by N`
  drop  - the row goes, counted in `dropped(approved=…)` like a team or sweep row with an approval

Only a `kind=new` row with a complete reviews fetch is affected: a `re_review` row, and a row whose
reviews fetch failed (degraded), never drops and never gets marked. An unknown value reads as `keep`
and prints one warning line to stderr; it is not an `errors=` count and not a non-zero exit.

Against a stub `gh` (same pattern as `test_pr_scan_auto_comment.py`). No network. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
PR_SCAN = KIT / "skills" / "pr-scan" / "pr-scan.sh"
OWNER = "acme"
REPO = "acme/widgets"
ME = "tester"

EMPTY_THREADS = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}

GH_STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
if [ "$1" = search ]; then
  shift 2  # drop "search prs"
  is_team=0
  for a in "$@"; do case "$a" in --review-requested=*) is_team=1 ;; esac; done
  if [ "$is_team" = 1 ]; then printf '%s\n' "${STUB_TEAM_JSON:-[]}"
  else printf '%s\n' "${STUB_DIRECT_JSON:-[]}"
  fi
  exit 0
fi
if [ "$1" = pr ] && [ "$2" = list ]; then printf '%s\n' "${STUB_SWEEP_JSON:-[]}"; exit 0; fi
[ "$1" = api ] || { echo "stub gh: unexpected call: $*" >&2; exit 9; }
shift
path=""; fval_n=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) shift 2 ;;
    --paginate) shift ;;
    -H) shift 2 ;;
    -F) key=${2%%=*}; val=${2#*=}; [ "$key" = n ] && fval_n=$val; shift 2 ;;
    -f) shift 2 ;;
    --input) shift 2 ;;
    *) [ -z "$path" ] && path=$1; shift ;;
  esac
done
case "$path" in
  graphql)
    var="STUB_GRAPHQL_${fval_n}_JSON"
    printf '%s\n' "${!var:-$STUB_GRAPHQL_DEFAULT}"
    exit 0 ;;
  */pulls/*/reviews\?per_page=100)
    num=$(printf '%s' "$path" | sed -E 's#.*/pulls/([0-9]+)/.*#\1#')
    var="STUB_REVIEWS_${num}_JSON"
    printf '%s\n' "${!var:-[]}"
    exit 0 ;;
  */pulls/*)
    num=$(printf '%s' "$path" | sed -E 's#.*/pulls/([0-9]+)$#\1#')
    var="STUB_PR_${num}_JSON"
    printf '%s\n' "${!var}"
    exit 0 ;;
  *) echo "stub gh: unhandled api path: $path" >&2; exit 9 ;;
esac
"""

# no key in the config: the same as `keep`
MISSING = object()


def pr_json(num: int, author: str, head: str) -> dict:
    return {
        "user": {"login": author}, "draft": False, "updated_at": "2026-01-02T00:00:00Z", "state": "open",
        "head": {"sha": head}, "base": {"ref": "main"}, "additions": 3, "deletions": 1, "changed_files": 1,
        "html_url": f"https://example.test/{REPO}/pull/{num}", "title": f"pr {num}",
        "requested_reviewers": [], "requested_teams": [],
    }


def direct_entry(num: int, author: str) -> dict:
    return {"repository": {"nameWithOwner": REPO}, "number": num, "updatedAt": "2026-01-02T00:00:00Z",
            "isDraft": False, "author": {"login": author, "is_bot": False}}


def review(login: str, state: str, head: str, review_id: int = 1) -> dict:
    return {"id": review_id, "user": {"login": login}, "state": state, "body": "", "commit_id": head}


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class ApprovedDirect(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prscan-approveddirect-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"; self.bin.mkdir()
        gh = self.bin / "gh"; gh.write_text(GH_STUB); gh.chmod(0o755)
        env_dir = self.tmp / "ws" / ".context" / "reference" / "env"; env_dir.mkdir(parents=True)
        env_dir.joinpath("config.json").write_text(json.dumps(
            {"github": {"review_bot": "", "sandbox_token_prefix": ""}}))
        self.state_dir = self.tmp / "ws" / ".context" / "state" / "pr-review"; self.state_dir.mkdir(parents=True)
        self.log = self.tmp / "gh.log"

    def write_config(self, approved_direct=MISSING) -> None:
        cfg = {
            "login": ME, "owner": OWNER, "sweep_repos": [], "days": 14, "max_rows": 8, "enrich_cap": 40,
            "deep_lines": 600, "bots": ["dependabot[bot]"], "footer": "",
            "auto_approve": {"mode": "off", "bot_authors": []},
            "auto_comment": {"mode": "off", "prios": [1], "max_per_tick": 3},
        }
        if approved_direct is not MISSING:
            cfg["approved_direct"] = approved_direct
        self.state_dir.joinpath("config.json").write_text(json.dumps(cfg))

    def env(self, **stub) -> dict:
        base = {k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "PR_REVIEW_HOME", "PR_SCAN_OUT")}
        base.update(
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", CONTEXT_ROOT=str(self.tmp / "ws" / ".context"),
            PR_SCAN_OUT=str(self.tmp / "scanout"), STUB_LOG=str(self.log),
            STUB_GRAPHQL_DEFAULT=json.dumps(EMPTY_THREADS),
        )
        base.update(stub)
        return base

    def run_scan(self, env: dict):
        return subprocess.run(["bash", str(PR_SCAN), "--quiet"], env=env, capture_output=True, text=True, timeout=60)

    def queue(self) -> list:
        return json.loads((self.tmp / "scanout" / "latest" / "queue.json").read_text())

    def row(self, num: int) -> dict:
        rows = [r for r in self.queue() if r["pr"] == num]
        self.assertEqual(len(rows), 1, self.queue())
        return rows[0]

    def errors_text(self) -> str:
        return (self.tmp / "scanout" / "latest" / "errors.txt").read_text()

    def direct_env(self, num: int, head: str, reviews: list | str, author: str = "alice", **stub) -> dict:
        """One direct request, `num` at `head`, whose reviews endpoint answers `reviews`."""
        body = reviews if isinstance(reviews, str) else json.dumps(reviews)
        return self.env(**{
            "STUB_DIRECT_JSON": json.dumps([direct_entry(num, author)]),
            f"STUB_PR_{num}_JSON": json.dumps(pr_json(num, author, head)),
            f"STUB_REVIEWS_{num}_JSON": body,
            **stub,
        })

    # --- keep, and a missing key: the row stays, nothing is added to it ---

    def test_keep_leaves_an_approved_direct_row_alone(self):
        self.write_config("keep")
        head = "a" * 40
        r = self.run_scan(self.direct_env(101, head, [review("bob", "APPROVED", head)]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(101)
        self.assertEqual((row["kind"], row["prio"], row["approved"]), ("new", 1, 1))
        self.assertFalse(row.get("approved_mark"))
        self.assertEqual(row["state_label"], "new (manual)")
        self.assertIn("approved=0 ", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_a_missing_key_behaves_as_keep(self):
        self.write_config()   # the key is not in the file at all
        head = "b" * 40
        r = self.run_scan(self.direct_env(102, head, [review("bob", "APPROVED", head)]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(102)
        self.assertFalse(row.get("approved_mark"))
        self.assertEqual(row["state_label"], "new (manual)")
        self.assertIn("approved=0 ", r.stdout)
        self.assertEqual(r.stderr, "")

    # --- mark: the row stays, the row field is set, the label ends with the approval count ---

    def test_mark_keeps_the_row_and_adds_the_label_suffix(self):
        self.write_config("mark")
        head = "c" * 40
        r = self.run_scan(self.direct_env(103, head, [
            review("bob", "APPROVED", head, 1), review("carol", "APPROVED", head, 2),
            review("dave", "CHANGES_REQUESTED", head, 3),
        ]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(103)
        self.assertEqual((row["kind"], row["prio"], row["approved"]), ("new", 1, 2))
        self.assertIs(row["approved_mark"], True)
        self.assertEqual((row["state"], row["section"]), ("new", "New — not started"))   # only the label changes
        self.assertEqual(row["state_label"], "new (manual) · approved by 2")
        self.assertIn("approved=0 ", r.stdout)   # kept, so not counted as dropped
        self.assertIn(" shown=1 ", r.stdout)

    def test_mark_does_not_touch_a_direct_row_with_no_approval(self):
        self.write_config("mark")
        head = "d" * 40
        r = self.run_scan(self.direct_env(104, head, [review("bob", "CHANGES_REQUESTED", head)]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(104)
        self.assertFalse(row.get("approved_mark"))
        self.assertEqual(row["state_label"], "new (manual)")

    def test_mark_never_marks_a_team_row_because_it_drops(self):
        self.write_config("mark")
        head = "e" * 40
        env = self.env(
            STUB_TEAM_JSON=json.dumps([direct_entry(105, "alice")]),
            STUB_PR_105_JSON=json.dumps(pr_json(105, "alice", head)),
            STUB_REVIEWS_105_JSON=json.dumps([review("bob", "APPROVED", head)]),
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.queue(), [])     # the pre-existing team drop is not changed by `mark`
        self.assertIn("approved=1 ", r.stdout)

    # --- drop: the row goes and is counted in the existing `approved` bucket ---

    def test_drop_removes_the_row_and_counts_it(self):
        self.write_config("drop")
        head = "f" * 40
        r = self.run_scan(self.direct_env(106, head, [review("bob", "APPROVED", head)]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.queue(), [])
        self.assertIn(" approved=1 cap=", r.stdout)   # `auto_mode=` also sits on the line, so match through `cap=`
        self.assertIn(" shown=0 ", r.stdout)
        self.assertIn(" errors=0 ", r.stdout)

    def test_drop_keeps_a_direct_row_that_has_no_approval(self):
        self.write_config("drop")
        head = "1" * 40
        r = self.run_scan(self.direct_env(107, head, [review("bob", "COMMENTED", head)]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.row(107)["kind"], "new")
        self.assertIn(" approved=0 cap=", r.stdout)

    # --- guards: a degraded row, and any kind other than `new`, is never dropped or marked ---

    def test_a_degraded_direct_row_is_never_dropped_or_marked(self):
        for mode in ("drop", "mark"):
            with self.subTest(mode=mode):
                self.write_config(mode)
                head = "2" * 40
                # an answer that is not JSON makes the reviews fetch fail after its retry: the row degrades
                r = self.run_scan(self.direct_env(108, head, "{not json"))
                self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)   # a failed gh call is a counted error
                self.assertIn(" degraded=1 ", r.stdout)
                self.assertIn(" approved=0 cap=", r.stdout)
                row = self.row(108)
                self.assertEqual(row["degraded"], ["reviews"])
                self.assertFalse(row.get("approved_mark"))
                self.assertEqual(row["state_label"], "new (manual)")

    def test_a_direct_re_review_row_with_approvals_is_never_dropped_or_marked(self):
        for mode in ("drop", "mark"):
            with self.subTest(mode=mode):
                self.write_config(mode)
                old_head, new_head = "3" * 40, "4" * 40
                r = self.run_scan(self.direct_env(109, new_head, [
                    review(ME, "APPROVED", old_head, 1), review("bob", "APPROVED", new_head, 2),
                ]))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                row = self.row(109)
                self.assertEqual((row["kind"], row["approved"]), ("re_review", 1))
                self.assertFalse(row.get("approved_mark"))
                self.assertEqual(row["state_label"], "follow-up (manual)")
                self.assertIn(" approved=0 cap=", r.stdout)

    # --- an unknown value: read as keep, one stderr line, no error count, exit 0 ---

    def test_an_unknown_value_reads_as_keep_with_one_stderr_warning(self):
        self.write_config("Drop")   # the match is exact: case matters
        head = "5" * 40
        r = self.run_scan(self.direct_env(110, head, [review("bob", "APPROVED", head)]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(110)
        self.assertFalse(row.get("approved_mark"))
        self.assertEqual(row["state_label"], "new (manual)")
        self.assertIn(" approved=0 cap=", r.stdout)
        self.assertIn(" errors=0 ", r.stdout)
        warnings = [ln for ln in r.stderr.splitlines() if ln.strip()]
        self.assertEqual(len(warnings), 1, r.stderr)
        self.assertIn("approved_direct", warnings[0])
        self.assertIn("Drop", warnings[0])
        self.assertEqual(self.errors_text(), "")

    def test_a_value_that_is_not_a_plain_string_still_gives_one_warning_line(self):
        for bad in ({"mode": "drop"}, True, 7, "drop\nmark", ""):
            with self.subTest(value=bad):
                self.write_config(bad)
                head = "8" * 40
                r = self.run_scan(self.direct_env(113, head, [review("bob", "APPROVED", head)]))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertFalse(self.row(113).get("approved_mark"))
                self.assertIn(" approved=0 cap=", r.stdout)
                self.assertEqual(len([ln for ln in r.stderr.splitlines() if ln.strip()]), 1, r.stderr)
                self.assertEqual(self.errors_text(), "")

    def test_a_valid_value_prints_no_warning(self):
        for mode in ("keep", "mark", "drop"):
            with self.subTest(mode=mode):
                self.write_config(mode)
                head = "6" * 40
                r = self.run_scan(self.direct_env(111, head, [review("bob", "APPROVED", head)]))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(r.stderr, "")

    def test_mark_only_prints_no_warning_for_an_unknown_value(self):
        self.write_config("Drop")
        head = "7" * 40
        env = self.direct_env(112, head, [])
        self.assertEqual(self.run_scan(env).returncode, 0)   # the sweep warns; the separate mark step stays quiet
        run_dir = str(self.tmp / "scanout" / "latest")
        m = subprocess.run(["bash", str(PR_SCAN), "--mark-only", "--run", run_dir], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(m.returncode, 0, m.stdout + m.stderr)
        self.assertEqual(m.stderr, "")


if __name__ == "__main__":
    unittest.main()
