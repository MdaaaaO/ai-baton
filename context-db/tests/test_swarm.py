"""swarm.py — an epic's task ledger. The ledger's state machine is held against the table in `docs/swarm.md`
§ What moves a row, row by row; everything else drives the real CLI on a temp content root, and `resume` reads a real
git remote and a fake `gh`. Run: make -C .claude/context-db test T=test_swarm."""
from __future__ import annotations
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "bin"))
from tests import hermetic_env  # noqa: E402
from tests.fakes.gh_stub import write_gh_stub  # noqa: E402
import swarm  # noqa: E402

SWARM = HERE.parent / "bin" / "swarm.py"
CONTRACT = HERE.parents[1] / "docs" / "swarm.md"
EPIC = "epic-1"
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
LINE_TAIL = "v=1 id={id} from={sender}"


# ── docs/swarm.md § What moves a row, read from the page ─────────────────────────────────────
TABLE_ACTS = {
    "A first `ASSIGN`, a reassignment to a new worker, or one after `parked` or a `FAIL` on the merged commit": "assign",
    "`ASSIGN … rebrief=1`": "rebrief",
    "`CLAIMED`": "CLAIMED",
    "`PR`, or the orchestrator opening the draft PR from the body file": "PR",
    "`READY`": "READY",
    "The orchestrator: the gates hold at the `READY` head": "gates",
    "`BLOCKED`": "BLOCKED",
    "`PARKED`": "PARKED",
    "`MERGED`": "MERGED",
    "`swarm.py pass`, the last `DW-<n>`": "pass",
    "The orchestrator: a `FAIL` on the merged commit": "fail",
    "The orchestrator: it closes the ticket": "close",
}
# The page does not spell "any before `merged`" out. `parked` is not in it: "Only a new `ASSIGN`, not a re-brief, restarts
# it", so a late BLOCKED leaves a parked row parked. `blocked` is: the receiver of a BLOCKED "re-briefs, reassigns, parks".
BEFORE_MERGED = {"ready", "assigned", "claimed", "pr", "ready-to-merge", "blocked"}


def contract_section() -> str:
    text = CONTRACT.read_text(encoding="utf-8")
    m = re.search(r"^### What moves a row\n(.*?)(?=^#{2,3} )", text, re.M | re.S)
    if not m:
        raise AssertionError("docs/swarm.md has no section `### What moves a row`")
    return m.group(1)


def parse_from(cell: str) -> tuple[set, str | None]:
    when = None
    if cell.endswith(", at a new head"):
        cell, when = cell[: -len(", at a new head")], "new_head"
    if cell in ("any", "any other"):  # "any other" is what the earlier rows did not take: the walk is first-match
        return set(swarm.STATUSES), when
    if cell == "any before `merged`":
        return set(BEFORE_MERGED), when
    names = re.findall(r"`([^`]+)`", cell)
    if not names or cell != ", ".join(f"`{n}`" for n in names):
        raise AssertionError(f"docs/swarm.md § What moves a row: unread From cell: {cell!r}")
    return set(names), when


def parse_to(cell: str) -> tuple[str | None, str | None, str | None]:
    """(to, when, otherwise); `to` None is "no move"."""
    if cell in ("no move", "no move; records the head"):
        return None, None, None
    m = re.fullmatch(r"`([^`]+)` if a PR is open, else `([^`]+)`", cell)
    if m:
        return m.group(1), "pr_open", m.group(2)
    m = re.fullmatch(r"`([^`]+)`", cell)
    if not m:
        raise AssertionError(f"docs/swarm.md § What moves a row: unread To cell: {cell!r}")
    return m.group(1), None, None


def contract_moves() -> list[tuple]:
    """The page's table as (act, from-set, to, when, otherwise), in the page's order."""
    out = []
    for line in contract_section().splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] == "Line or act":
            continue
        if cells[0] not in TABLE_ACTS:
            raise AssertionError(f"docs/swarm.md § What moves a row: a row this test does not know: {cells[0]!r}")
        frm, from_when = parse_from(cells[1])
        to, to_when, otherwise = parse_to(cells[2])
        out.append((TABLE_ACTS[cells[0]], frm, to, from_when or to_when, otherwise))
    return out


def contract_next(moves: list[tuple], act: str, status: str, new_head: bool, pr_open: bool) -> str | None:
    """What the page says, walked first-match: `status` for "no move", None when no row takes it."""
    flags = {"new_head": new_head, "pr_open": pr_open}
    for a, frm, to, when, otherwise in moves:
        if a != act or status not in frm:
            continue
        if when and not flags[when]:
            if otherwise is None:
                continue
            return otherwise
        return status if to is None else to
    return None


def memory_ledger(status: str, *, ready_head: str | None = None, pr_is_open: bool = False, worker: str = "lane-7") -> dict:
    row = swarm.new_row("7")
    row["status"] = status
    row["ready_head"] = ready_head
    row["worker"]["name"] = worker
    if pr_is_open:
        row.update(pr=12, pr_state="open")
    return {"key": EPIC, "tickets": {"7": row}, "seen": {}, "log": []}


def status_line(word: str, *, sha: str = SHA_A, line_id: str = "m01", sender: str = "lane-7", ticket: str = "7") -> str:
    fields = {"CLAIMED": "feat/7-x", "PR": f"feat/7-x {sha} body=7-pr.md", "READY": sha, "BLOCKED": "input orchestrator"}[word]
    return f"{word} {ticket} {fields} " + LINE_TAIL.format(id=line_id, sender=sender)


class StateMachineIsTheContractTable(unittest.TestCase):
    """DW-8 and the orchestrator's ask: the table in the page and `apply` agree line for line."""

    def setUp(self):
        self.moves = contract_moves()

    def test_moves_are_the_page_table_row_for_row(self):
        code = [(m.act, set(m.frm), m.to, m.when, m.otherwise) for m in swarm.MOVES]
        self.assertEqual(len(code), len(self.moves), "a row was added to or dropped from one side only")
        for ours, theirs in zip(code, self.moves):
            self.assertEqual(ours, theirs)

    def test_every_act_at_every_status_agrees_with_the_page(self):
        for act in TABLE_ACTS.values():
            for status in swarm.STATUSES:
                for new_head in (False, True):
                    for is_open in (False, True):
                        with self.subTest(act=act, status=status, new_head=new_head, pr_open=is_open):
                            self.assertEqual(swarm.next_status(act, status, new_head=new_head, pr_open=is_open),
                                             contract_next(self.moves, act, status, new_head, is_open))

    def test_apply_moves_every_status_as_the_page_says(self):
        for word in swarm.STATUS_LINES:
            for status in swarm.STATUSES:
                for new_head in (False, True):
                    for is_open in (False, True):
                        with self.subTest(word=word, status=status, new_head=new_head, pr_open=is_open):
                            led = memory_ledger(status, ready_head=SHA_B if new_head else SHA_A, pr_is_open=is_open)
                            swarm.apply_line(led, status_line(word))
                            want = contract_next(self.moves, word, status, word == "READY" and new_head, is_open)
                            self.assertEqual(led["tickets"]["7"]["status"], status if want is None else want)

    def test_ready_never_sets_ready_to_merge(self):
        """DW-8: `READY` records the head; only the orchestrator's gates move a row to `ready-to-merge`."""
        for status in swarm.STATUSES:
            if status == "ready-to-merge":
                continue
            with self.subTest(status=status):
                led = memory_ledger(status, pr_is_open=True)
                swarm.apply_line(led, status_line("READY"))
                row = led["tickets"]["7"]
                self.assertNotEqual(row["status"], "ready-to-merge")
                self.assertEqual((row["head"], row["ready_head"]), (SHA_A, SHA_A))

    def test_the_page_still_names_what_the_table_does_not(self):
        section = contract_section()
        self.assertIn("`DONE` among them, moves no row", CONTRACT.read_text(encoding="utf-8"))
        self.assertIn("A `MERGED` the owner's merge or an auto-merge caused can reach a row at `pr`", section)
        self.assertIn("`swarm.py apply` handles the four status lines", section)
        self.assertEqual(sorted(swarm.STATUS_LINES), ["BLOCKED", "CLAIMED", "PR", "READY"])

    def test_the_two_status_lists_agree(self):
        self.assertEqual(len(set(swarm.STATUSES)), 10)
        self.assertEqual(set(swarm.BEFORE_MERGED), BEFORE_MERGED)


class CliCase(unittest.TestCase):
    """A temp content root, and the real script run against it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.env = hermetic_env(self.tmp)
        self.root = Path(self.env["CONTEXT_ROOT"])
        self.root.mkdir()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SWARM), *args], capture_output=True, text=True, env=self.env,
                              stdin=subprocess.DEVNULL)

    def ok(self, *args: str) -> str:
        p = self.run_cli(*args)
        self.assertEqual(p.returncode, 0, f"{args}: {p.stdout}{p.stderr}")
        return p.stdout

    def refused(self, *args: str, code: int = 3) -> str:
        p = self.run_cli(*args)
        self.assertEqual(p.returncode, code, f"{args}: {p.stdout}{p.stderr}")
        self.assertNotIn("Traceback", p.stderr)
        return p.stderr

    def ledger_file(self) -> Path:
        return self.root / "state" / "swarm" / f"{EPIC}.json"

    def ledger(self) -> dict:
        return json.loads(self.ledger_file().read_text(encoding="utf-8"))

    def row(self, ticket: str = "7") -> dict:
        return self.ledger()["tickets"][ticket]

    def plan(self, *tickets: str, approve: bool = True, **init) -> None:
        extra = [x for k, v in init.items() for x in (f"--{k.replace('_', '-')}", str(v))]
        self.ok("init", EPIC, "--repo", "owner/repo", "--session", "orch-1", *extra)
        for t in tickets:
            self.ok("add", EPIC, t)
        if approve:
            self.ok("approve", EPIC, "--decision", "go on the plan")

    def assign(self, ticket: str = "7", name: str = "lane-7", branch: str = "feat/7-x", *extra: str) -> None:
        self.ok("assign", EPIC, ticket, "--kind", "subagent", "--name", name, "--branch", branch, *extra)

    def apply(self, word: str, **kw) -> str:
        return self.ok("apply", EPIC, status_line(word, **kw))

    def to_pr(self, ticket: str = "7", sha: str = SHA_A) -> None:
        """Assigned, claimed, READY at `sha`, the draft PR opened."""
        self.assign(ticket, f"lane-{ticket}", f"feat/{ticket}-x")
        self.ok("apply", EPIC, status_line("CLAIMED", ticket=ticket, sender=f"lane-{ticket}", line_id=f"c0{ticket}"))
        self.ok("apply", EPIC, status_line("READY", ticket=ticket, sender=f"lane-{ticket}", line_id=f"r0{ticket}", sha=sha))
        self.ok("status", EPIC, ticket, "pr", "--pr", f"10{ticket}")

    def to_merged(self, ticket: str = "7", sha: str = SHA_A) -> None:
        self.to_pr(ticket, sha)
        self.ok("status", EPIC, ticket, "ready-to-merge", "--head", sha)
        self.ok("status", EPIC, ticket, "merged", "--sha", SHA_C)

    def to_done(self, ticket: str) -> None:
        self.to_merged(ticket)
        self.ok("add", EPIC, ticket, "--update", "--dw", "DW-1")
        self.ok("pass", EPIC, ticket, "DW-1", "--evidence", "seen on the merged commit", "--by", "verifier")
        self.ok("status", EPIC, ticket, "done")

    def tick(self, *args: str) -> dict:
        return json.loads(self.ok("tick", EPIC, "--json", *args))

    def tick_after(self, minutes: float, ticket: str = "7") -> dict:
        """The tick `minutes` past the row's last progress."""
        when = swarm.parse_time(self.row(ticket)["last_progress"]) + timedelta(minutes=minutes)
        return self.tick("--now", when.isoformat())

    def store_config(self, **swarm_keys) -> None:
        env_dir = self.root / "reference" / "env"
        env_dir.mkdir(parents=True)
        (env_dir / "config.json").write_text(json.dumps({"environment": "ci", "swarm": swarm_keys}), encoding="utf-8")


class InitAndAdd(CliCase):
    """DW-1: `init`, `add`."""

    def test_init_writes_the_ledger_under_state_swarm(self):
        out = self.ok("init", EPIC, "--repo", "owner/repo", "--waves", "w1,w2", "--epic-usd", "50", "--worker-usd", "8",
                      "--max-workers", "2", "--session", "orch-1", "--title", "An epic", "--doc", "core/epic.md")
        self.assertEqual(Path(out.strip()), self.ledger_file())
        led = self.ledger()
        self.assertEqual(led["schema"], swarm.SCHEMA)
        self.assertEqual((led["key"], led["repo"], led["waves"]), (EPIC, "owner/repo", ["w1", "w2"]))
        self.assertEqual(led["budget"], {"epic_usd": 50.0, "worker_usd": 8.0})
        self.assertEqual((led["max_workers"], led["session"], led["doc"]), (2, "orch-1", "core/epic.md"))
        self.assertFalse(led["plan_approved"])
        for field in ("grants", "watches", "decisions", "holds", "log"):
            self.assertEqual(led[field], [], field)

    def test_init_refuses_to_overwrite(self):
        self.plan("7")
        before = self.ledger_file().read_bytes()
        self.refused("init", EPIC, "--repo", "owner/repo")
        self.assertEqual(self.ledger_file().read_bytes(), before)

    def test_a_bad_key_cannot_leave_the_ledger_dir(self):
        self.refused("init", "../escape", "--repo", "owner/repo", code=2)
        self.assertFalse((self.root / "state" / "escape.json").exists())

    def test_add_records_every_field_of_a_row(self):
        self.plan(approve=False)
        self.ok("add", EPIC, "#7", "--title", "A ticket", "--area", "a.py", "--area", "b.py", "--dep", "#3", "--dep", "4",
                "--sizing", "medium", "--wave", "w1", "--dw", "DW-1", "--cap-usd", "5")
        r = self.row()
        self.assertEqual((r["ticket"], r["title"], r["status"]), ("7", "A ticket", "ready"))
        self.assertEqual((r["area"], r["deps"], r["sizing"], r["wave"]), (["a.py", "b.py"], ["3", "4"], "medium", "w1"))
        self.assertEqual((r["dws"], r["budget_cap"], r["spent"], r["stalls"]), (["DW-1"], 5.0, 0, 0))
        self.assertEqual(r["worker"], {"kind": None, "name": None, "ref": None, "worktree": None, "branch": None})
        self.assertEqual(self.ledger()["waves"], ["w1"], "an unknown wave joins the list")

    def test_add_refuses_a_duplicate_and_a_self_dependency(self):
        self.plan("7")
        self.refused("add", EPIC, "7")
        self.refused("add", EPIC, "8", "--dep", "8", code=2)

    def test_add_reads_the_dw_ids_from_a_ticket(self):
        self.plan(approve=False)
        ticket = self.tmp / "ticket.md"
        ticket.write_text("## Done when\n\n- [ ] DW-1 one · check: `true`\n- [x] DW-2 two · check: test\n", encoding="utf-8")
        self.ok("add", EPIC, "7", "--ticket-file", str(ticket))
        self.assertEqual(self.row()["dws"], ["DW-1", "DW-2"])

    def test_a_ticket_with_no_dw_ids_is_a_usage_error_and_adds_no_row(self):
        self.plan(approve=False)
        ticket = self.tmp / "ticket.md"
        ticket.write_text("## Goal\n\nSomething.\n", encoding="utf-8")
        self.refused("add", EPIC, "7", "--ticket-file", str(ticket), code=2)
        self.assertEqual(self.ledger()["tickets"], {})

    def test_a_new_or_replanned_row_resets_the_owners_go(self):
        self.plan("7")
        self.assertTrue(self.ledger()["plan_approved"])
        self.ok("add", EPIC, "7", "--update", "--title", "A new title", "--dw", "DW-1")
        self.assertTrue(self.ledger()["plan_approved"], "a title or a delta's DW id is not a re-plan")
        self.ok("add", EPIC, "7", "--update", "--dep", "3")
        self.assertFalse(self.ledger()["plan_approved"])
        self.ok("approve", EPIC, "--decision", "go again")
        self.ok("add", EPIC, "8")
        self.assertFalse(self.ledger()["plan_approved"])

    def test_an_incident_lane_or_bookkeeping_leaves_the_go_in_place(self):
        self.plan("7")
        self.ok("add", EPIC, "8", "--incident")
        self.ok("add", EPIC, "3", "--already-done")
        self.assertTrue(self.ledger()["plan_approved"])
        self.assertEqual(self.row("3")["status"], "done")

    def test_the_owners_go_needs_its_decision_line(self):
        self.plan("7", approve=False)
        self.refused("approve", EPIC, code=2)
        self.refused("approve", EPIC, "--decision", "  ")
        self.ok("approve", EPIC, "--decision", "a line")
        self.assertEqual(self.ledger()["plan_decision"], "a line")


class Assign(CliCase):
    """DW-1: `assign`, with what the tick's assignment step refuses."""

    def test_assign_fills_the_worker_and_starts_the_clock(self):
        self.plan("7")
        self.ok("assign", EPIC, "7", "--kind", "peer", "--name", "lane-7", "--ref", "agent-9", "--worktree", "/tmp/wt",
                "--branch", "feat/7-x", "--cap-usd", "3")
        r = self.row()
        self.assertEqual(r["status"], "assigned")
        self.assertEqual(r["worker"], {"kind": "peer", "name": "lane-7", "ref": "agent-9", "worktree": "/tmp/wt", "branch": "feat/7-x"})
        self.assertEqual(r["budget_cap"], 3.0)
        self.assertTrue(r["last_progress"])

    def test_refused_before_the_plan_is_approved(self):
        self.plan("7", approve=False)
        self.assertIn("plan not approved", self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-7"))
        self.assertEqual(self.row()["status"], "ready")

    def test_an_incident_lane_starts_past_the_plan_and_its_dependencies(self):
        self.plan(approve=False)
        self.ok("add", EPIC, "7", "--incident", "--dep", "3")
        self.assign()
        self.assertEqual(self.row()["status"], "assigned")

    def test_refused_with_a_dependency_not_done(self):
        self.plan("3", "7")
        self.ok("add", EPIC, "7", "--update", "--dep", "3")
        self.ok("approve", EPIC, "--decision", "go")
        self.assertIn("waiting on 3", self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-7"))

    def test_refused_at_the_worker_cap_and_a_reassignment_replaces_its_own_slot(self):
        self.plan("7", "8", max_workers=1)
        self.assign()
        self.assertIn("no free worker slot", self.refused("assign", EPIC, "8", "--kind", "subagent", "--name", "lane-8"))
        self.ok("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-new")
        self.assertEqual(self.row()["worker"]["name"], "lane-new")

    def test_an_incident_lane_widens_no_cap(self):
        self.plan("7", max_workers=1)
        self.assign()
        self.ok("add", EPIC, "8", "--incident")
        self.refused("assign", EPIC, "8", "--kind", "subagent", "--name", "lane-8")

    def test_refused_at_the_epic_budget_and_at_the_workers_cap(self):
        self.plan("7", "8", epic_usd=10)
        self.ok("add", EPIC, "8", "--update", "--cap-usd", "2")
        self.ok("approve", EPIC, "--decision", "go")
        self.ok("progress", EPIC, "8", "--spent", "2")
        self.assertIn("worker budget", self.refused("assign", EPIC, "8", "--kind", "subagent", "--name", "lane-8"))
        self.ok("budget", EPIC, "--spent", "10", "--decision", "the reader's total")
        self.assertIn("epic budget", self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-7"))

    def test_refused_under_a_stop_and_a_hold(self):
        self.plan("7", "8")
        stop = self.root / "state" / "swarm" / "STOP-7"
        stop.write_text("{}", encoding="utf-8")
        self.assertIn("STOP on 7", self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-7"))
        stop.unlink()
        self.ok("hold", EPIC, "7", "--releaser", "lane-x", "--reason", "a shared resource")
        self.assertIn("HOLD on 7", self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-7"))
        (self.root / "state" / "swarm" / f"STOP-{EPIC}").write_text("{}", encoding="utf-8")
        self.assertIn(f"STOP on {EPIC}", self.refused("assign", EPIC, "8", "--kind", "subagent", "--name", "lane-8"))

    def test_a_row_past_the_table_is_refused(self):
        self.plan("7")
        self.to_pr()
        self.assertIn("no assignment from pr", self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-8"))

    def test_a_rebrief_moves_no_row_and_restarts_the_stall_window(self):
        self.plan("7")
        self.assign()
        self.apply("CLAIMED")
        self.ok("assign", EPIC, "7", "--rebrief")
        r = self.row()
        self.assertEqual((r["status"], r["rebriefs"]), ("claimed", 1))
        self.assertTrue(r["stall_counted_at"])

    def test_a_parked_row_restarts_on_a_new_assign_only(self):
        self.plan("7")
        self.assign()
        self.ok("status", EPIC, "7", "parked", "--reason", "a later wave")
        self.ok("assign", EPIC, "7", "--rebrief")
        self.assertEqual(self.row()["status"], "parked")
        self.assign()
        r = self.row()
        self.assertEqual((r["status"], r["parked_reason"]), ("assigned", None))

    def test_a_fix_after_a_fail_on_the_merged_commit_is_a_new_branch(self):
        self.plan("7")
        self.to_merged()
        self.ok("status", EPIC, "7", "blocked", "--reason", "DW-1 fails on the merged commit")
        self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-new")
        self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-new", "--branch", "feat/7-x")
        self.assign("7", "lane-new", "fix/7-y")
        r = self.row()
        self.assertEqual((r["status"], r["worker"]["branch"]), ("assigned", "fix/7-y"))
        self.assertEqual((r["pr"], r["head"], r["ready_head"], r["merged_sha"], r["passes"], r["blocked"]),
                         (None, None, None, None, {}, None))


class StatusMoves(CliCase):
    """DW-1 and DW-8: the moves the orchestrator writes."""

    def test_the_path_from_ready_to_done(self):
        self.plan("7")
        self.ok("add", EPIC, "7", "--update", "--dw", "DW-1", "--dw", "DW-2")
        self.assign()
        self.apply("CLAIMED", line_id="m01")
        self.apply("READY", line_id="m02")
        self.assertEqual(self.row()["status"], "claimed", "READY with no PR yet moves no row")
        self.ok("status", EPIC, "7", "pr", "--pr", "12")
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)
        self.ok("status", EPIC, "7", "merged", "--sha", SHA_C)
        self.assertEqual((self.row()["status"], self.row()["merged_sha"], self.row()["pr_state"]), ("merged", SHA_C, "merged"))
        self.ok("pass", EPIC, "7", "DW-1", "--evidence", "one", "--by", "verifier")
        self.assertEqual(self.row()["status"], "merged", "not the last DW")
        self.ok("pass", EPIC, "7", "DW-2", "--evidence", "two", "--by", "verifier")
        self.assertEqual(self.row()["status"], "verified", "a pass sets verified at the last DW")
        self.ok("status", EPIC, "7", "done")
        self.assertEqual(self.row()["status"], "done", "the orchestrator writes verified to done at the close")

    def test_moves_the_table_does_not_have_are_refused(self):
        self.plan("7")
        for status in ("pr", "ready-to-merge", "merged", "done"):
            extra = {"pr": ("--pr", "1"), "ready-to-merge": ("--head", SHA_A), "merged": ("--sha", SHA_C)}.get(status, ())
            with self.subTest(status=status):
                self.refused("status", EPIC, "7", status, *extra)
        self.assertEqual(self.row()["status"], "ready")

    def test_the_moves_another_writer_owns_are_refused(self):
        self.plan("7")
        for status, owner in (("assigned", "assign"), ("claimed", "apply"), ("verified", "pass"), ("ready", "back to ready")):
            with self.subTest(status=status):
                self.assertIn(owner, self.refused("status", EPIC, "7", status))

    def test_ready_to_merge_is_pinned_to_the_ready_head(self):
        self.plan("7")
        self.to_pr()
        self.refused("status", EPIC, "7", "ready-to-merge", code=2)
        self.assertIn("a moved head needs a new READY", self.refused("status", EPIC, "7", "ready-to-merge", "--head", SHA_B))
        self.ok("progress", EPIC, "7", "--head", SHA_B)
        self.assertIn("a moved head needs a new READY", self.refused("status", EPIC, "7", "ready-to-merge", "--head", SHA_A))
        self.apply("READY", sha=SHA_B, line_id="m09")
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_B[:7])
        self.assertEqual(self.row()["status"], "ready-to-merge")

    def test_a_new_ready_head_takes_the_row_back_to_pr(self):
        self.plan("7")
        self.to_pr()
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)
        self.apply("READY", sha=SHA_A, line_id="m08")
        self.assertEqual(self.row()["status"], "ready-to-merge", "the same head moves nothing")
        self.apply("READY", sha=SHA_B, line_id="m09")
        self.assertEqual(self.row()["status"], "pr")

    def test_merged_from_pr_only_on_an_owner_or_auto_merge(self):
        self.plan("7")
        self.to_pr()
        self.refused("status", EPIC, "7", "merged", "--sha", SHA_C)
        self.refused("status", EPIC, "7", "merged", code=2)
        self.ok("status", EPIC, "7", "merged", "--sha", SHA_C, "--merged-by", "auto")
        self.assertEqual((self.row()["status"], self.row()["merged_by"]), ("merged", "auto"))

    def test_merged_to_blocked_is_a_fail_on_the_merged_commit(self):
        """DW-8: the edge exists, needs its reason, and exists from `merged` only."""
        self.plan("7")
        self.to_pr()
        self.refused("status", EPIC, "7", "blocked", "--reason", "a fail")
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)
        self.ok("status", EPIC, "7", "merged", "--sha", SHA_C)
        self.refused("status", EPIC, "7", "blocked", code=2)
        self.ok("status", EPIC, "7", "blocked", "--reason", "DW-1 fails")
        r = self.row()
        self.assertEqual((r["status"], r["blocked"]["reason"], r["blocked"]["ran"]), ("blocked", "verifier-fail", "DW-1 fails"))

    def test_a_repeat_moves_nothing_and_keeps_what_the_move_recorded(self):
        self.plan("7")
        self.assign()
        self.apply("BLOCKED")
        before = self.row()["blocked"]
        self.assertIn("no move", self.ok("status", EPIC, "7", "blocked", "--reason", "another"))
        self.assertEqual(self.row()["blocked"], before)

    def test_parked_needs_a_reason(self):
        self.plan("7")
        self.refused("status", EPIC, "7", "parked", code=2)
        self.ok("status", EPIC, "7", "parked", "--reason", "a later wave")
        self.assertEqual((self.row()["status"], self.row()["parked_reason"]), ("parked", "a later wave"))


class Progress(CliCase):
    """DW-1: `progress`."""

    def setUp(self):
        super().setUp()
        self.plan("7")
        self.assign()

    def test_a_moved_head_or_a_note_restarts_the_clock_and_clears_the_stalls(self):
        self.ok("progress", EPIC, "7", "--stall")
        self.assertEqual(self.row()["stalls"], 1)
        self.ok("progress", EPIC, "7", "--head", SHA_A)
        r = self.row()
        self.assertEqual((r["head"], r["stalls"], r["stall_counted_at"]), (SHA_A, 0, None))
        self.ok("progress", EPIC, "7", "--stall")
        self.ok("progress", EPIC, "7", "--note", "a status comment")
        self.assertEqual(self.row()["stalls"], 0)

    def test_spend_alone_and_an_unmoved_head_are_not_progress(self):
        self.ok("progress", EPIC, "7", "--head", SHA_A)
        self.ok("progress", EPIC, "7", "--stall")
        self.ok("progress", EPIC, "7", "--spent", "1.5")
        self.ok("progress", EPIC, "7", "--head", SHA_A[:7])
        r = self.row()
        self.assertEqual((r["spent"], r["stalls"]), (1.5, 1))

    def test_a_bad_sha_is_a_usage_error(self):
        self.refused("progress", EPIC, "7", "--head", "not-a-sha", code=2)


class Pass(CliCase):
    """DW-2 and DW-8: only the verifier, only with evidence, only on a merged row."""

    def setUp(self):
        super().setUp()
        self.plan("7")
        self.ok("add", EPIC, "7", "--update", "--dw", "DW-1", "--dw", "DW-2")

    def test_exits_non_zero_without_evidence_or_without_by_verifier(self):
        self.to_merged()
        before = self.ledger_file().read_bytes()
        self.refused("pass", EPIC, "7", "DW-1", "--by", "verifier", code=2)
        self.refused("pass", EPIC, "7", "DW-1", "--evidence", "seen", code=2)
        self.refused("pass", EPIC, "7", "DW-1", "--evidence", "seen", "--by", "orchestrator")
        self.refused("pass", EPIC, "7", "DW-1", "--evidence", "  ", "--by", "verifier")
        self.assertEqual(self.ledger_file().read_bytes(), before)

    def test_help_names_both_as_required(self):
        out = self.ok("pass", "--help")
        usage = out[:out.index("positional")]
        self.assertRegex(usage, r"--evidence EVIDENCE")
        self.assertRegex(usage, r"--by BY")
        self.assertNotIn("[--evidence", usage)
        self.assertNotIn("[--by", usage)

    def test_refused_before_the_merge_and_for_an_id_not_on_the_row(self):
        self.to_pr()
        self.assertIn("follows the merge", self.refused("pass", EPIC, "7", "DW-1", "--evidence", "e", "--by", "verifier"))
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)
        self.ok("status", EPIC, "7", "merged", "--sha", SHA_C)
        self.refused("pass", EPIC, "7", "DW-9", "--evidence", "e", "--by", "verifier")
        self.refused("pass", EPIC, "7", "not-an-id", "--evidence", "e", "--by", "verifier", code=2)

    def test_records_the_evidence_and_a_repeat_changes_nothing(self):
        self.to_merged()
        self.ok("pass", EPIC, "7", "DW-1", "--evidence", "seen", "--by", "verifier")
        rec = self.row()["passes"]["DW-1"]
        self.assertEqual((rec["evidence"], rec["by"]), ("seen", "verifier"))
        before = self.ledger_file().read_bytes()
        self.assertIn("no change", self.ok("pass", EPIC, "7", "DW-1", "--evidence", "seen", "--by", "verifier"))
        self.assertEqual(self.ledger_file().read_bytes(), before)


class Tick(CliCase):
    """DW-3 and DW-8: the tick's input."""

    def test_a_ticket_is_ready_only_when_every_dependency_is_done(self):
        self.plan("3", "7")
        self.ok("add", EPIC, "7", "--update", "--dep", "3")
        self.ok("approve", EPIC, "--decision", "go")
        self.ok("add", EPIC, "3", "--update", "--dw", "DW-1")
        for step in ("ready", "merged", "verified"):
            with self.subTest(dep_at=step):
                if step == "merged":
                    self.to_merged("3")
                if step == "verified":
                    self.ok("pass", EPIC, "3", "DW-1", "--evidence", "seen", "--by", "verifier")
                self.assertEqual(self.row("3")["status"], step)
                t = self.tick()
                self.assertNotIn("7", t["ready"])
                self.assertEqual(t["waiting"], [{"ticket": "7", "on": ["3"]}])
        self.ok("status", EPIC, "3", "done")
        t = self.tick()
        self.assertEqual((t["ready"], t["waiting"]), (["7"], []))
        self.assertEqual(t["assignable"], ["7"])

    def test_a_dependency_with_no_row_is_not_done(self):
        self.plan("7")
        self.ok("add", EPIC, "7", "--update", "--dep", "3")
        self.assertEqual(self.tick()["waiting"], [{"ticket": "7", "on": ["3"]}])
        self.ok("add", EPIC, "3", "--already-done")
        self.assertEqual(self.tick()["ready"], ["7"])

    def test_flags_a_worker_past_the_default_stall_minutes(self):
        self.plan("7")
        self.assign()
        self.assertEqual(self.tick_after(89)["stalled"], [])
        stalled = self.tick_after(91)["stalled"]
        self.assertEqual([(s["ticket"], s["worker"], s["minutes"], s["stalls"]) for s in stalled], [("7", "lane-7", 91, 0)])

    def test_stall_minutes_comes_from_the_store(self):
        self.store_config(stall_minutes=30)
        self.plan("7")
        self.assign()
        self.assertEqual(self.tick_after(29)["stalled"], [])
        self.assertEqual(len(self.tick_after(31)["stalled"]), 1)

    def test_the_stall_flag_covers_assigned_and_claimed_only(self):
        """DW-8: `pr`, `ready-to-merge`, `blocked` and `parked` are exempt, and so is a row under a HOLD."""
        self.plan("7")
        self.assign()
        self.assertEqual(len(self.tick_after(500)["stalled"]), 1, "assigned")
        self.apply("CLAIMED", line_id="m01")
        self.assertEqual(len(self.tick_after(500)["stalled"]), 1, "claimed")
        self.apply("BLOCKED", line_id="m02")
        self.assertEqual(self.tick_after(500)["stalled"], [], "blocked")
        self.ok("status", EPIC, "7", "parked", "--reason", "later")
        self.assertEqual(self.tick_after(500)["stalled"], [], "parked")
        self.assign()
        self.apply("CLAIMED", line_id="m03")
        self.apply("PR", line_id="m04")
        self.assertEqual(self.row()["status"], "pr")
        self.assertEqual(self.tick_after(500)["stalled"], [], "pr")
        self.ok("status", EPIC, "7", "pr", "--pr", "12")
        self.apply("READY", line_id="m05")
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)
        self.assertEqual(self.tick_after(500)["stalled"], [], "ready-to-merge")

    def test_a_held_row_and_a_row_with_a_ready_in_force_do_not_stall(self):
        self.plan("7", "8")
        self.assign()
        self.assign("8", "lane-8", "feat/8-x")
        self.ok("apply", EPIC, status_line("READY", ticket="8", sender="lane-8"))
        self.ok("hold", EPIC, "7", "--releaser", "lane-x", "--reason", "a resource")
        self.assertEqual(self.tick_after(500)["stalled"], [])

    def test_the_stall_ladder_counts_rebriefs_then_reassigns(self):
        self.plan("7")
        self.assign()
        self.assertEqual(self.tick_after(100)["stalled"][0]["next"], "count")
        self.ok("progress", EPIC, "7", "--stall")
        when = swarm.parse_time(self.row()["stall_counted_at"]) + timedelta(minutes=80)
        led = self.ledger()
        led["tickets"]["7"]["last_progress"] = "2020-01-01T00:00:00Z"
        self.ledger_file().write_text(json.dumps(led), encoding="utf-8")
        self.assertEqual(self.tick("--now", when.isoformat())["stalled"], [], "the count restarts the window")
        when = swarm.parse_time(self.row()["stall_counted_at"]) + timedelta(minutes=100)
        self.assertEqual(self.tick("--now", when.isoformat())["stalled"][0]["next"], "rebrief")
        self.ok("assign", EPIC, "7", "--rebrief")
        when = swarm.parse_time(self.row()["stall_counted_at"]) + timedelta(minutes=100)
        self.assertEqual(self.tick("--now", when.isoformat())["stalled"][0]["next"], "reassign-or-park")

    def test_flags_a_worker_past_its_cap_and_an_epic_past_its_cap(self):
        self.plan("7", epic_usd=10, worker_usd=2)
        self.assign()
        self.assertEqual(self.tick()["budget"]["state"], "ok")
        self.ok("progress", EPIC, "7", "--spent", "2")
        b = self.tick()["budget"]
        self.assertEqual(b["over"], [{"ticket": "7", "spent": 2.0, "cap": 2.0}])
        self.ok("budget", EPIC, "--spent", "10", "--decision", "the reader's total")
        t = self.tick()
        self.assertEqual(t["budget"]["state"], "over")
        self.assertTrue(any("epic budget" in r for r in t["no_assign"]))

    def test_budget_defaults_to_no_cap_and_reads_the_store(self):
        self.plan("7")
        self.assertEqual(self.tick()["budget"]["state"], "no-cap")
        self.store_config(budget={"epic_usd": 5, "worker_usd": 1})
        self.ok("progress", EPIC, "7", "--spent", "1")
        self.assign_refused_for_budget()
        self.assertEqual(self.tick()["budget"]["epic_usd"], 5)

    def assign_refused_for_budget(self) -> None:
        self.assertIn("worker budget", self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-7"))

    def test_assignable_stops_at_the_worker_cap(self):
        self.plan("1", "2", "3", "4", "5")
        self.assertEqual((self.tick()["max_workers"], self.tick()["assignable"]), (4, ["1", "2", "3", "4"]))
        self.ledger_file().unlink()
        self.plan("5", "6", "7", max_workers=2)
        self.assertEqual(self.tick()["assignable"], ["5", "6"])
        self.assign("5", "lane-5", "feat/5-x")
        self.assertEqual(self.tick()["assignable"], ["6"])

    def test_max_workers_comes_from_the_store(self):
        self.store_config(max_workers=1)
        self.plan("5", "6")
        self.assertEqual(self.tick()["assignable"], ["5"])

    def test_assignable_is_the_current_wave_and_an_unfinished_wave_holds_the_next(self):
        self.ok("init", EPIC, "--repo", "owner/repo", "--waves", "w1,w2")
        self.ok("add", EPIC, "5", "--wave", "w1")
        self.ok("add", EPIC, "6", "--wave", "w2")
        self.ok("approve", EPIC, "--decision", "go")
        t = self.tick()
        self.assertEqual((t["wave"], t["ready"], t["assignable"]), ("w1", ["5", "6"], ["5"]))
        self.ok("add", EPIC, "5", "--update", "--dw", "DW-1")
        self.to_merged("5")
        self.assertEqual(self.tick()["assignable"], [], "a merged row still holds its wave")

    def test_a_stuck_row_does_not_hold_the_rest(self):
        """The work behind a stop waits, and everything else goes on."""
        self.ok("init", EPIC, "--repo", "owner/repo", "--waves", "w1,w2")
        self.ok("add", EPIC, "5", "--wave", "w1")
        self.ok("add", EPIC, "4", "--wave", "w1", "--dep", "5")
        self.ok("add", EPIC, "6", "--wave", "w2")
        self.ok("approve", EPIC, "--decision", "go")
        self.ok("status", EPIC, "5", "parked", "--reason", "the owner's call")
        t = self.tick()
        self.assertEqual((t["wave"], t["assignable"]), ("w2", ["6"]))

    def test_nothing_is_assignable_before_the_go_under_a_stop_or_past_the_budget(self):
        self.plan("7", approve=False)
        t = self.tick()
        self.assertEqual((t["ready"], t["assignable"]), (["7"], []))
        self.assertIn("plan not approved", t["no_assign"])
        self.ok("approve", EPIC, "--decision", "go")
        self.assertEqual(self.tick()["assignable"], ["7"])
        stop = self.root / "state" / "swarm" / f"STOP-{EPIC}"
        (stop.parent / f"{stop.name}.lock").write_text("", encoding="utf-8")
        self.assertEqual(self.tick()["assignable"], ["7"], "another writer's lock file is not a STOP")
        stop.write_text("{}", encoding="utf-8")
        t = self.tick()
        self.assertEqual((t["assignable"], t["stops"]), ([], [EPIC]))
        stop.unlink()
        self.ok("budget", EPIC, "--epic-usd", "1", "--spent", "1", "--decision", "a line")
        self.assertEqual(self.tick()["assignable"], [])

    def test_an_incident_lane_goes_first_and_past_the_plan_but_not_past_a_cap(self):
        self.plan("7", approve=False)
        self.ok("add", EPIC, "8", "--incident")
        self.assertEqual(self.tick()["assignable"], ["8"])
        self.ok("approve", EPIC, "--decision", "go")
        self.assertEqual(self.tick()["assignable"], ["8", "7"])
        self.ok("budget", EPIC, "--epic-usd", "1", "--spent", "1", "--decision", "a line")
        self.assertEqual(self.tick()["assignable"], [])

    def test_lists_ready_with_no_pr_and_a_head_past_its_ready(self):
        self.plan("7", "8")
        self.assign()
        self.apply("CLAIMED", line_id="m01")
        self.apply("READY", line_id="m02")
        self.assertEqual([(i["ticket"], i["head"]) for i in self.tick()["integrate"]], [("7", SHA_A)])
        self.ok("status", EPIC, "7", "pr", "--pr", "12")
        self.assertEqual(self.tick()["integrate"], [])
        self.ok("progress", EPIC, "7", "--head", SHA_B)
        self.assertEqual(self.tick()["needs_ready"], [{"ticket": "7", "head": SHA_B, "ready_head": SHA_A}])

    def test_a_ready_on_a_row_reassigned_with_its_pr_open_is_the_orchestrators_next_act(self):
        self.plan("7")
        self.to_pr()
        self.apply("BLOCKED", sender="lane-7", line_id="m01")
        self.assign("7", "lane-new", "feat/7-x")
        self.apply("CLAIMED", sender="lane-new", line_id="m02")
        self.apply("READY", sender="lane-new", line_id="m03")
        self.assertEqual([(i["ticket"], i["pr"]) for i in self.tick()["integrate"]], [("7", 107)])

    def test_a_pr_line_with_no_number_yet_is_the_orchestrators_next_act(self):
        self.plan("7")
        self.assign()
        self.apply("CLAIMED", line_id="m01")
        self.apply("PR", line_id="m02")
        self.assertEqual([(i["ticket"], i["body"]) for i in self.tick()["integrate"]], [("7", "7-pr.md")])

    def test_watches_decisions_grants_and_holds_reach_the_tick(self):
        self.plan("7")
        self.ok("watch", EPIC, "--due", "2020-01-01T00:00:00Z", "--owner", "orchestrator", "--what", "a soak")
        self.ok("watch", EPIC, "--due", "2999-01-01T00:00:00Z", "--owner", "orchestrator", "--what", "later")
        self.ok("decision", EPIC, "--question", "which one?", "--for-ticket", "7")
        self.ok("grant", EPIC, "--scope", "a-resource", "--by", "owner", "--expires", "2020-01-01T00:00:00Z", "--decision", "a line")
        self.ok("hold", EPIC, "a-resource", "--releaser", "lane-x", "--reason", "serial")
        t = self.tick()
        self.assertEqual([w["what"] for w in t["watches_due"]], ["a soak"])
        self.assertEqual([d["question"] for d in t["decisions_open"]], ["which one?"])
        self.assertEqual([g["scope"] for g in t["grants_expired"]], ["a-resource"])
        self.assertEqual([h["scope"] for h in t["holds"]], ["a-resource"])
        self.ok("watch", EPIC, "--close", "w1")
        self.ok("decision", EPIC, "--close", "d1", "--line", "the chosen one")
        t = self.tick()
        self.assertEqual((t["watches_due"], t["decisions_open"]), ([], []))

    def test_tick_with_nothing_to_sweep_leaves_the_ledger_bytes_unchanged(self):
        self.plan("7")
        self.assign()
        before = self.ledger_file().read_bytes()
        self.tick_after(500)
        self.ok("tick", EPIC)
        self.assertEqual(self.ledger_file().read_bytes(), before)

    def test_text_output_is_one_lowercase_word_a_line(self):
        self.plan("7", approve=False)
        out = self.ok("tick", EPIC)
        self.assertRegex(out, r"(?m)^ready 7$")
        self.assertRegex(out, r"(?m)^no-assign plan not approved$")


class Render(CliCase):
    """DW-5: `sessions/<name>/prs.tsv` from the ledger, and the section printed for the flush."""

    def prs_file(self, name: str = "orch-1") -> Path:
        return self.root / "sessions" / name / "prs.tsv"

    def test_writes_prs_tsv_with_the_open_prs(self):
        self.plan("7", "8", "9")
        self.to_pr("7", SHA_A)
        self.ok("add", EPIC, "9", "--update", "--repo", "owner/other")
        self.to_merged("8", SHA_B)
        self.to_pr("9", SHA_B)
        self.ok("render", EPIC)
        self.assertEqual(self.prs_file().read_text(encoding="utf-8").splitlines(), [
            "repo\tpr\thead7\tstate\tticket",
            f"owner/repo\t107\t{SHA_A[:7]}\tpr\t7",
            f"owner/other\t109\t{SHA_B[:7]}\tpr\t9",
        ])

    def test_the_state_column_follows_the_row_and_the_merge_drops_it(self):
        self.plan("7")
        self.to_pr()
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)
        self.ok("render", EPIC)
        self.assertIn(f"owner/repo\t107\t{SHA_A[:7]}\tready-to-merge\t7", self.prs_file().read_text(encoding="utf-8"))
        self.ok("status", EPIC, "7", "merged", "--sha", SHA_C)
        self.ok("render", EPIC)
        self.assertEqual(self.prs_file().read_text(encoding="utf-8"), "repo\tpr\thead7\tstate\tticket\n")

    def test_a_named_session_is_kept_on_the_ledger(self):
        self.plan("7")
        self.ok("render", EPIC, "--session", "orch-2")
        self.assertTrue(self.prs_file("orch-2").is_file())
        self.assertEqual(self.ledger()["session"], "orch-2")

    def test_no_session_name_is_a_usage_error(self):
        self.ok("init", EPIC, "--repo", "owner/repo")
        self.assertIn("--session", self.refused("render", EPIC, code=2))

    def test_prints_the_section_and_touches_no_context_doc(self):
        self.plan("7")
        self.ok("add", EPIC, "3", "--already-done")
        self.ok("add", EPIC, "7", "--update", "--title", "a | title", "--dep", "3", "--dw", "DW-1")
        self.ok("approve", EPIC, "--decision", "go again")
        self.to_pr()
        self.apply("BLOCKED", line_id="m09")
        out = self.ok("render", EPIC)
        self.assertTrue(out.startswith("## Swarm\n"))
        self.assertIn("Rendered by swarm.py", out)
        row = next(line for line in out.splitlines() if line.startswith("| #7 "))
        self.assertIn("blocked (input, orchestrator)", row)
        self.assertIn(f"| {SHA_A[:7]} |", row)
        self.assertIn("| #107 |", row)
        self.assertIn("| 0/1 |", row)
        self.assertIn("| #3 |", row)
        written = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file() and "state" not in p.parts)
        self.assertEqual(written, [os.path.join("sessions", "orch-1", "prs.tsv")])

    def test_a_cell_cannot_break_the_table(self):
        self.assertEqual(swarm.cell("a|b\nc"), "a\\|b c")
        self.assertEqual(swarm.cell(None), "-")


class Apply(CliCase):
    """DW-7 and DW-8."""

    def setUp(self):
        super().setUp()
        self.plan("7")
        self.assign()

    def test_updates_a_row_from_a_status_line(self):
        out = self.apply("CLAIMED")
        self.assertIn("assigned -> claimed", out)
        r = self.row()
        self.assertEqual((r["status"], r["worker"]["branch"]), ("claimed", "feat/7-x"))
        self.apply("PR", line_id="m02")
        r = self.row()
        self.assertEqual((r["status"], r["head"], r["body"]), ("pr", SHA_A, "7-pr.md"))

    def test_a_repeated_line_is_a_no_op(self):
        self.apply("CLAIMED")
        before = self.ledger_file().read_bytes()
        self.assertIn("no change", self.apply("CLAIMED"))
        self.assertEqual(self.ledger_file().read_bytes(), before, "not even the updated time")

    def test_a_repeated_id_never_moves_the_row_again(self):
        """A re-send keeps its id: a second copy after the row moved on must not move it back."""
        self.apply("CLAIMED", line_id="m01")
        self.apply("BLOCKED", line_id="m02")
        self.assertEqual(self.row()["status"], "blocked")
        self.apply("BLOCKED", line_id="m02")
        self.apply("READY", line_id="m03")
        self.assertEqual(self.row()["status"], "claimed")
        self.apply("BLOCKED", line_id="m02")
        self.assertEqual(self.row()["status"], "claimed")

    def test_a_used_id_on_another_line_is_flagged_and_moves_nothing(self):
        self.apply("CLAIMED", line_id="m01")
        self.assertIn("read differently", self.apply("BLOCKED", line_id="m01"))
        self.assertEqual(self.row()["status"], "claimed")

    def test_ready_records_the_head_and_moves_no_row(self):
        self.apply("CLAIMED", line_id="m01")
        out = self.apply("READY", line_id="m02")
        self.assertIn("no move", out)
        r = self.row()
        self.assertEqual((r["status"], r["head"], r["ready_head"]), ("claimed", SHA_A, SHA_A))

    def test_a_blocked_newer_than_the_ready_leaves_the_merge_gate_until_a_newer_ready(self):
        """DW-8, latest wins: the gate reads the READY in force."""
        self.apply("CLAIMED", line_id="m01")
        self.apply("READY", line_id="m02")
        self.ok("status", EPIC, "7", "pr", "--pr", "12")
        self.apply("BLOCKED", line_id="m03")
        r = self.row()
        self.assertEqual((r["status"], r["ready_head"], r["blocked"]["reason"]), ("blocked", None, "input"))
        self.refused("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)
        self.apply("READY", line_id="m04")
        r = self.row()
        self.assertEqual((r["status"], r["ready_head"], r["blocked"]), ("pr", SHA_A, None))
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)

    def test_ready_after_blocked_with_no_pr_goes_back_to_claimed(self):
        self.apply("BLOCKED", line_id="m01")
        self.apply("READY", line_id="m02")
        self.assertEqual(self.row()["status"], "claimed")

    def test_blocked_records_its_fields(self):
        self.ok("apply", EPIC, f'BLOCKED 7 stop orchestrator sha={SHA_B} ran="git push; a comment" v=1 id=m01 from=lane-7')
        b = self.row()["blocked"]
        self.assertEqual((b["reason"], b["who"], b["sha"], b["ran"]), ("stop", "orchestrator", SHA_B, "git push; a comment"))
        self.assertEqual(self.row()["head"], SHA_B)

    def test_latest_wins_per_sender_scope_and_line_kind(self):
        self.apply("READY", sha=SHA_A, line_id="m01")
        self.apply("CLAIMED", line_id="m02")
        self.apply("READY", sha=SHA_B, line_id="m03")
        lines = self.row()["lines"]["lane-7"]
        self.assertEqual(lines["READY"]["sha"], SHA_B)
        self.assertEqual(lines["READY"]["id"], "m03")
        self.assertEqual(lines["CLAIMED"]["id"], "m02", "kinds do not replace each other")
        self.assertEqual(self.row()["ready_head"], SHA_B)

    def test_a_line_from_another_sender_is_recorded_and_moves_nothing(self):
        out = self.apply("BLOCKED", sender="lane-other", line_id="m01")
        self.assertIn("no move", out)
        r = self.row()
        self.assertEqual((r["status"], r["blocked"]), ("assigned", None))
        self.assertEqual(r["lines"]["lane-other"]["BLOCKED"]["id"], "m01")
        self.apply("READY", sender="lane-other", sha=SHA_B, line_id="m02")
        self.assertEqual(self.row()["head"], None, "another sender's READY sets no head")
        self.apply("CLAIMED", sender="lane-7", line_id="m03")
        self.assertEqual(self.row()["status"], "claimed")
        self.assertEqual(sorted(self.row()["lines"]), ["lane-7", "lane-other"], "senders do not replace each other")

    def test_a_line_with_no_edge_is_recorded_and_moves_nothing(self):
        self.ok("status", EPIC, "7", "parked", "--reason", "later")
        self.assertIn("no move from parked", self.apply("BLOCKED", line_id="m01"))
        r = self.row()
        self.assertEqual((r["status"], r["blocked"]), ("parked", None))
        self.assertIn("BLOCKED", r["lines"]["lane-7"])

    def test_a_status_line_restarts_the_progress_clock(self):
        self.ok("progress", EPIC, "7", "--stall")
        self.apply("CLAIMED")
        self.assertEqual((self.row()["stalls"], self.row()["stall_counted_at"]), (0, None))

    def test_done_and_every_other_word_are_refused(self):
        """DW-8: `apply` takes CLAIMED, PR, READY and BLOCKED, not DONE, because DONE moves no row."""
        before = self.ledger_file().read_bytes()
        for word in ("DONE", "MERGED", "PARKED", "ASSIGN", "HOLD", "ACK"):
            with self.subTest(word=word):
                err = self.refused("apply", EPIC, f"{word} 7 v=1 id=x01 from=lane-7", code=2)
                self.assertIn("CLAIMED, PR, READY, BLOCKED", err)
        self.assertEqual(self.ledger_file().read_bytes(), before)

    def test_a_malformed_line_changes_nothing(self):
        before = self.ledger_file().read_bytes()
        for line in (
            "",
            "CLAIMED",
            "CLAIMED 7 feat/7-x v=1 id=x01",  # no from=
            "CLAIMED 7 feat/7-x v=1 from=lane-7",  # no id=
            "CLAIMED 7 feat/7-x id=x01 from=lane-7",  # no v=
            "CLAIMED 7 v=1 id=x01 from=lane-7",  # no branch
            "CLAIMED 7 feat/7-x extra v=1 id=x01 from=lane-7",  # one field too many
            "CLAIMED 7 v=1 feat/7-x id=x01 from=lane-7",  # a field after a key=value pair
            f"PR 7 feat/7-x {SHA_A} v=1 id=x01 from=lane-7",  # no body=
            "READY 7 NOTASHA v=1 id=x01 from=lane-7",
            "BLOCKED 7 bored orchestrator v=1 id=x01 from=lane-7",  # a reason outside the list
            'BLOCKED 7 input orchestrator ran="unterminated v=1 id=x01 from=lane-7',
        ):
            with self.subTest(line=line):
                self.refused("apply", EPIC, line, code=2)
        self.assertEqual(self.ledger_file().read_bytes(), before)

    def test_a_grammar_version_outside_the_list_is_refused_naming_it(self):
        err = self.refused("apply", EPIC, f"READY 7 {SHA_A} v=9 id=x01 from=lane-7", code=2)
        self.assertIn("reads 1", err)

    def test_an_unknown_key_on_a_line_is_refused_by_the_shared_parser(self):
        err = self.refused("apply", EPIC, 'CLAIMED 7 feat/7-x v=1 id=x01 from=lane-7 note="a note" future=1', code=2)
        self.assertIn("future", err)

    def test_an_unknown_ticket_is_absent_and_records_no_id(self):
        self.refused("apply", EPIC, status_line("CLAIMED", ticket="99"), code=1)
        self.assertEqual(self.ledger()["seen"], {})


class Records(CliCase):
    """Grants, watches, decisions, holds and budget changes."""

    def setUp(self):
        super().setUp()
        self.plan("7")

    def test_a_grant_needs_its_decision(self):
        args = ("grant", EPIC, "--scope", "a-resource", "--by", "owner", "--expires", "2999-01-01T00:00:00-05:00")
        self.refused(*args, code=2)
        self.refused(*args, "--decision", " ")
        self.ok(*args, "--decision", "a line")
        g = self.ledger()["grants"][0]
        self.assertEqual((g["scope"], g["granted_by"], g["expires"], g["decision"]),
                         ("a-resource", "owner", "2999-01-01T05:00:00Z", "a line"))

    def test_a_bad_time_is_a_usage_error(self):
        self.refused("grant", EPIC, "--scope", "s", "--by", "owner", "--expires", "soon", "--decision", "a line", code=2)
        self.refused("watch", EPIC, "--due", "soon", "--owner", "o", "--what", "w", code=2)

    def test_a_decision_closes_with_its_line(self):
        self.ok("decision", EPIC, "--question", "which one?")
        self.refused("decision", EPIC, "--close", "d1")
        self.refused("decision", EPIC, "--close", "d9", "--line", "x", code=1)
        self.ok("decision", EPIC, "--close", "d1", "--line", "the chosen one")
        d = self.ledger()["decisions"][0]
        self.assertEqual(d["line"], "the chosen one")
        self.assertTrue(d["closed"])

    def test_only_the_releaser_or_the_owner_ends_a_hold(self):
        self.ok("hold", EPIC, "7", "--releaser", "lane-x", "--reason", "a resource")
        self.refused("hold", EPIC, "7", "--releaser", "lane-y", "--reason", "again")
        self.refused("hold", EPIC, "7", "--release", "--by", "lane-other")
        self.refused("hold", EPIC, "7", "--release")
        self.ok("hold", EPIC, "7", "--release", "--by", "lane-x")
        self.ok("hold", EPIC, "7", "--releaser", "lane-x", "--reason", "again")
        self.ok("hold", EPIC, "7", "--release", "--by", "owner")
        self.assertEqual(self.ledger()["holds"][1]["released_by"], "owner")
        self.refused("hold", EPIC, "7", "--release", "--by", "owner", code=1)

    def test_a_budget_change_needs_its_decision(self):
        self.refused("budget", EPIC, "--epic-usd", "5")
        self.refused("budget", EPIC, "--epic-usd", "5", "--decision", "  ")
        self.refused("budget", EPIC, "--decision", "a line", code=2)
        self.ok("budget", EPIC, "--epic-usd", "5", "--decision", "a line")
        self.assertEqual(self.ledger()["budget"]["epic_usd"], 5.0)

    def test_show_prints_the_ledger_or_a_row(self):
        self.assertEqual(json.loads(self.ok("show", EPIC))["key"], EPIC)
        self.assertEqual(json.loads(self.ok("show", EPIC, "7"))["ticket"], "7")


class ExitCodes(CliCase):
    """The engine's contract: 0 ok, 1 absent, 2 usage or I/O, 3 refused; one line, never a traceback."""

    def test_no_ledger_and_no_ticket_are_absent(self):
        self.refused("tick", EPIC, code=1)
        self.refused("apply", EPIC, status_line("CLAIMED"), code=1)
        self.plan("7")
        self.refused("status", EPIC, "99", "done", code=1)
        self.refused("show", EPIC, "99", code=1)
        self.refused("watch", EPIC, "--close", "w9", code=1)

    def test_a_corrupt_or_foreign_ledger_is_an_io_error(self):
        self.plan("7")
        for text in ("{not json", json.dumps({"schema": 99}), "[]"):
            with self.subTest(text=text):
                self.ledger_file().write_text(text, encoding="utf-8")
                self.assertNotIn("Traceback", self.refused("tick", EPIC, code=2))
                self.refused("progress", EPIC, "7", code=2)

    def test_a_corrupt_store_config_is_one_line_and_exit_2(self):
        self.plan("7")
        env_dir = self.root / "reference" / "env"
        env_dir.mkdir(parents=True)
        (env_dir / "config.json").write_text("{not json", encoding="utf-8")
        self.assertNotIn("Traceback", self.refused("tick", EPIC, code=2))

    def test_a_bad_config_value_falls_back_to_the_default_and_says_so(self):
        self.plan("7")
        self.store_config(stall_minutes="soon")
        p = self.run_cli("tick", EPIC, "--json")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["stall_minutes"], 90)
        self.assertIn("stall_minutes", p.stderr)

    def test_every_subcommand_answers_help(self):
        for sub in ("init", "add", "approve", "assign", "status", "progress", "pass", "apply", "tick", "render", "resume",
                    "grant", "watch", "decision", "hold", "budget", "show"):
            with self.subTest(sub=sub):
                self.ok(sub, "--help")

    def test_top_level_help_names_the_schema(self):
        out = self.ok("--help")
        for word in ("plan_approved", "tickets", "passes", "ready_head", "swarm.stall_minutes", "prs.tsv"):
            self.assertIn(word, out)


class ReadyInForce(CliCase):
    """Every assign ends the READY in force, a re-brief included: the head it named is about to move."""

    def test_a_new_worker_on_the_same_branch_inherits_no_ready(self):
        self.plan("7")
        self.assign()
        self.apply("CLAIMED", line_id="m01")
        self.apply("READY", line_id="m02")
        self.assign("7", "lane-new", "feat/7-x")
        self.assertIsNone(self.row()["ready_head"])
        t = self.tick_after(500)
        self.assertEqual(t["workers"], 1)
        self.assertEqual(t["integrate"], [])
        self.assertEqual([s["ticket"] for s in t["stalled"]], ["7"])

    def test_a_rebrief_shuts_the_gate_until_a_new_ready(self):
        self.plan("7")
        self.to_pr()
        self.ok("assign", EPIC, "7", "--rebrief")
        self.assertIsNone(self.row()["ready_head"])
        self.refused("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)
        self.apply("READY", line_id="m09", sha=SHA_B)
        self.ok("progress", EPIC, "7", "--head", SHA_B)
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_B)

    def test_a_rebrief_at_ready_to_merge_needs_a_new_ready_and_says_so(self):
        self.plan("7")
        self.to_pr()
        self.ok("status", EPIC, "7", "ready-to-merge", "--head", SHA_A)
        self.ok("assign", EPIC, "7", "--rebrief")
        self.assertEqual(self.tick()["needs_ready"], [{"ticket": "7", "head": SHA_A, "ready_head": None}])
        self.assertIn("needs-ready 7 ", self.ok("tick", EPIC))
        self.apply("READY", line_id="m09")
        self.assertEqual(self.row()["status"], "pr", "a READY after a re-brief goes through the gates again")


class FailOnTheMergedCommit(CliCase):
    """The guard is the row's recorded FAIL, whatever the status just before the assign."""

    def setUp(self):
        super().setUp()
        self.plan("7")
        self.ok("add", EPIC, "7", "--update", "--dw", "DW-1", "--dw", "DW-2")
        self.to_merged()
        self.ok("pass", EPIC, "7", "DW-1", "--evidence", "seen on the merged commit", "--by", "verifier")
        self.ok("status", EPIC, "7", "blocked", "--reason", "DW-2 fails on the merged commit")

    def assert_only_a_new_branch_gets_it(self):
        self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-new")
        self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", "lane-new", "--branch", "feat/7-x")
        self.assign("7", "lane-new", "fix/7-y")
        r = self.row()
        self.assertEqual((r["status"], r["worker"]["branch"], r["fail"]), ("assigned", "fix/7-y", None))
        self.assertEqual((r["pr"], r["pr_state"], r["head"], r["merged_sha"], r["passes"]), (None, None, None, None, {}))

    def test_it_holds_through_a_park(self):
        self.ok("status", EPIC, "7", "parked", "--reason", "an owner call")
        self.assert_only_a_new_branch_gets_it()

    def test_it_holds_through_a_late_ready(self):
        self.apply("READY", line_id="m09", sha=SHA_B)
        self.assertEqual(self.row()["status"], "claimed")
        self.assert_only_a_new_branch_gets_it()

    def test_a_late_ready_opens_no_gate(self):
        self.apply("READY", line_id="m09", sha=SHA_B)
        self.refused("status", EPIC, "7", "pr", "--pr", "999")
        self.assertEqual(self.tick()["integrate"], [])
        self.assertEqual([f["ticket"] for f in self.tick()["failed"]], ["7"])
        self.assertIn("failed 7 ", self.ok("tick", EPIC))

    def test_old_passes_never_reach_verified(self):
        self.apply("READY", line_id="m09", sha=SHA_B)
        self.apply("PR", line_id="m10", sha=SHA_B)
        self.ok("status", EPIC, "7", "merged", "--sha", SHA_C, "--merged-by", "owner")
        self.refused("pass", EPIC, "7", "DW-2", "--evidence", "seen on the old branch", "--by", "verifier")
        self.assertEqual(self.row()["status"], "merged")

    def test_the_old_worker_is_not_re_briefed(self):
        self.refused("assign", EPIC, "7", "--rebrief")
        self.assertEqual(self.row()["rebriefs"], 0)


class CapRaise(CliCase):
    """Raising a cap is the owner's: docs/swarm.md § Authority matrix. A first cap or a lower one decides nothing."""

    def assign_cap(self, *args: str, name: str = "lane-7", ticket: str = "7") -> list[str]:
        return ["assign", EPIC, ticket, "--kind", "subagent", "--name", name, "--branch", f"feat/{ticket}-x", *args]

    def test_a_raise_needs_the_decision_line_and_writes_it(self):
        self.plan("7")
        self.assign("7", "lane-7", "feat/7-x", "--cap-usd", "5")
        before = self.ledger_file().read_bytes()
        self.refused(*self.assign_cap("--cap-usd", "10"))
        self.refused(*self.assign_cap("--cap-usd", "10", "--decision", "  "))
        self.assertEqual(self.ledger_file().read_bytes(), before)
        self.ok(*self.assign_cap("--cap-usd", "10", "--decision", "the owner raised it"))
        r = self.row()
        self.assertEqual((r["budget_cap"], r["cap_decision"]), (10.0, "the owner raised it"))
        entry = [e for e in self.ledger()["log"] if e["act"] == "cap"]
        self.assertEqual([(e["from"], e["to"], e["note"]) for e in entry], [(5.0, 10.0, "the owner raised it")])

    def test_a_first_cap_and_a_lower_cap_need_no_decision(self):
        self.plan("7")
        self.assign("7", "lane-7", "feat/7-x", "--cap-usd", "5")
        self.assign("7", "lane-7", "feat/7-x", "--cap-usd", "3")
        r = self.row()
        self.assertEqual((r["budget_cap"], r["cap_decision"]), (3.0, None))
        self.assertEqual([e for e in self.ledger()["log"] if e["act"] == "cap"], [])

    def test_a_first_cap_above_the_epics_worker_cap_is_a_raise(self):
        self.plan("7", worker_usd=8)
        self.refused(*self.assign_cap("--cap-usd", "20"))
        self.ok(*self.assign_cap("--cap-usd", "20", "--decision", "the owner raised it"))
        self.assertEqual(self.row()["budget_cap"], 20.0)

    def test_the_decision_route_works_at_the_cap(self):
        self.plan("7")
        self.assign("7", "lane-7", "feat/7-x", "--cap-usd", "5")
        self.ok("progress", EPIC, "7", "--spent", "5")
        self.refused(*self.assign_cap())
        self.refused(*self.assign_cap("--cap-usd", "10"))
        self.refused(*self.assign_cap("--cap-usd", "4", "--decision", "a lower cap frees nothing"))
        self.ok(*self.assign_cap("--cap-usd", "10", "--decision", "the owner raised it"))
        self.assertEqual((self.row()["status"], self.row()["budget_cap"]), ("assigned", 10.0))

    def test_a_rebrief_takes_the_same_route(self):
        self.plan("7")
        self.to_pr()
        self.ok("assign", EPIC, "7", "--rebrief", "--cap-usd", "5")
        self.refused("assign", EPIC, "7", "--rebrief", "--cap-usd", "10")
        self.ok("assign", EPIC, "7", "--rebrief", "--cap-usd", "10", "--decision", "the owner raised it")
        self.assertEqual((self.row()["status"], self.row()["budget_cap"]), ("pr", 10.0))

    def test_an_incident_widens_no_cap(self):
        self.plan("7")
        self.ok("add", EPIC, "7", "--update", "--cap-usd", "5")
        self.ok("approve", EPIC, "--decision", "go on the plan")
        self.ok("add", EPIC, "7", "--update", "--incident", "--cap-usd", "9")
        self.assertFalse(self.ledger()["plan_approved"], "a raise through `add` still goes back to the owner")


class LanesUnderStopOrHold(CliCase):
    """`assign` checks the lane named on the line, and the row's old worker too when it differs."""

    def setUp(self):
        super().setUp()
        self.plan("7")

    def stop(self, scope: str) -> None:
        (self.root / "state" / "swarm" / f"STOP-{scope}").write_text("{}", encoding="utf-8")

    def hold(self, scope: str) -> None:
        self.ok("hold", EPIC, scope, "--releaser", "lane-x", "--reason", "a shared resource")

    def refused_to(self, name: str) -> str:
        return self.refused("assign", EPIC, "7", "--kind", "subagent", "--name", name, "--branch", "feat/7-x")

    def test_a_stop_on_the_lane_on_the_line(self):
        self.stop("lane-new")
        self.assertIn("STOP on lane-new", self.refused_to("lane-new"))
        self.assertEqual(self.row()["status"], "ready")

    def test_a_hold_on_the_lane_on_the_line(self):
        self.hold("lane-new")
        self.assertIn("HOLD on lane-new", self.refused_to("lane-new"))
        self.assertEqual(self.row()["status"], "ready")

    def test_a_stop_on_the_old_worker(self):
        self.assign()
        self.stop("lane-7")
        self.assertIn("STOP on lane-7", self.refused_to("lane-new"))

    def test_a_hold_on_the_old_worker(self):
        self.assign()
        self.hold("lane-7")
        self.assertIn("HOLD on lane-7", self.refused_to("lane-new"))

    def test_a_stop_on_another_lane_stops_nothing(self):
        self.stop("lane-other")
        self.assign()
        self.assertEqual(self.row()["worker"]["name"], "lane-7")


class OpenRebriefs(CliCase):
    """A worker re-briefed at any status runs again, so it counts toward `swarm.max_workers` until it answers."""

    def setUp(self):
        super().setUp()
        self.plan("7", "8", max_workers=1)
        self.to_pr()

    def test_a_row_re_briefed_at_pr_takes_a_slot(self):
        self.assertEqual(self.tick()["workers"], 0)
        self.ok("assign", EPIC, "7", "--rebrief")
        t = self.tick()
        self.assertEqual((t["workers"], t["assignable"]), (1, []))
        self.assertEqual([r["ticket"] for r in t["rebriefs_open"]], ["7"])
        self.assertIn("rebrief-open 7 ", self.ok("tick", EPIC))
        self.assertIn("no free worker slot", self.refused("assign", EPIC, "8", "--kind", "subagent", "--name", "lane-8"))

    def test_a_ready_or_a_blocked_answers_it(self):
        for n, word in enumerate(("READY", "BLOCKED")):
            with self.subTest(word=word):
                self.ok("assign", EPIC, "7", "--rebrief")
                self.apply(word, line_id=f"a0{n}", sha=SHA_B)
                self.assertIsNone(self.row()["rebrief_open"])
                self.assertEqual(self.tick()["workers"], 0)

    def test_a_line_from_another_sender_answers_nothing(self):
        self.ok("assign", EPIC, "7", "--rebrief")
        self.apply("READY", line_id="a01", sha=SHA_B, sender="lane-other")
        self.assertEqual(self.tick()["workers"], 1)

    def test_a_park_closes_it_and_a_parked_row_opens_none(self):
        self.ok("assign", EPIC, "7", "--rebrief")
        self.ok("status", EPIC, "7", "parked", "--reason", "an owner call")
        self.assertEqual(self.tick()["workers"], 0)
        self.ok("assign", EPIC, "7", "--rebrief")
        self.assertIsNone(self.row()["rebrief_open"], "a parked row has no worker to restart")
        self.assertEqual(self.tick()["workers"], 0)

    def test_a_merge_closes_it(self):
        self.ok("assign", EPIC, "7", "--rebrief")
        self.ok("status", EPIC, "7", "merged", "--sha", SHA_C, "--merged-by", "owner")
        self.assertEqual(self.tick()["workers"], 0)

    def test_the_stall_flag_stays_assigned_and_claimed_only(self):
        self.ok("assign", EPIC, "7", "--rebrief")
        self.assertEqual(self.tick_after(500)["stalled"], [])


class PrStateNeverMovesBack(CliCase):
    """`status pr` leaves a merged or closed `pr_state` alone. A new PR opens its own."""

    def set_pr_state(self, state: str) -> None:
        led = self.ledger()
        led["tickets"]["7"]["pr_state"] = state
        self.ledger_file().write_text(json.dumps(led), encoding="utf-8")

    def test_no_number_on_a_merged_pr(self):
        self.plan("7")
        self.to_merged()
        self.ok("status", EPIC, "7", "blocked", "--reason", "DW-1 fails on the merged commit")
        self.apply("READY", line_id="m09", sha=SHA_B)
        self.refused("status", EPIC, "7", "pr")
        self.assertEqual(self.row()["pr_state"], "merged")

    def test_a_closed_pr_stays_closed_and_a_new_number_is_open(self):
        self.plan("7")
        self.to_pr()
        self.set_pr_state("closed")
        self.refused("status", EPIC, "7", "pr")
        self.refused("status", EPIC, "7", "pr", "--pr", str(self.row()["pr"]))
        self.assertEqual(self.row()["pr_state"], "closed")
        self.ok("status", EPIC, "7", "pr", "--pr", "999")
        self.assertEqual((self.row()["pr"], self.row()["pr_state"]), (999, "open"))


class NullConfigAndSpend(CliCase):

    def test_an_explicit_null_means_the_default(self):
        self.store_config(max_workers=None, stall_minutes=None)
        self.plan("7")
        t = self.tick()
        self.assertEqual((t["max_workers"], t["stall_minutes"]), (swarm.DEFAULTS["swarm.max_workers"],
                                                                 swarm.DEFAULTS["swarm.stall_minutes"]))

    def test_recording_spend_decides_nothing(self):
        self.plan("7")
        log_before = self.ledger()["log"]
        self.ok("budget", EPIC, "--spent", "3")
        self.assertEqual((self.ledger()["spent"], self.ledger()["log"]), (3.0, log_before))
        self.refused("budget", EPIC, "--spent", "4", "--worker-usd", "9")
        self.assertEqual(self.ledger()["spent"], 3.0)


class Module(unittest.TestCase):
    """The shape another script reads: `load`, `edit`, `tick_report`, unknown keys kept."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"CONTEXT_ROOT": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.assertEqual(self.main("init", EPIC, "--repo", "owner/repo"), 0)
        self.assertEqual(self.main("add", EPIC, "7"), 0)

    @staticmethod
    def main(*argv: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return swarm.main(list(argv))

    def test_edit_writes_nothing_when_the_body_raises(self):
        path = swarm.ledger_path(EPIC)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            with swarm.edit(EPIC) as led:
                led["tickets"]["7"]["status"] = "done"
                raise ValueError("a step failed")
        self.assertEqual(path.read_bytes(), before)

    def test_edit_writes_nothing_when_nothing_changed(self):
        path = swarm.ledger_path(EPIC)
        before = path.read_bytes()
        with swarm.edit(EPIC):
            pass
        self.assertEqual(path.read_bytes(), before)

    def test_unknown_keys_survive_a_write(self):
        with swarm.edit(EPIC) as led:
            led["from_another_ticket"] = {"kept": True}
            led["tickets"]["7"]["spent_detail"] = [1, 2]
        self.assertEqual(self.main("progress", EPIC, "7", "--note", "n"), 0)
        led = swarm.load(EPIC)
        self.assertEqual(led["from_another_ticket"], {"kept": True})
        self.assertEqual(led["tickets"]["7"]["spent_detail"], [1, 2])

    def test_an_absent_ledger_is_the_named_error(self):
        with self.assertRaises(swarm.Absent):
            swarm.load("no-such-epic")
        with self.assertRaises(swarm.Absent):
            with swarm.edit("no-such-epic"):
                pass

    def test_a_tick_reads_from_another_script(self):
        report = swarm.tick_report(swarm.load(EPIC))
        self.assertEqual(report["ready"], ["7"])
        self.assertEqual(swarm.budget_state(swarm.load(EPIC))["state"], "no-cap")

    def test_the_parsed_line_is_a_plain_dict(self):
        rec = swarm.parse_status_line(f'BLOCKED #7 scope lane-x sha={SHA_A} note="a note" v=1 id=m01 from=lane-7')
        self.assertEqual(rec, {"word": "BLOCKED", "ticket": "7", "reason": "scope", "who": "lane-x", "v": "1", "id": "m01",
                               "from": "lane-7", "sha": SHA_A, "note": "a note"})

    def test_a_short_and_a_long_sha_are_one_head(self):
        self.assertTrue(swarm.same_sha(SHA_A, SHA_A[:7]))
        self.assertFalse(swarm.same_sha(SHA_A, SHA_B[:7]))
        self.assertFalse(swarm.same_sha(None, SHA_A))


class Resume(CliCase):
    """DW-4: rows rebuilt from a pushed branch and a PR body, in a fixture repo."""

    def setUp(self):
        super().setUp()
        self.remote = self.tmp / "remote.git"
        self.checkout = self.tmp / "checkout"
        self.git_run("init", "--bare", "-b", "main", str(self.remote))
        self.git_run("init", "-b", "main", str(self.checkout))
        self.git("remote", "add", "origin", str(self.remote))
        self.commit("first")
        self.git("push", "-q", "origin", "main")
        self.gh_log = self.tmp / "gh.log"
        self.env["PATH"] = f"{self.tmp / 'bin'}{os.pathsep}{self.env['PATH']}"
        self.set_prs([])

    def git_run(self, *args: str, cwd: Path | None = None) -> str:
        p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd, env=hermetic_env(self.tmp),
                           stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, f"git {args}: {p.stderr}")
        return p.stdout.strip()

    def git(self, *args: str) -> str:
        return self.git_run(*args, cwd=self.checkout)

    def commit(self, name: str) -> str:
        (self.checkout / name).write_text(name, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def push_branch(self, branch: str) -> str:
        self.git("checkout", "-q", "-b", branch, "main")
        head = self.commit(branch.replace("/", "_"))
        self.git("push", "-q", "origin", branch)
        self.git("checkout", "-q", "main")
        return head

    def set_prs(self, prs: list, code: int = 0) -> None:
        reply = (code, "", "gh: a read failed") if code else json.dumps(prs)
        write_gh_stub(self.tmp / "bin", {"pr list": reply}, self.gh_log)

    @staticmethod
    def pr(number: int, branch: str, head: str, *, state: str = "OPEN", body: str = "", merge: str | None = None) -> dict:
        return {"number": number, "headRefName": branch, "headRefOid": head, "state": state, "body": body,
                "mergeCommit": {"oid": merge} if merge else None}

    def resume(self, *args: str) -> str:
        return self.ok("resume", EPIC, "--repo-dir", str(self.checkout), *args)

    def actions(self, *args: str) -> dict:
        return {r["ticket"]: r for r in json.loads(self.resume("--json", *args))}

    def test_prints_the_diff_and_writes_nothing_without_apply(self):
        self.plan("7")
        self.assign()
        head = self.push_branch("feat/7-x")
        self.set_prs([self.pr(12, "feat/7-x", head)])
        before = self.ledger_file().read_bytes()
        out = self.resume()
        self.assertEqual(self.ledger_file().read_bytes(), before)
        self.assertIn("7 status: assigned -> pr", out)
        self.assertIn(f"7 head: - -> {head}", out)
        self.assertIn("7 pr: - -> 12", out)
        self.assertIn("nothing written", out)

    def test_apply_rebuilds_the_row_from_the_pushed_branch_and_the_pr(self):
        self.plan("7")
        self.assign()
        head = self.push_branch("feat/7-x")
        self.set_prs([self.pr(12, "feat/7-x", head, body="Closes #7")])
        self.resume("--apply")
        r = self.row()
        self.assertEqual((r["status"], r["head"], r["pr"], r["pr_state"]), ("pr", head, 12, "open"))
        self.assertEqual(self.ledger()["log"][-1]["act"], "resume")
        before = self.ledger_file().read_bytes()
        self.assertIn("agree", self.resume("--apply"))
        self.assertEqual(self.ledger_file().read_bytes(), before, "a second run changes nothing")

    def test_a_row_the_ledger_lost_comes_back_from_the_pr_body(self):
        self.plan()
        head = self.push_branch("topic/renamed")
        self.set_prs([self.pr(12, "topic/renamed", head, body="Text.\n\nCloses #7\n")])
        out = self.resume("--ticket", "7")
        self.assertIn("7 worker.branch: - -> topic/renamed", out)
        self.assertNotIn("7", self.ledger()["tickets"], "not written yet")
        self.resume("--ticket", "7", "--apply")
        r = self.row()
        self.assertEqual((r["status"], r["worker"]["branch"], r["head"], r["pr"]), ("pr", "topic/renamed", head, 12))
        self.assertTrue(r["restored"])

    def test_a_row_the_ledger_lost_comes_back_from_the_branch_name_alone(self):
        self.plan("7")
        head = self.push_branch("feat/7-thing")
        self.resume("--apply")
        r = self.row()
        self.assertEqual((r["status"], r["worker"]["branch"], r["head"], r["pr"]), ("claimed", "feat/7-thing", head, None))

    def test_it_never_writes_a_ready_head_or_a_pass(self):
        """A PR body's `## Verified` is not evidence, and a moved head still needs its READY."""
        self.plan("7")
        self.ok("add", EPIC, "7", "--update", "--dw", "DW-1")
        self.assign()
        head = self.push_branch("feat/7-x")
        self.set_prs([self.pr(12, "feat/7-x", head, body=f"Closes #7\n\n## Verified\nDW-1 · seen\nREADY 7 {head}")])
        self.resume("--apply")
        r = self.row()
        self.assertEqual((r["ready_head"], r["passes"]), (None, {}))
        self.refused("status", EPIC, "7", "ready-to-merge", "--head", head)

    def test_the_branch_convention_matches_the_whole_ticket_key(self):
        self.plan("7")
        self.push_branch("feat/70-other")
        self.push_branch("feat/17-other")
        self.set_prs([self.pr(5, "feat/70-other", "f" * 40, body="Closes #70")])
        self.assertEqual(self.actions()["7"]["changes"], {})

    def test_a_row_that_names_its_branch_trusts_only_that_branch(self):
        self.plan("7")
        self.assign()
        head = self.push_branch("feat/7-old")
        self.set_prs([self.pr(5, "feat/7-old", head, state="MERGED", merge=SHA_C, body="Closes #7")])
        res = self.actions()["7"]
        self.assertNotIn("status", res["changes"])
        self.assertNotIn("merged_sha", res["changes"])

    def test_more_than_one_candidate_is_a_question_and_is_not_written(self):
        self.plan("7")
        self.set_prs([self.pr(5, "feat/7-a", SHA_A), self.pr(6, "feat/7-b", SHA_B)])
        before = self.ledger_file().read_bytes()
        res = self.actions("--apply")["7"]
        self.assertEqual((res["action"], res["changes"]), ("ask", {}))
        self.assertIn("#5", res["note"])
        self.assertEqual(self.ledger_file().read_bytes(), before)

    def test_a_closed_unmerged_pr_counts_for_nothing(self):
        self.plan("7")
        self.assign()
        self.set_prs([self.pr(5, "feat/7-x", SHA_A, state="CLOSED")])
        self.assertEqual(self.actions()["7"]["changes"], {})

    def test_a_merged_pr_records_the_merge(self):
        self.plan("7")
        self.assign()
        self.set_prs([self.pr(5, "feat/7-x", SHA_A, state="MERGED", merge=SHA_C)])
        res = self.actions("--apply")["7"]
        self.assertEqual(res["action"], "record-merge")
        r = self.row()
        self.assertEqual((r["status"], r["merged_sha"], r["pr_state"]), ("merged", SHA_C, "merged"))

    def test_a_verified_or_done_row_never_changes(self):
        self.plan("7")
        self.ok("add", EPIC, "7", "--update", "--dw", "DW-1")
        self.to_merged()
        self.ok("pass", EPIC, "7", "DW-1", "--evidence", "e", "--by", "verifier")
        head = self.push_branch("feat/7-x")
        self.set_prs([self.pr(9, "feat/7-x", head)])
        self.assertEqual(self.actions()["7"]["changes"], {})

    def test_a_blocked_or_parked_row_keeps_its_status(self):
        self.plan("7")
        self.assign()
        self.apply("BLOCKED")
        head = self.push_branch("feat/7-x")
        self.set_prs([self.pr(12, "feat/7-x", head)])
        self.resume("--apply")
        r = self.row()
        self.assertEqual((r["status"], r["pr"]), ("blocked", 12))

    def test_it_says_what_to_do_with_a_lost_worker(self):
        self.plan("5", "6", "7", "8", "9", max_workers=9)
        for ticket in ("6", "7", "8"):
            self.assign(ticket, f"lane-{ticket}", f"feat/{ticket}-x")
        head = self.push_branch("feat/6-x")
        self.set_prs([self.pr(16, "feat/6-x", head)])
        self.git("worktree", "add", "-q", "-b", "feat/7-x", str(self.tmp / "wt7"), "main")
        (self.tmp / "wt7" / "unsaved").write_text("work", encoding="utf-8")
        res = self.actions()
        self.assertEqual(res["5"]["action"], "none", "ready, and nothing on the remote")
        self.assertEqual(res["6"]["action"], "drive-to-merge")
        self.assertEqual(res["7"]["action"], "respawn-in-place")
        self.assertEqual((res["7"]["worktree"], res["7"]["dirty"]), (str(self.tmp / "wt7"), 1))
        self.assertEqual(res["8"]["action"], "reassign", "assigned, with no worktree and no PR")

    def test_a_worker_stopped_by_a_restart_is_respawned_in_place_with_the_transcript_and_findings(self):
        self.plan("7")
        self.assign("7", "lane-7", "feat/7-x")
        self.git("worktree", "add", "-q", "-b", "feat/7-x", str(self.tmp / "wt7"), "main")  # clean, branch never pushed
        res = self.actions()["7"]
        self.assertEqual((res["action"], res["dirty"], res["worktree"]), ("respawn-in-place", 0, str(self.tmp / "wt7")))
        self.assertIn("same worktree", res["note"])
        self.assertIn("never a fresh assignment", res["note"])
        self.assertIn("saved transcript", res["note"])
        self.assertIn("findings inline", res["note"])
        self.assertIn("rebrief=1", res["note"])
        self.assertNotIn("unsaved", res["note"])
        (self.tmp / "wt7" / "unsaved").write_text("work", encoding="utf-8")
        self.assertIn("1 unsaved change", self.actions()["7"]["note"])

    def test_only_an_assigned_or_claimed_row_can_have_lost_its_worker(self):
        self.plan("5", "7", max_workers=5)
        self.git("worktree", "add", "-q", "-b", "feat/5-x", str(self.tmp / "wt5"), "main")
        self.assertEqual(self.actions()["5"]["action"] if "5" in self.actions() else "none", "none",
                         "a ready row has no worker to respawn, whatever worktree sits beside it")
        head = self.push_branch("feat/7-x")
        self.set_prs([self.pr(17, "feat/7-x", head)])
        res = self.actions()["7"]
        self.assertEqual(res["action"], "update", "a ready row with an open PR is a fact to record, not a PR to drive")
        self.assertEqual(res["changes"]["status"], ["ready", "pr"])

    def test_an_assigned_or_claimed_row_with_an_open_pr_is_driven_to_merge(self):
        self.plan("6", "7", max_workers=4)
        self.assign("6", "lane-6", "feat/6-x")
        self.assign("7", "lane-7", "feat/7-x")
        self.ok("apply", EPIC, status_line("CLAIMED", ticket="7", sender="lane-7", line_id="c07"))
        self.set_prs([self.pr(16, "feat/6-x", self.push_branch("feat/6-x")), self.pr(17, "feat/7-x", self.push_branch("feat/7-x"))])
        res = self.actions()
        self.assertEqual((res["6"]["action"], res["7"]["action"]), ("drive-to-merge", "drive-to-merge"))

    def test_a_named_pr_gh_does_not_list_is_a_question(self):
        self.plan("7")
        self.to_pr()
        self.assertEqual(self.actions()["7"]["action"], "ask")

    def test_a_closed_pr_is_recorded_and_said(self):
        self.plan("7")
        self.to_pr()
        self.set_prs([self.pr(107, "feat/7-x", SHA_A, state="CLOSED")])
        res = self.actions()["7"]
        self.assertEqual((res["action"], res["note"]), ("ask", "PR #107 is closed"))
        self.assertEqual(res["changes"]["pr_state"], ["open", "closed"])
        text = self.resume()
        self.assertNotIn("gh does not list it", text)
        self.assertNotIn("the ledger and the remote agree", text)
        self.actions("--apply")
        self.assertEqual(self.row()["pr_state"], "closed")
        self.ok("status", EPIC, "7", "pr", "--pr", "999")

    def test_a_recorded_fail_is_a_reassign_whatever_gh_shows(self):
        self.plan("7")
        self.to_merged()
        self.ok("status", EPIC, "7", "blocked", "--reason", "DW-1 fails on the merged commit")
        self.set_prs([self.pr(107, "feat/7-x", SHA_A, state="MERGED", merge=SHA_C)])
        res = self.actions()["7"]
        self.assertEqual(res["action"], "reassign")
        self.assertIn("new branch", res["note"])

    def test_a_failed_read_is_exit_2_never_nothing_on_the_remote(self):
        self.plan("7")
        self.assign()
        before = self.ledger_file().read_bytes()
        self.set_prs([], code=1)
        self.assertIn("gh pr list failed", self.refused("resume", EPIC, "--repo-dir", str(self.checkout), "--apply", code=2))
        self.set_prs([])
        err = self.refused("resume", EPIC, "--repo-dir", str(self.checkout), "--remote", "no-such-remote", code=2)
        self.assertIn("git ls-remote failed", err)
        self.refused("resume", EPIC, "--repo-dir", str(self.tmp / "no-such-dir"), code=2)
        self.assertEqual(self.ledger_file().read_bytes(), before)

    def test_it_asks_gh_for_the_contracts_fields(self):
        self.plan("7")
        self.resume()
        call = self.gh_log.read_text(encoding="utf-8")
        self.assertIn("pr list", call)
        self.assertIn("-R owner/repo", call)
        for field in ("headRefName", "headRefOid", "number", "body"):
            self.assertIn(field, call)

    def test_a_row_of_another_repo_is_not_compared_against_this_one(self):
        self.plan("7")
        self.ok("add", EPIC, "7", "--update", "--repo", "owner/other")
        self.push_branch("feat/7-x")
        self.assertEqual(self.actions(), {})


if __name__ == "__main__":
    unittest.main()
