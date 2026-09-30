"""pr-scan.sh's unattended auto-COMMENT gate (`.auto_comment.eligible` in queue.json, config block
`auto_comment` in `.context/state/pr-review/config.json`, opt-in and off by default): eligibility is
computed inline, with no extra `gh` call, from the row's own prio/kind/author — never for a follow-up,
never for a bot author, and capped at `max_per_tick` rows per tick. This is the deterministic pre-filter
`pr-review/SKILL.md` § "Unattended auto-COMMENT path for direct review requests" spawns a `review-runner`
per row of; the fork that reports the queue never spawns anything itself.

Against a stub `gh` (and, for the bot-author case, a `python3` shim that fakes an eligible
`trivial-check.py` verdict without running the real gate — that gate's own `gh` surface is exercised by
`test_pr_review_scripts.py` / the trivial-check tests, not here). No network. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
PR_SCAN = KIT / "skills" / "pr-scan" / "pr-scan.sh"
OWNER = "acme"
REPO = "acme/widgets"
ME = "tester"

EMPTY_THREADS = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}


def follow_up_threads(opener: str, last: str) -> dict:
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
        {"isResolved": False,
         "opener": {"nodes": [{"author": {"login": opener}}]},
         "last": {"nodes": [{"author": {"login": last}}]}},
    ]}}}}}


def pr_json(num: int, author: str, head: str, **extra) -> dict:
    base = {
        "user": {"login": author}, "draft": False, "updated_at": "2026-01-02T00:00:00Z", "state": "open",
        "head": {"sha": head}, "base": {"ref": "main"}, "additions": 3, "deletions": 1, "changed_files": 1,
        "html_url": f"https://example.test/{REPO}/pull/{num}", "title": f"pr {num}",
        "requested_reviewers": [], "requested_teams": [],
    }
    base.update(extra)
    return base


def direct_entry(num: int, author: str, updated: str) -> dict:
    return {"repository": {"nameWithOwner": REPO}, "number": num, "updatedAt": updated,
            "isDraft": False, "author": {"login": author, "is_bot": False}}


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

# a `python3` shim that fakes trivial-check.py as eligible (patch-bump) — everything else (kit_profile.py
# calls pr-scan.sh and bot-verdict.sh make) passes through to the real interpreter unchanged.
PY_SHIM = r"""#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in
    *trivial-check.py)
      printf '%s\n' '{"eligible": true, "class": "patch-bump", "reasons": [], "packages": []}'
      exit 0 ;;
  esac
done
exec "__REAL_PYTHON3__" "$@"
"""


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class AutoCommentGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prscan-autocomment-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"; self.bin.mkdir()
        gh = self.bin / "gh"; gh.write_text(GH_STUB); gh.chmod(0o755)
        env_dir = self.tmp / "ws" / ".context" / "reference" / "env"; env_dir.mkdir(parents=True)
        env_dir.joinpath("config.json").write_text(json.dumps(
            {"github": {"review_bot": "", "sandbox_token_prefix": ""}}))
        self.state_dir = self.tmp / "ws" / ".context" / "state" / "pr-review"; self.state_dir.mkdir(parents=True)
        self.log = self.tmp / "gh.log"
        self.out = self.tmp / "out"

    def write_config(self, **auto_comment) -> None:
        cfg = {
            "login": ME, "owner": OWNER, "sweep_repos": [], "days": 14, "max_rows": 8, "enrich_cap": 40,
            "deep_lines": 600, "bots": ["dependabot[bot]"], "footer": "",
            "auto_approve": {"mode": "off", "bot_authors": []},
            "auto_comment": {"mode": "shadow", "prios": [1], "max_per_tick": 3, **auto_comment},
        }
        self.state_dir.joinpath("config.json").write_text(json.dumps(cfg))

    def env(self, use_py_shim: bool = False, **stub) -> dict:
        path = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        if use_py_shim:
            shim_dir = self.tmp / "pyshim"; shim_dir.mkdir(exist_ok=True)
            shim = shim_dir / "python3"
            shim.write_text(PY_SHIM.replace("__REAL_PYTHON3__", sys.executable))
            shim.chmod(0o755)
            path = f"{shim_dir}{os.pathsep}{path}"
        base = {k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "PR_REVIEW_HOME", "PR_SCAN_OUT")}
        base.update(
            PATH=path, CONTEXT_ROOT=str(self.tmp / "ws" / ".context"), PR_SCAN_OUT=str(self.tmp / "scanout"),
            STUB_LOG=str(self.log), STUB_GRAPHQL_DEFAULT=json.dumps(EMPTY_THREADS),
        )
        base.update(stub)
        return base

    def run_scan(self, env: dict):
        return subprocess.run(["bash", str(PR_SCAN), "--quiet"], env=env, capture_output=True, text=True, timeout=60)

    def queue(self):
        latest = self.tmp / "scanout" / "latest"
        return json.loads((latest / "queue.json").read_text())

    def row(self, num: int):
        rows = [r for r in self.queue() if r["pr"] == num]
        self.assertEqual(len(rows), 1, self.queue())
        return rows[0]

    # --- prio filter: a direct review request (prio 1, in `auto_comment.prios`) is eligible ---

    def test_direct_request_in_configured_prios_is_eligible(self):
        self.write_config(prios=[1])
        env = self.env(
            STUB_DIRECT_JSON=json.dumps([direct_entry(101, "alice", "2026-01-02T00:00:00Z")]),
            STUB_PR_101_JSON=json.dumps(pr_json(101, "alice", "a" * 40)),
            STUB_REVIEWS_101_JSON="[]",
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(101)
        self.assertEqual(row["prio"], 1)
        self.assertEqual(row["kind"], "new")
        self.assertTrue(row["auto_comment"]["eligible"], row["auto_comment"])

    # --- prio filter: the same row is NOT eligible once its prio is dropped from the configured list ---

    def test_prio_not_in_configured_list_is_not_eligible(self):
        self.write_config(prios=[3])  # direct requests are always prio 1 — never 3
        env = self.env(
            STUB_DIRECT_JSON=json.dumps([direct_entry(102, "alice", "2026-01-02T00:00:00Z")]),
            STUB_PR_102_JSON=json.dumps(pr_json(102, "alice", "b" * 40)),
            STUB_REVIEWS_102_JSON="[]",
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(102)
        self.assertEqual(row["prio"], 1)
        self.assertFalse(row["auto_comment"]["eligible"])
        self.assertIn("prio", row["auto_comment"]["reason"])

    # --- author not a bot: a bot author that survived the pre-existing bot-drop (AUTO_BOTS + trivial-
    # eligible) must still never qualify for the unattended auto-COMMENT path ---

    def test_bot_author_is_not_eligible_even_when_auto_approve_eligible(self):
        self.write_config(prios=[1])
        env = self.env(
            use_py_shim=True,
            STUB_DIRECT_JSON=json.dumps([direct_entry(103, "dependabot[bot]", "2026-01-02T00:00:00Z")]),
            STUB_PR_103_JSON=json.dumps(pr_json(103, "dependabot[bot]", "c" * 40)),
            STUB_REVIEWS_103_JSON="[]",
        )
        # this scenario needs the trivial-approve gate to pass (bot_authors + eligible) or the bot is
        # dropped from the queue entirely before it ever reaches the auto-COMMENT gate — see the module
        # docstring: that gate's own `gh` surface is exercised elsewhere, here it is faked eligible.
        cfg = json.loads(self.state_dir.joinpath("config.json").read_text())
        cfg["auto_approve"] = {"mode": "shadow", "bot_authors": ["dependabot[bot]"], "max_files": 10, "max_lines": 200}
        self.state_dir.joinpath("config.json").write_text(json.dumps(cfg))
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(103)
        self.assertEqual(row["prio"], 1)  # it did survive the bot-drop and reach the gate
        self.assertFalse(row["auto_comment"]["eligible"])
        self.assertIn("bot", row["auto_comment"]["reason"])

    # --- follow-up excluded: a direct request is still prio 1 even when it is a follow-up (kind stays
    # relevant to auto-COMMENT even though it does not move prio for src=="direct") ---

    def test_follow_up_is_not_eligible_even_at_prio_1(self):
        self.write_config(prios=[1])
        head = "d" * 40
        env = self.env(
            STUB_DIRECT_JSON=json.dumps([direct_entry(104, "alice", "2026-01-02T00:00:00Z")]),
            STUB_PR_104_JSON=json.dumps(pr_json(104, "alice", head)),
            STUB_REVIEWS_104_JSON=json.dumps([
                {"user": {"login": ME}, "state": "APPROVED", "body": "", "commit_id": head},
            ]),
            STUB_GRAPHQL_104_JSON=json.dumps(follow_up_threads(ME, "alice")),
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(104)
        self.assertEqual(row["prio"], 1)
        self.assertEqual(row["kind"], "follow_up")
        self.assertFalse(row["auto_comment"]["eligible"])
        self.assertEqual(row["auto_comment"]["reason"], "follow-up")

    # --- cap: at most `max_per_tick` rows are marked eligible per tick, newest (processed) first ---

    def test_cap_limits_eligible_rows_to_max_per_tick(self):
        self.write_config(prios=[1], max_per_tick=1)
        env = self.env(
            STUB_DIRECT_JSON=json.dumps([
                direct_entry(201, "alice", "2026-01-03T00:00:00Z"),   # newer — processed first
                direct_entry(202, "bob", "2026-01-02T00:00:00Z"),
            ]),
            STUB_PR_201_JSON=json.dumps(pr_json(201, "alice", "e" * 40)),
            STUB_REVIEWS_201_JSON="[]",
            STUB_PR_202_JSON=json.dumps(pr_json(202, "bob", "f" * 40)),
            STUB_REVIEWS_202_JSON="[]",
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row201, row202 = self.row(201), self.row(202)
        self.assertTrue(row201["auto_comment"]["eligible"], row201["auto_comment"])
        self.assertFalse(row202["auto_comment"]["eligible"])
        self.assertIn("max_per_tick", row202["auto_comment"]["reason"])

    # --- mode off (the kit default): the gate never marks anything eligible ---

    def test_mode_off_never_marks_eligible(self):
        self.write_config(mode="off", prios=[1])
        env = self.env(
            STUB_DIRECT_JSON=json.dumps([direct_entry(105, "alice", "2026-01-02T00:00:00Z")]),
            STUB_PR_105_JSON=json.dumps(pr_json(105, "alice", "1" * 40)),
            STUB_REVIEWS_105_JSON="[]",
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(105)
        self.assertFalse(row["auto_comment"]["eligible"])
        self.assertIn("auto_comment=0", r.stdout)


if __name__ == "__main__":
    unittest.main()
