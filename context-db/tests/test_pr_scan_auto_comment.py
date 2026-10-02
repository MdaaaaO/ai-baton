"""pr-scan.sh's unattended auto-COMMENT gate (`.auto_comment.eligible` in queue.json, config block
`auto_comment` in `.context/state/pr-review/config.json`, opt-in and off by default): eligibility is
computed inline, with no extra `gh` call, from the row's own prio/kind/author — never for a follow-up,
never for a bot author, and capped at `max_per_tick` rows per tick. This is the deterministic pre-filter
`pr-review/SKILL.md` § "Unattended auto-COMMENT path for direct review requests" spawns a `review-runner`
per row of; the fork that reports the queue never spawns anything itself.

Against a stub `gh` (and, for the bot-author case, a `python3` shim that fakes an eligible
`trivial-check.py` verdict without running the real gate — that gate's own `gh` surface is exercised by
`test_pr_review_scripts.py` / the trivial-check tests, not here). No network. Stdlib unittest.

`ReviewedHeadRows` drives the same stub through the rows whose head already carries our review: which ones
stay in the queue (and in which brief section `row-state.py` puts them), which drop, and that a failing
`row-state.py` is a counted sweep error. Run: make -C .claude/context-db test."""
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

# a `python3` shim that fails `row-state.py` the way a crash would — everything else passes through.
PY_SHIM_ROW_STATE_FAILS = r"""#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in
    *row-state.py) echo "row-state: boom" >&2; exit 1 ;;
  esac
done
exec "__REAL_PYTHON3__" "$@"
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


class ScanHarness:
    """A workspace, a stub `gh` and the helpers to run one sweep and read its queue.json back."""

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

    def env(self, use_py_shim: bool = False, py_shim: str = PY_SHIM, **stub) -> dict:
        path = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        if use_py_shim:
            shim_dir = self.tmp / "pyshim"; shim_dir.mkdir(exist_ok=True)
            shim = shim_dir / "python3"
            shim.write_text(py_shim.replace("__REAL_PYTHON3__", sys.executable))
            shim.chmod(0o755)
            path = f"{shim_dir}{os.pathsep}{path}"
        base = {k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "PR_REVIEW_HOME", "PR_SCAN_OUT")}
        base.update(
            PATH=path, CONTEXT_ROOT=str(self.tmp / "ws" / ".context"), PR_SCAN_OUT=str(self.tmp / "scanout"),
            STUB_LOG=str(self.log), STUB_GRAPHQL_DEFAULT=json.dumps(EMPTY_THREADS),
        )
        base.update(stub)
        return base

    def run_scan(self, env: dict, *args: str):
        return subprocess.run(["bash", str(PR_SCAN), "--quiet", *args], env=env, capture_output=True, text=True, timeout=60)

    def mark_only(self, env: dict):
        """Marks the most recent sweep's shown rows as surfaced — the main session's step, run separately
        from the (read-only) sweep itself."""
        return subprocess.run(["bash", str(PR_SCAN), "--mark-only"], env=env, capture_output=True, text=True, timeout=60)

    def queue(self):
        latest = self.tmp / "scanout" / "latest"
        return json.loads((latest / "queue.json").read_text())

    def row(self, num: int):
        rows = [r for r in self.queue() if r["pr"] == num]
        self.assertEqual(len(rows), 1, self.queue())
        return rows[0]

    def write_ledger(self, rows: list) -> None:
        self.state_dir.joinpath("ledger.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows))


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class AutoCommentGate(ScanHarness, unittest.TestCase):

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

    # --- re-review excluded by default: a direct re-request on a PR we already reviewed on an older head
    # (kind=re_review) must not ride the trailer on the strength of the prior review's prio alone ---

    def test_re_review_is_not_eligible_by_default(self):
        self.write_config(prios=[1])
        old_head = "6" * 40
        new_head = "7" * 40
        env = self.env(
            STUB_DIRECT_JSON=json.dumps([direct_entry(501, "alice", "2026-01-02T00:00:00Z")]),
            STUB_PR_501_JSON=json.dumps(pr_json(501, "alice", new_head)),
            STUB_REVIEWS_501_JSON=json.dumps([
                {"user": {"login": ME}, "state": "APPROVED", "body": "", "commit_id": old_head},
            ]),
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(501)
        self.assertEqual(row["prio"], 1)
        self.assertEqual(row["kind"], "re_review")
        self.assertFalse(row["auto_comment"]["eligible"])
        self.assertEqual(row["auto_comment"]["reason"], "re-review")

    # --- re-review opted back in: `include_re_review: true` makes the same row eligible again ---

    def test_re_review_is_eligible_when_include_re_review_true(self):
        self.write_config(prios=[1], include_re_review=True)
        old_head = "8" * 40
        new_head = "9" * 40
        env = self.env(
            STUB_DIRECT_JSON=json.dumps([direct_entry(502, "alice", "2026-01-02T00:00:00Z")]),
            STUB_PR_502_JSON=json.dumps(pr_json(502, "alice", new_head)),
            STUB_REVIEWS_502_JSON=json.dumps([
                {"user": {"login": ME}, "state": "APPROVED", "body": "", "commit_id": old_head},
            ]),
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(502)
        self.assertEqual(row["kind"], "re_review")
        self.assertTrue(row["auto_comment"]["eligible"], row["auto_comment"])

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

    # --- a head already `shadow_comment`-decided is fully "done" for this path, the same way a
    # `shadow_approve` head already is (PR#373 review): it must not resurface as `kind=new` and steal a
    # `max_per_tick` slot a fresh direct request needs ---

    def test_shadow_comment_row_is_dropped_and_never_counts_toward_cap(self):
        self.write_config(prios=[1], max_per_tick=1)
        shadow_head = "2" * 40
        fresh_head = "3" * 40
        self.write_ledger([{"repo": REPO, "pr": 301, "head": shadow_head, "status": "shadow_comment",
                             "ts": "2026-01-01T00:00:00Z"}])
        env = self.env(
            STUB_DIRECT_JSON=json.dumps([
                direct_entry(301, "alice", "2026-01-03T00:00:00Z"),   # newer — processed first, already shadow-decided
                direct_entry(302, "bob", "2026-01-02T00:00:00Z"),
            ]),
            STUB_PR_301_JSON=json.dumps(pr_json(301, "alice", shadow_head)),
            STUB_REVIEWS_301_JSON="[]",
            STUB_PR_302_JSON=json.dumps(pr_json(302, "bob", fresh_head)),
            STUB_REVIEWS_302_JSON="[]",
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        nums = [row["pr"] for row in self.queue()]
        self.assertNotIn(301, nums, self.queue())        # dropped like shadow_approve — done for this head
        row302 = self.row(302)
        self.assertTrue(row302["auto_comment"]["eligible"], row302["auto_comment"])  # cap slot not stolen

    # --- a head already `held` (an `on_stop: hold` STOP decision) must stay an ordinary queue row for the
    # next interactive `pr-review` walk, but must never be re-flagged `C` or counted toward `max_per_tick`
    # again until the head changes (PR#373 review) ---

    def test_held_row_stays_visible_but_is_never_reflagged_or_counted(self):
        self.write_config(prios=[1], max_per_tick=1)
        held_head = "4" * 40
        fresh_head = "5" * 40
        self.write_ledger([{"repo": REPO, "pr": 401, "head": held_head, "status": "held",
                             "ts": "2026-01-01T00:00:00Z"}])
        env = self.env(
            STUB_DIRECT_JSON=json.dumps([
                direct_entry(401, "alice", "2026-01-03T00:00:00Z"),   # newer — processed first, already held
                direct_entry(402, "bob", "2026-01-02T00:00:00Z"),
            ]),
            STUB_PR_401_JSON=json.dumps(pr_json(401, "alice", held_head)),
            STUB_REVIEWS_401_JSON="[]",
            STUB_PR_402_JSON=json.dumps(pr_json(402, "bob", fresh_head)),
            STUB_REVIEWS_402_JSON="[]",
        )
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row401 = self.row(401)   # still an ordinary row — the interactive walk still needs to see it
        self.assertFalse(row401["auto_comment"]["eligible"])
        self.assertEqual(row401["auto_comment"]["reason"], "held")
        row402 = self.row(402)
        self.assertTrue(row402["auto_comment"]["eligible"], row402["auto_comment"])  # cap slot not stolen


def my_review(state: str, head: str, review_id: int) -> dict:
    return {"id": review_id, "user": {"login": ME}, "state": state, "body": "", "commit_id": head}


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class ReviewedHeadRows(ScanHarness, unittest.TestCase):
    """A head that already carries our review, with no reply waiting on us: the row stays in the queue
    as `kind=done` while our verdict stands, once for a review the kit posted, and drops otherwise."""

    def sweep_env(self, entries: dict, **stub) -> dict:
        """entries: pr number -> (head, my reviews on it); every PR arrives via the repo sweep."""
        cfg = json.loads(self.state_dir.joinpath("config.json").read_text())
        cfg["sweep_repos"] = [REPO]
        self.state_dir.joinpath("config.json").write_text(json.dumps(cfg))
        sweep = [{"number": n, "updatedAt": "2099-01-01T00:00:00Z", "isDraft": False,
                  "author": {"login": "alice", "is_bot": False}, "headRefOid": head}
                 for n, (head, _) in entries.items()]
        env = {"STUB_SWEEP_JSON": json.dumps(sweep)}
        for n, (head, reviews) in entries.items():
            env[f"STUB_PR_{n}_JSON"] = json.dumps(pr_json(n, "alice", head, updated_at="2099-01-01T00:00:00Z"))
            env[f"STUB_REVIEWS_{n}_JSON"] = json.dumps(reviews)
        env.update(stub)
        return self.env(**env)

    def test_our_verdict_on_the_current_head_keeps_the_row_as_watching(self):
        self.write_config(prios=[1])
        h1, h2, h3 = "a" * 40, "b" * 40, "c" * 40
        env = self.sweep_env({
            701: (h1, [my_review("APPROVED", h1, 7001)]),
            702: (h2, [my_review("CHANGES_REQUESTED", h2, 7002)]),
            703: (h3, []),
        })
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for num, label in ((701, "watching (you approved)"), (702, "watching (you requested changes)")):
            row = self.row(num)
            self.assertEqual((row["kind"], row["prio"], row["section"], row["state_label"]),
                             ("done", 6, "Watching", label), row)
            self.assertFalse(row["auto_comment"]["eligible"])
        self.assertEqual(self.row(703)["section"], "New — not started")
        self.assertEqual([row["pr"] for row in self.queue()][0], 703)   # a reviewed row never outranks an open one
        self.assertIn(" new=1 ", r.stdout)                              # only the unreviewed PR counts as new
        # the main session's step, not the sweep's: marks this run's shown rows as surfaced
        m = self.mark_only(env)
        self.assertEqual(m.returncode, 0, m.stdout + m.stderr)
        # still open, same heads: the next sweep keeps watching them and has nothing new to report
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.row(701)["section"], "Watching")
        self.assertIn(" new=0 ", r.stdout)

    def test_a_reviewed_head_alone_is_not_news(self):
        self.write_config(prios=[1])
        head = "d" * 40
        r = self.run_scan(self.sweep_env({704: (head, [my_review("APPROVED", head, 7004)])}))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.row(704)["section"], "Watching")
        self.assertIn(" new=0 ", r.stdout)   # the fork answers NO-OP: our own review is not a reason for a brief

    def test_a_kit_posted_comment_is_handled_once_then_dropped(self):
        self.write_config(prios=[1])
        head, other = "e" * 40, "f" * 40
        self.write_ledger([{"repo": REPO, "pr": 705, "head": head, "status": "auto_commented", "event": "COMMENT",
                            "review_id": 7005, "comments": 2, "ts": "2026-01-01T00:00:00Z"}])
        env = self.sweep_env({705: (head, [my_review("COMMENTED", head, 7005)]), 706: (other, [])})
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(705)
        self.assertEqual((row["kind"], row["section"], row["state_label"]),
                         ("done", "Handled this tick", "handled (review #7005)"), row)
        m = self.mark_only(env)
        self.assertEqual(m.returncode, 0, m.stdout + m.stderr)
        r = self.run_scan(env)   # shown and marked above: this sweep drops it as done
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(705, [row["pr"] for row in self.queue()], self.queue())
        self.assertIn("done=1 ", r.stdout)

    def test_a_comment_posted_outside_the_kit_still_drops_as_done(self):
        self.write_config(prios=[1])
        head = "1" * 40
        r = self.run_scan(self.sweep_env({707: (head, [my_review("COMMENTED", head, 7007)])}))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.queue(), [])
        self.assertIn("done=1 ", r.stdout)

    def test_a_reply_in_our_thread_outranks_the_verdict_we_left(self):
        self.write_config(prios=[1])
        head = "2" * 40
        env = self.sweep_env({708: (head, [my_review("CHANGES_REQUESTED", head, 7008)])},
                             STUB_GRAPHQL_708_JSON=json.dumps(follow_up_threads(ME, "alice")))
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self.row(708)
        self.assertEqual((row["kind"], row["section"], row["state_label"]),
                         ("follow_up", "Needs you", "needs you (author replied)"), row)

    def test_a_failing_row_state_is_a_counted_sweep_error(self):
        self.write_config(prios=[1])
        env = self.sweep_env({709: ("3" * 40, [])})
        shim_env = self.env(use_py_shim=True, py_shim=PY_SHIM_ROW_STATE_FAILS)
        env["PATH"] = shim_env["PATH"]
        r = self.run_scan(env)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("errors=1 ", r.stdout)
        errors = (self.tmp / "scanout" / "latest" / "errors.txt").read_text()
        self.assertIn("FAIL row-state", errors)
        self.assertIn("boom", errors)
        self.assertEqual(self.row(709)["kind"], "new")   # the queue itself survives, without a section
        self.assertNotIn("section", self.row(709))


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class SweepLeavesTheLedgerAlone(ScanHarness, unittest.TestCase):
    """The sweep (`pr-scan.sh` with no `--mark-only`) is read-only against the ledger — the one write
    this skill causes moves to the separate `--mark-only` step, run by the main session, never the
    (forked, read-only) sweep itself."""

    def test_a_sweep_with_new_rows_does_not_touch_the_ledger(self):
        self.write_config(prios=[1])
        cfg = json.loads(self.state_dir.joinpath("config.json").read_text())
        cfg["sweep_repos"] = [REPO]
        self.state_dir.joinpath("config.json").write_text(json.dumps(cfg))
        env = self.env(STUB_SWEEP_JSON=json.dumps([
            {"number": 801, "updatedAt": "2099-01-01T00:00:00Z", "isDraft": False,
             "author": {"login": "alice", "is_bot": False}, "headRefOid": "9" * 40},
        ]), STUB_PR_801_JSON=json.dumps(pr_json(801, "alice", "9" * 40, updated_at="2099-01-01T00:00:00Z")),
            STUB_REVIEWS_801_JSON=json.dumps([]))
        ledger_path = self.state_dir / "ledger.jsonl"
        self.assertFalse(ledger_path.exists())            # nothing seeded it before the sweep runs
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(" new=1 ", r.stdout)               # the sweep did find a new, unsurfaced row …
        self.assertEqual(ledger_path.read_text(), "")    # … and still never wrote it

    def test_mark_only_is_the_only_thing_that_writes(self):
        self.write_config(prios=[1])
        cfg = json.loads(self.state_dir.joinpath("config.json").read_text())
        cfg["sweep_repos"] = [REPO]
        self.state_dir.joinpath("config.json").write_text(json.dumps(cfg))
        env = self.env(STUB_SWEEP_JSON=json.dumps([
            {"number": 802, "updatedAt": "2099-01-01T00:00:00Z", "isDraft": False,
             "author": {"login": "alice", "is_bot": False}, "headRefOid": "8" * 40},
        ]), STUB_PR_802_JSON=json.dumps(pr_json(802, "alice", "8" * 40, updated_at="2099-01-01T00:00:00Z")),
            STUB_REVIEWS_802_JSON=json.dumps([]))
        r = self.run_scan(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ledger_path = self.state_dir / "ledger.jsonl"
        self.assertEqual(ledger_path.read_text(), "")
        m = self.mark_only(env)
        self.assertEqual(m.returncode, 0, m.stdout + m.stderr)
        rows = [json.loads(ln) for ln in ledger_path.read_text().splitlines() if ln.strip()]
        self.assertEqual([r["pr"] for r in rows], [802])
        self.assertEqual(rows[0]["status"], "surfaced")


if __name__ == "__main__":
    unittest.main()
