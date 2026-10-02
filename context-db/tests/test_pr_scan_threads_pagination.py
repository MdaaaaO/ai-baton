"""pr-scan.sh's `reviewThreads` GraphQL query used to ask for a bare `first:100` with no cursor — a PR with
more than 100 review threads silently truncated on page 1, which could hide a "mine" open thread (one we
opened that got a reply) forever on page 2: the row would never show as `follow_up` and would quietly fall
out of the queue. `fetch_threads()` now paginates (`pageInfo{hasNextPage endCursor}`, a `while` loop,
`-F c=<cursor>` on every page after the first) the same way `pr-merge.sh`'s `open_threads()` and
`reply-threads.sh` already do.

Against a stub `gh` whose GraphQL route answers differently depending on whether `-F c=` (the cursor) is
present — page 1 (no cursor) returns two resolved threads plus `hasNextPage: true`; page 2 (cursor present)
returns the one open "mine" thread plus `hasNextPage: false`. A single-page fetch sees only page 1 and
counts `threads.open`/`threads.mine` as 0/0; the paginated fetch merges both pages and counts 1/1. No
network. Stdlib unittest. Run: make -C .claude/context-db test."""
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
PR_NUM = 77
HEAD = "d" * 40

GH_STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
if [ "$1" = search ]; then printf '%s\n' "[]"; exit 0; fi
if [ "$1" = pr ] && [ "$2" = list ]; then printf '%s\n' "${STUB_SWEEP_JSON:-[]}"; exit 0; fi
[ "$1" = api ] || { echo "stub gh: unexpected call: $*" >&2; exit 9; }
shift
path=""; fval_c=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) shift 2 ;;
    --paginate) shift ;;
    -H) shift 2 ;;
    -F) key=${2%%=*}; val=${2#*=}; [ "$key" = c ] && fval_c=$val; shift 2 ;;
    -f) shift 2 ;;
    --input) shift 2 ;;
    *) [ -z "$path" ] && path=$1; shift ;;
  esac
done
case "$path" in
  graphql)
    if [ -n "$fval_c" ]; then printf '%s\n' "$STUB_GRAPHQL_PAGE2_JSON"
    else printf '%s\n' "$STUB_GRAPHQL_PAGE1_JSON"
    fi
    exit 0 ;;
  */pulls/*/reviews\?per_page=100)
    printf '%s\n' "${STUB_REVIEWS_JSON:-[]}"
    exit 0 ;;
  */pulls/*)
    printf '%s\n' "$STUB_PR_JSON"
    exit 0 ;;
  *) echo "stub gh: unhandled api path: $path" >&2; exit 9 ;;
esac
"""


def thread_node(resolved: bool, opener: str, last: str) -> dict:
    return {"isResolved": resolved,
            "opener": {"nodes": [{"author": {"login": opener}}]},
            "last": {"nodes": [{"author": {"login": last}}]}}


PAGE1 = {"data": {"repository": {"pullRequest": {"reviewThreads": {
    "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR1"},
    "nodes": [thread_node(True, "alice", "alice"), thread_node(True, "bob", "bob")],
}}}}}

# the thread page 1 alone would miss: resolved=false, WE opened it, the last reply is not ours — this
# is the "mine" open thread the truncation bug used to hide forever on a >100-thread PR.
PAGE2 = {"data": {"repository": {"pullRequest": {"reviewThreads": {
    "pageInfo": {"hasNextPage": False, "endCursor": None},
    "nodes": [thread_node(False, ME, "bob")],
}}}}}

PR_JSON = {
    "user": {"login": "alice"}, "draft": False, "updated_at": "2026-01-02T00:00:00Z", "state": "open",
    "head": {"sha": HEAD}, "base": {"ref": "main"}, "additions": 3, "deletions": 1, "changed_files": 1,
    "html_url": f"https://example.test/{REPO}/pull/{PR_NUM}", "title": f"pr {PR_NUM}",
    "requested_reviewers": [], "requested_teams": [],
}

SWEEP_JSON = [{"number": PR_NUM, "updatedAt": "2026-01-02T00:00:00Z", "isDraft": False,
               "author": {"login": "alice"}, "headRefOid": HEAD}]


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class ReviewThreadsPagination(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prscan-threadpage-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"; self.bin.mkdir()
        gh = self.bin / "gh"; gh.write_text(GH_STUB); gh.chmod(0o755)
        env_dir = self.tmp / "ws" / ".context" / "reference" / "env"; env_dir.mkdir(parents=True)
        env_dir.joinpath("config.json").write_text(json.dumps(
            {"github": {"review_bot": "", "sandbox_token_prefix": ""}}))
        state_dir = self.tmp / "ws" / ".context" / "state" / "pr-review"; state_dir.mkdir(parents=True)
        state_dir.joinpath("config.json").write_text(json.dumps({
            "login": ME, "owner": OWNER, "sweep_repos": [REPO], "days": 36500, "max_rows": 8,
            "enrich_cap": 40, "deep_lines": 600, "bots": [], "footer": "",
            "auto_approve": {"mode": "off", "bot_authors": []},
            "auto_comment": {"mode": "off", "prios": [1], "max_per_tick": 3},
        }))
        self.log = self.tmp / "gh.log"
        self.scanout = self.tmp / "scanout"

    def run_scan(self):
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "CONTEXT_ROOT": str(self.tmp / "ws" / ".context"),
            "PR_SCAN_OUT": str(self.scanout),
            "STUB_LOG": str(self.log),
            "STUB_SWEEP_JSON": json.dumps(SWEEP_JSON),
            "STUB_PR_JSON": json.dumps(PR_JSON),
            "STUB_REVIEWS_JSON": "[]",
            "STUB_GRAPHQL_PAGE1_JSON": json.dumps(PAGE1),
            "STUB_GRAPHQL_PAGE2_JSON": json.dumps(PAGE2),
        }
        return subprocess.run(["bash", str(PR_SCAN), "--quiet"], env=env, capture_output=True,
                               text=True, timeout=60)

    def row(self):
        queue = json.loads((self.scanout / "latest" / "queue.json").read_text())
        rows = [r for r in queue if r["pr"] == PR_NUM]
        self.assertEqual(len(rows), 1, queue)
        return rows[0]

    def test_a_mine_open_thread_on_page_two_is_not_lost(self):
        r = self.run_scan()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        threads = self.row()["threads"]
        self.assertEqual(threads["open"], 1, threads)
        self.assertEqual(threads["mine"], 1, threads)

    def test_the_cursor_is_sent_on_the_second_page_request(self):
        self.run_scan()
        calls = self.log.read_text()
        self.assertIn("-F c CURSOR1", calls.replace("=", " "))


if __name__ == "__main__":
    unittest.main()
