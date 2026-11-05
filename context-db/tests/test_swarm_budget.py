"""swarm_budget.py — the swarm's budget brake, driven through its real CLI on a temp content root and fixture transcripts
under a temp HOME; `swarm.py tick` and `lease.py` read what it wrote. Run: make -C .claude/context-db test T=test_swarm_budget."""
from __future__ import annotations
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
import kb  # noqa: E402
import swarm  # noqa: E402
import swarm_budget  # noqa: E402

BUDGET = HERE.parent / "bin" / "swarm_budget.py"
SWARM = HERE.parent / "bin" / "swarm.py"
LEASE = HERE.parent / "bin" / "lease.py"
KIT = HERE.parents[1]
EPIC = "epic-1"
ORCH = "orch-sid"
OUTPUT_USD_PER_TOKEN = 15 / 1_000_000  # the built-in sonnet row
FAT = 200_000  # a prefix past the default split threshold


def turn(rid: str, usd: float = 0.0, ctx: int = 0, tools: tuple[str, ...] = ()) -> dict:
    """One assistant turn: `usd` of output on the sonnet row, a prefix of `ctx` tokens, one `Agent` call per id in `tools`."""
    usage = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": ctx,
             "output_tokens": round(usd / OUTPUT_USD_PER_TOKEN)}
    content = [{"type": "tool_use", "id": t, "name": "Agent", "input": {}} for t in tools]
    return {"type": "assistant", "requestId": rid, "timestamp": "2026-01-02T10:00:00Z",
            "message": {"id": rid, "model": "claude-sonnet-test", "usage": usage, "content": content}}


def fat_turns(count: int, prefix: str = "r") -> list[dict]:
    return [turn(f"{prefix}{i}", ctx=FAT) for i in range(count)]


class BudgetCase(unittest.TestCase):
    """A temp content root and HOME, the real scripts run against them."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.env = hermetic_env(self.tmp)
        for name in ("CLAUDE_CODE_SESSION_ID", "SESSION_STATS_PRICES", "SESSION_STATS_SPLIT_WINDOW", "SESSION_STATS_SPLIT_THRESHOLD"):
            self.env.pop(name, None)
        self.root = Path(self.env["CONTEXT_ROOT"])
        self.root.mkdir()
        self.projects = self.tmp / ".claude" / "projects" / "proj"
        self.projects.mkdir(parents=True)

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, env=self.env,
                              stdin=subprocess.DEVNULL)

    def ok(self, script: Path, *args: str) -> str:
        p = self.run_script(script, *args)
        self.assertEqual(p.returncode, 0, f"{args}: {p.stdout}{p.stderr}")
        return p.stdout

    def sweep(self, *args: str) -> str:
        return self.ok(BUDGET, "sweep", EPIC, *args)

    def config(self, **budget) -> None:
        env_dir = self.root / "reference" / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "config.json").write_text(json.dumps({"environment": "ci", "swarm": {"budget": budget}}), encoding="utf-8")

    def write_session(self, session_id: str, turns: list[dict]) -> Path:
        path = self.projects / f"{session_id}.jsonl"
        path.write_text("".join(json.dumps(t) + "\n" for t in turns), encoding="utf-8")
        return path

    def write_subagent(self, session_id: str, agent_id: str, turns: list[dict], call: str | None = None) -> Path:
        directory = self.projects / session_id / "subagents"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"agent-{agent_id}.jsonl"
        path.write_text("".join(json.dumps(t) + "\n" for t in turns), encoding="utf-8")
        if call:
            (directory / f"agent-{agent_id}.meta.json").write_text(json.dumps({"toolUseId": call}), encoding="utf-8")
        return path

    def write_result(self, worker: str, text: str) -> Path:
        directory = self.root / "state" / "swarm" / EPIC / worker
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "result.json"
        path.write_text(text, encoding="utf-8")
        return path

    def plan(self, *tickets: str, **init) -> None:
        extra = [x for k, v in init.items() for x in (f"--{k.replace('_', '-')}", str(v))]
        self.ok(SWARM, "init", EPIC, "--repo", "owner/repo", "--session", "orch-1", *extra)
        for t in tickets:
            self.ok(SWARM, "add", EPIC, t)
        self.ok(SWARM, "approve", EPIC, "--decision", "go on the plan")

    def assign(self, ticket: str, ref: str | None, kind: str = "subagent", *extra: str) -> None:
        args = ["assign", EPIC, ticket, "--kind", kind, "--name", f"lane-{ticket}", "--branch", f"feat/{ticket}-x", *extra]
        self.ok(SWARM, *args, *(["--ref", ref] if ref else []))

    def ledger(self) -> dict:
        return json.loads((self.root / "state" / "swarm" / f"{EPIC}.json").read_text(encoding="utf-8"))

    def row(self, ticket: str = "7") -> dict:
        return self.ledger()["tickets"][ticket]

    def stop_file(self) -> Path:
        return self.root / "state" / "swarm" / f"STOP-{EPIC}"

    def assign_time(self, ticket: str = "7"):
        return swarm.parse_time(next(e["at"] for e in reversed(self.ledger()["log"]) if e.get("ticket") == ticket and e["act"] == "assign"))


class SpendReading(BudgetCase):
    """DW-1: per-worker and per-epic `spent` from fixture transcripts, and `tick` flags a cap breach."""

    def full_epic(self) -> None:
        self.write_session(ORCH, [turn("o1", usd=1.0)])
        self.write_subagent(ORCH, "w7", [turn("w7-1", usd=2.0, tools=("toolu_f1",))])
        self.write_subagent(ORCH, "f1", [turn("f1-1", usd=0.5)], call="toolu_f1")
        self.write_session("peer-sid", [turn("p1", usd=4.0)])
        self.write_subagent("peer-sid", "ps", [turn("ps-1", usd=1.0)])
        headless = self.write_session("head-sid", [turn("h1", usd=2.5)])
        self.plan("7", "8", "9")
        self.assign("7", "w7")
        self.assign("8", "peer-sid", "peer")
        self.assign("9", str(headless), "headless")
        self.write_result("lane-9", json.dumps({"type": "result", "total_cost_usd": 3.0}))

    def test_writes_each_worker_and_the_epic_with_no_double_count(self):
        self.full_epic()
        self.sweep("--session-id", ORCH)
        self.assertEqual(self.row("7")["spent"], 2.5, "a subagent worker: its own transcript and the fork it made")
        self.assertEqual(self.row("8")["spent"], 5.0, "a peer: the session's TOTAL")
        self.assertEqual(self.row("9")["spent"], 3.0, "a headless worker: the higher of the two readings")
        self.assertEqual(self.ledger()["spent"], 11.5, "the orchestrator's TOTAL holds its subagent worker and its fork once")

    def test_a_row_reading_names_its_worker_and_its_owning_session(self):
        self.full_epic()
        self.sweep("--session-id", ORCH)
        reading = self.row("7")["spend"]["w7"]
        self.assertEqual((reading["worker"], reading["kind"], reading["usage"], reading["forks"], reading["under"]),
                         ("lane-7", "subagent", 2.5, 1, ORCH))

    def test_a_fork_with_no_meta_stays_out_of_the_worker_but_in_the_epic(self):
        self.write_session(ORCH, [turn("o1", usd=1.0)])
        self.write_subagent(ORCH, "w7", [turn("w7-1", usd=2.0, tools=("toolu_f1",))])
        self.write_subagent(ORCH, "f1", [turn("f1-1", usd=0.5)])
        self.plan("7")
        self.assign("7", "w7")
        self.sweep("--session-id", ORCH)
        self.assertEqual(self.row("7")["spent"], 2.0)
        self.assertEqual(self.ledger()["spent"], 3.5)

    def test_nested_forks_count_toward_the_worker(self):
        self.write_subagent(ORCH, "w7", [turn("w7-1", usd=1.0, tools=("toolu_f1",))])
        self.write_subagent(ORCH, "f1", [turn("f1-1", usd=1.0, tools=("toolu_f2",))], call="toolu_f1")
        self.write_subagent(ORCH, "f2", [turn("f2-1", usd=1.0)], call="toolu_f2")
        self.write_subagent(ORCH, "other", [turn("o-1", usd=9.0)], call="toolu_other")
        self.plan("7")
        self.assign("7", "w7")
        self.sweep()
        self.assertEqual(self.row("7")["spent"], 3.0)

    def test_a_smaller_reading_never_lowers_spent(self):
        path = self.write_session("peer-sid", [turn("p1", usd=4.0)])
        self.plan("7")
        self.assign("7", "peer-sid", "peer")
        self.sweep()
        path.write_text(json.dumps(turn("p1", usd=1.0)) + "\n", encoding="utf-8")
        self.sweep()
        self.assertEqual(self.row()["spent"], 4.0)
        path.unlink()
        out = self.sweep()
        self.assertEqual(self.row()["spent"], 4.0)
        self.assertIn("spend-unread 7", out)

    def test_reconcile_takes_the_higher_reading_either_way(self):
        for usage, reported, want in ((2.0, 1.5, 2.0), (2.0, 2.75, 2.75)):
            with self.subTest(usage=usage, reported=reported):
                self.setUp()
                path = self.write_session("head-sid", [turn("h1", usd=usage)])
                self.plan("7")
                self.assign("7", str(path), "headless")
                self.write_result("lane-7", json.dumps({"total_cost_usd": reported}))
                self.sweep()
                self.assertEqual(self.row()["spent"], want)

    def test_a_headless_run_reads_the_last_stream_line(self):
        self.plan("7")
        self.assign("7", None, "headless")
        self.write_result("lane-7", "\n".join((json.dumps({"type": "system"}), "not json", json.dumps({"total_cost_usd": 1.25}))))
        self.sweep()
        self.assertEqual(self.row()["spent"], 1.25)

    def test_a_result_with_no_usable_amount_is_unread_not_zero(self):
        for bad in ('{"total_cost_usd": true}', '{"total_cost_usd": -1}', '{"total_cost_usd": NaN}', '{"total_cost_usd": "3"}',
                    '{"other": 1}', "not json"):
            with self.subTest(bad=bad):
                self.setUp()
                self.plan("7")
                self.assign("7", None, "headless", "--cap-usd", "5")
                self.write_result("lane-7", bad)
                out = self.sweep()
                self.assertIn("spend-unread 7", out)
                self.assertIn("no-spawn spend unread for 7", out)
                self.assertEqual(self.row()["spent"], 0)

    def test_a_missing_transcript_is_unread_and_refuses_spawn_under_a_dollar_cap(self):
        self.plan("7", epic_usd=10)
        self.assign("7", "nobody")
        out = self.sweep("--session-id", "nobody-either")
        self.assertIn("spend-unread 7", out)
        self.assertIn("no-spawn spend unread for 7", out)
        self.assertIn("spend-unread orchestrator", out)

    def test_a_transcript_the_readers_choke_on_is_unread_not_a_crash(self):
        path = self.write_session("peer-sid", [])
        path.write_text(json.dumps({"type": "assistant", "requestId": "r1", "message": "not an object"}) + "\n", encoding="utf-8")
        self.plan("7")
        self.assign("7", "peer-sid", "peer", "--cap-usd", "5")
        p = self.run_script(BUDGET, "sweep", EPIC)
        self.assertNotIn("Traceback", p.stderr)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("spend-unread 7", p.stdout)
        self.assertIn("no-spawn spend unread for 7", p.stdout)
        self.assertEqual(self.row()["spent"], 0)

    def test_an_ended_worker_with_a_figure_by_hand_stops_refusing(self):
        self.plan("7", epic_usd=10)
        self.assign("7", "gone")
        self.ok(SWARM, "apply", EPIC, f"READY 7 {'a' * 40} v=1 id=x1 from=lane-7")
        self.assertIn("no-spawn spend unread for 7", self.sweep("--session-id", "gone-too"))
        self.ok(SWARM, "progress", EPIC, "7", "--spent", "2.5")
        self.assertNotIn("no-spawn spend unread for 7", self.sweep("--session-id", "gone-too"))

    def test_a_worker_with_no_ref_is_told_how_to_name_it(self):
        self.plan("7")
        self.assign("7", None)
        out = self.sweep()
        self.assertIn("swarm.py assign --ref", out)

    def test_an_unread_worker_refuses_nothing_with_no_dollar_cap(self):
        self.plan("7")
        self.assign("7", "nobody")
        out = self.sweep()
        self.assertIn("spend-unread 7", out)
        self.assertNotIn("no-spawn", out)

    def test_tick_flags_a_cap_breach_from_what_the_sweep_wrote(self):
        self.write_subagent(ORCH, "w7", [turn("w7-1", usd=3.0)])
        self.plan("7")
        self.assign("7", "w7", "subagent", "--cap-usd", "2")
        self.assertNotIn("over-budget 7", self.ok(SWARM, "tick", EPIC))
        self.assertIn("over-budget 7 kind=spend value=3.0 cap=2.0", self.sweep())
        self.assertIn("over-budget 7", self.ok(SWARM, "tick", EPIC))

    def test_past_the_epic_cap_tick_assigns_nothing_and_the_digest_says_so(self):
        self.write_session(ORCH, [turn("o1", usd=11.0)])
        self.plan("7", "8", epic_usd=10)
        self.assign("7", None)
        self.sweep("--session-id", ORCH)
        tick = self.ok(SWARM, "tick", EPIC)
        self.assertRegex(tick, r"no-assign .*epic budget at its cap")
        digest = self.ok(BUDGET, "digest", EPIC)
        self.assertIn("Epic cap reached", digest)
        self.assertIn("$11.00 of $10.00", digest)

    def test_status_and_digest_write_nothing_and_read_no_transcript(self):
        self.write_subagent(ORCH, "w7", [turn("w7-1", usd=3.0)])
        self.plan("7")
        self.assign("7", "w7")
        before = self.ledger()
        status = self.ok(BUDGET, "status", EPIC)
        self.ok(BUDGET, "digest", EPIC)
        self.assertEqual(self.ledger(), before)
        self.assertIn("spent 7 worker=lane-7", status)
        self.assertIn("spent=0.00", status)
        self.assertIn("spend-unread 7", status)

    def test_a_second_worker_on_the_row_adds_to_the_first(self):
        self.write_session("peer-1", [turn("a1", usd=2.0)])
        self.write_session("peer-2", [turn("b1", usd=1.5)])
        self.plan("7")
        self.assign("7", "peer-1", "peer")
        self.sweep()
        self.ok(SWARM, "assign", EPIC, "7", "--kind", "peer", "--name", "lane-7b", "--ref", "peer-2", "--branch", "feat/7-x")
        self.sweep()
        self.assertEqual(self.row()["spent"], 3.5)

    def test_a_sweep_with_nothing_changed_leaves_the_ledger_alone(self):
        self.write_session("peer-sid", [turn("p1", usd=2.0)])
        self.plan("7")
        self.assign("7", "peer-sid", "peer")
        self.sweep()
        before = self.ledger()
        self.sweep()
        self.assertEqual(self.ledger(), before)


class OtherCaps(BudgetCase):
    """The fork and minute caps, and the assignment's four fields."""

    def forks(self, count: int) -> None:
        self.write_subagent(ORCH, "w7", [turn("w7-1", tools=tuple(f"toolu_{i}" for i in range(count)))])
        self.plan("7")
        self.assign("7", "w7")

    def test_the_default_fork_cap_flags_the_fifth(self):
        self.forks(4)
        self.assertNotIn("over-budget", self.sweep())
        self.setUp()
        self.forks(5)
        self.assertIn("over-budget 7 kind=forks value=5 cap=4", self.sweep())

    def test_the_minute_cap_runs_from_the_last_assignment(self):
        self.config(worker_minutes=30)
        self.write_subagent(ORCH, "w7", [turn("w7-1")])
        self.plan("7")
        self.assign("7", "w7")
        begun = self.assign_time()
        at = begun + timedelta(minutes=30, seconds=59)
        self.assertNotIn("over-budget", self.sweep("--now", at.strftime(swarm.LEDGER_TIME)))
        at = begun + timedelta(minutes=31)
        self.assertIn("over-budget 7 kind=minutes value=31 cap=30", self.sweep("--now", at.strftime(swarm.LEDGER_TIME)))

    def test_a_rebrief_restarts_the_minute_clock(self):
        self.config(worker_minutes=30)
        self.write_subagent(ORCH, "w7", [turn("w7-1")])
        self.plan("7")
        self.assign("7", "w7")
        self.ok(SWARM, "assign", EPIC, "7", "--rebrief")
        rebriefed = swarm.parse_time(self.ledger()["log"][-1]["at"])
        at = rebriefed + timedelta(minutes=30, seconds=59)
        self.assertNotIn("over-budget", self.sweep("--now", at.strftime(swarm.LEDGER_TIME)))

    def test_a_cap_cannot_be_negative(self):
        self.plan("7")
        self.assign("7", None)
        self.assertEqual(self.run_script(BUDGET, "caps", EPIC, "7", "--forks", "-1").returncode, 2)

    def test_a_worker_that_ended_is_past_no_fork_or_minute_cap(self):
        self.config(worker_minutes=1)
        self.forks(9)
        head = "a" * 40
        self.ok(SWARM, "apply", EPIC, f"READY 7 {head} v=1 id=x1 from=lane-7")
        later = self.assign_time() + timedelta(hours=5)
        self.assertNotIn("over-budget", self.sweep("--now", later.strftime(swarm.LEDGER_TIME)))

    def test_caps_prints_the_four_field_names(self):
        self.config(worker_usd=5, worker_minutes=60)
        self.plan("7")
        self.assign("7", None)
        self.assertEqual(self.ok(BUDGET, "caps", EPIC, "7").splitlines(),
                         ["spend_usd: 5", "forks: 4", "minutes: 60", "split_turns: 10"])

    def test_a_headless_assignment_also_prints_the_flag(self):
        self.plan("7")
        self.assign("7", None, "headless", "--cap-usd", "2.5")
        self.assertIn("flag: --max-budget-usd 2.5", self.ok(BUDGET, "caps", EPIC, "7"))

    def test_a_raise_needs_the_owners_decision_line_a_lower_cap_does_not(self):
        self.plan("7")
        self.assign("7", None)
        self.ok(BUDGET, "caps", EPIC, "7", "--forks", "2")
        p = self.run_script(BUDGET, "caps", EPIC, "7", "--forks", "3")
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("needs the owner's --decision", p.stderr)
        self.assertEqual(self.row()["caps"]["forks"], 2)
        self.ok(BUDGET, "caps", EPIC, "7", "--forks", "3", "--decision", "owner: one more")
        self.assertEqual(self.row()["caps"]["forks"], 3)
        self.assertEqual(self.ledger()["log"][-1]["note"], "owner: one more")

    def test_a_first_cap_needs_no_decision_line(self):
        self.plan("7")
        self.assign("7", None)
        self.ok(BUDGET, "caps", EPIC, "7", "--minutes", "45")
        self.assertEqual(self.row()["caps"]["minutes"], 45)

    def test_a_row_cap_wins_over_the_config(self):
        self.config(worker_forks=9)
        self.forks(3)
        self.ok(BUDGET, "caps", EPIC, "7", "--forks", "2")
        self.assertIn("over-budget 7 kind=forks value=3 cap=2", self.sweep())

    def test_a_cap_that_does_not_parse_refuses_the_next_spawn(self):
        self.config(worker_usd="ten", split_turns=True)
        self.plan("7")
        self.assign("7", None)
        out = self.ok(BUDGET, "status", EPIC)
        self.assertIn("no-spawn budget config: swarm.budget.worker_usd is not a number", out)
        self.assertIn("no-spawn budget config: swarm.budget.split_turns is not a number", out)

    def test_an_unknown_ledger_or_ticket_exits_with_swarms_codes(self):
        self.assertEqual(self.run_script(BUDGET, "status", "nothing").returncode, 1)
        self.plan("7")
        self.assertEqual(self.run_script(BUDGET, "caps", EPIC, "99").returncode, 1)


class HardStop(BudgetCase):
    """DW-3: a breach with `swarm.budget.hard_stop` true writes the STOP file, and `tick` refuses to assign under it."""

    def breach(self, kind: str = "subagent", **config) -> None:
        self.config(**config)
        self.write_subagent(ORCH, "w7", [turn("w7-1", usd=3.0)])
        self.plan("7", "8")
        self.assign("7", "w7", kind, "--cap-usd", "2")

    def test_a_breach_with_the_stop_armed_writes_it_and_tick_assigns_nothing(self):
        self.breach(hard_stop=True)
        out = self.sweep()
        self.assertIn(f"hard-stop {EPIC} written", out)
        stop = json.loads(self.stop_file().read_text(encoding="utf-8"))
        self.assertEqual(stop["by"], "orchestrator")
        self.assertIn("budget: 7 spent $3.00 of $2.00", stop["reason"])
        self.assertTrue(any(e["act"] == "hard-stop" for e in self.ledger()["log"]))
        tick = self.ok(SWARM, "tick", EPIC)
        self.assertIn(f"no-assign STOP on {EPIC}", tick)
        self.assertNotIn("assignable 8", tick)
        p = self.run_script(SWARM, "assign", EPIC, "8", "--kind", "subagent", "--name", "lane-8", "--branch", "feat/8-x")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("STOP", p.stderr)

    def test_with_the_stop_off_a_breach_writes_no_file(self):
        self.breach(hard_stop=False)
        out = self.sweep()
        self.assertIn("over-budget 7", out)
        self.assertNotIn("hard-stop", out)
        self.assertFalse(self.stop_file().exists())

    def test_the_stop_is_off_by_default(self):
        self.breach()
        self.sweep()
        self.assertFalse(self.stop_file().exists())

    def test_a_hard_stop_nobody_can_read_arms_the_stop_and_says_so(self):
        self.breach(hard_stop="false")
        out = self.sweep()
        self.assertTrue(self.stop_file().exists())
        self.assertIn("no-spawn budget config: swarm.budget.hard_stop is not true or false", out)

    def test_no_breach_no_stop(self):
        self.config(hard_stop=True)
        self.write_subagent(ORCH, "w7", [turn("w7-1", usd=1.0)])
        self.plan("7")
        self.assign("7", "w7", "subagent", "--cap-usd", "2")
        self.sweep()
        self.assertFalse(self.stop_file().exists())

    def test_a_running_subagent_gets_a_task_stop_line_by_its_id_a_peer_does_not(self):
        self.breach(hard_stop=True)
        self.write_session("peer-sid", [turn("p1")])
        self.assign("8", "peer-sid", "peer")
        out = self.sweep()
        self.assertIn("task-stop 7 task=w7", out)
        self.assertNotIn("task-stop 8", out)
        self.assertNotRegex(out, r"\bpid\b|\bkill\b")

    def test_a_stop_that_cannot_be_written_fails_loudly_and_refuses_spawn(self):
        self.breach(hard_stop=True)
        (self.root / "state" / "swarm" / "stops.lock").mkdir(parents=True)  # the stop's lock file cannot open
        p = self.run_script(BUDGET, "sweep", EPIC)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn(f"hard-stop {EPIC} failed", p.stdout)
        self.assertIn("no-spawn hard stop not written", p.stdout)
        self.assertNotIn("task-stop", p.stdout)
        self.assertFalse(self.stop_file().exists())

    def test_the_owners_stop_stands_and_a_breach_is_no_failure(self):
        self.breach(hard_stop=True)
        self.ok(LEASE, "stop", EPIC, "--reason", "owner says stop", "--as", "owner")
        out = self.sweep()
        self.assertIn(f"hard-stop {EPIC} already", out)
        stop = json.loads(self.stop_file().read_text(encoding="utf-8"))
        self.assertEqual((stop["by"], stop["reason"]), ("owner", "owner says stop"))
        self.assertIn("task-stop 7", out)

    def test_a_stop_lifted_with_the_breach_standing_comes_back(self):
        self.breach(hard_stop=True)
        self.sweep()
        self.ok(LEASE, "go", EPIC, "--as", "orchestrator")
        self.assertFalse(self.stop_file().exists())
        self.sweep()
        self.assertTrue(self.stop_file().exists())

    def test_the_digest_names_the_stop_and_how_it_lifts(self):
        self.breach(hard_stop=True)
        self.sweep()
        digest = self.ok(BUDGET, "digest", EPIC)
        self.assertIn(f"STOP {EPIC} by the orchestrator: budget: 7 spent $3.00 of $2.00. Nothing spawns.", digest)
        self.assertIn(f"lease.py go {EPIC}", digest)
        self.assertIn("#7 lane-7: $3.00 / $2.00, forks 0 / 4", digest)


class SplitBreach(BudgetCase):
    """DW-5: the stats line's split hint for `swarm.budget.split_turns` turns is flagged over budget like a dollar breach."""

    def worker(self, turns: list[dict]) -> str:
        self.write_subagent(ORCH, "w7", turns)
        self.plan("7")
        self.assign("7", "w7")
        return self.sweep()

    def test_the_tenth_turn_on_the_hint_is_the_breach(self):
        self.assertNotIn("over-budget", self.worker(fat_turns(28)))  # a window of 20: the hint has been on for 9 turns
        self.assertEqual(self.row()["spend"]["w7"]["split_streak"], 9)
        self.setUp()
        self.assertIn("over-budget 7 kind=split value=10 cap=10", self.worker(fat_turns(29)))

    def test_it_trips_the_hard_stop_like_a_dollar_breach(self):
        self.config(hard_stop=True)
        out = self.worker(fat_turns(29))
        self.assertIn(f"hard-stop {EPIC} written", out)
        self.assertIn("7 carried the split hint for 10 turns", json.loads(self.stop_file().read_text(encoding="utf-8"))["reason"])

    def test_the_digest_names_it(self):
        self.worker(fat_turns(29))
        self.assertIn("Over budget: 7 carried the split hint for 10 turns, cap 10.", self.ok(BUDGET, "digest", EPIC))

    def test_a_compaction_resets_the_streak(self):
        turns = [*fat_turns(40), {"type": "user", "isCompactSummary": True}, *fat_turns(5, prefix="after")]
        self.assertNotIn("over-budget", self.worker(turns))
        self.assertEqual(self.row()["spend"]["w7"]["split_streak"], 0)

    def test_a_thin_stretch_resets_the_streak(self):
        turns = [*fat_turns(40), *[turn(f"thin{i}", ctx=1000) for i in range(6)]]
        self.assertNotIn("over-budget", self.worker(turns))
        self.assertEqual(self.row()["spend"]["w7"]["split_streak"], 0)

    def test_a_streaming_request_counts_as_one_turn(self):
        turns = [t for f in fat_turns(29) for t in (f, f)]
        self.assertIn("over-budget 7 kind=split value=10", self.worker(turns))

    def test_the_stats_lines_threshold_is_the_one_read(self):
        self.env["SESSION_STATS_SPLIT_THRESHOLD"] = str(FAT + 1)
        self.assertNotIn("over-budget", self.worker(fat_turns(40)))

    def test_the_last_turn_agrees_with_the_stats_line(self):
        cases = {"under a window": fat_turns(19), "a full window": fat_turns(20), "long": fat_turns(40),
                 "thin": [turn(f"t{i}", ctx=1000) for i in range(30)],
                 "turned thin": [*fat_turns(40), *[turn(f"t{i}", ctx=1000) for i in range(6)]],
                 "compacted": [*fat_turns(40), {"type": "user", "isCompactSummary": True}, *fat_turns(20, prefix="after")]}
        with mock.patch.dict(os.environ):
            for name in ("SESSION_STATS_SPLIT_WINDOW", "SESSION_STATS_SPLIT_THRESHOLD"):
                os.environ.pop(name, None)
            for name, turns in cases.items():
                with self.subTest(name):
                    path = self.write_session("parity", turns)
                    hint = swarm_budget.session_stats.split_hint(swarm_budget.session_stats.collect(str(path)))
                    self.assertEqual(hint is not None, swarm_budget.split_streak(str(path)) > 0)

    def test_the_config_sets_the_turns(self):
        self.config(split_turns=3)
        self.assertIn("over-budget 7 kind=split value=9 cap=3", self.worker(fat_turns(28)))

    def test_the_orchestrators_own_hint_is_a_breach_of_the_epic(self):
        self.config(hard_stop=True)
        self.write_session(ORCH, fat_turns(29))
        self.plan("7")
        out = self.sweep("--session-id", ORCH)
        self.assertIn("over-budget orchestrator kind=split value=10 cap=10", out)
        self.assertTrue(self.stop_file().exists())

    def test_a_worker_past_its_run_carries_no_split_breach(self):
        self.write_subagent(ORCH, "w7", fat_turns(29))
        self.plan("7")
        self.assign("7", "w7")
        self.ok(SWARM, "apply", EPIC, f"READY 7 {'a' * 40} v=1 id=x1 from=lane-7")
        self.assertNotIn("over-budget", self.sweep())


class WorkerBrief(unittest.TestCase):
    """DW-2: the brief states the breach behaviour and the rule that a worker never extends its own cap."""

    def test_the_brief_states_the_breach_and_the_no_self_extend_rule(self):
        text = (KIT / "docs" / "delegation.md").read_text(encoding="utf-8")
        section = re.search(r"^## The worker brief\n(.*?)(?=^## )", text, re.M | re.S).group(1)
        item = re.search(r"\*\*Budget.*", section, re.S)
        self.assertIsNotNone(item, "the brief has no budget item")
        line = " ".join(item.group(0).split())
        for fragment in ("finish the current step", "push", "`BLOCKED <ticket> budget orchestrator`", "`session-handoff`",
                         "start nothing new", "never extends its own cap", "`split_turns`"):
            self.assertIn(fragment, line)
        for field in swarm_budget.CAP_FIELDS:
            self.assertIn(f"`{field}`", line)


class CostReportSection(BudgetCase):
    """DW-4: `cost-report` prints a swarm section from a fixture ledger."""

    SKILL = KIT / "skills" / "cost-report"
    SCRIPT = SKILL / "cost_report.py"
    DAILY = SKILL / "fixtures" / "daily.sample.json"
    SAMPLE = SKILL / "fixtures" / "swarm-ledger.sample.json"

    def report(self, *args: str) -> subprocess.CompletedProcess:
        return self.run_script(self.SCRIPT, "report", "--daily", str(self.DAILY), *args)

    def test_a_fixture_ledger_prints_the_epic_and_each_worker(self):
        p = self.report("--swarm-ledger", str(self.SAMPLE))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("## Swarm (spend per epic and per worker, a list-price estimate, never a bill)", p.stdout)
        self.assertIn("| Swarm | 1 task ledger(s), list-price estimate", p.stdout)
        self.assertIn("| sample-epic | 26.00 | 40.00 | 3 | 1 |", p.stdout)
        self.assertIn("| sample-epic | 101 | lane-101 | subagent | merged | 7.50 | 10.00 | 2 / 4 |", p.stdout)
        self.assertIn("| sample-epic | 102 | lane-102 | peer | claimed | 11.00 | 10.00 | 1 / 4 |", p.stdout)
        self.assertIn("| sample-epic | 103 | lane-103 | headless | claimed | 3.00 | 5.00 | 0 / 4 |", p.stdout)

    def test_the_fixture_is_the_ledgers_real_shape(self):
        led = json.loads(self.SAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(led["schema"], swarm.SCHEMA)
        for row in led["tickets"].values():
            self.assertEqual(set(swarm.new_row("x")) - set(row), set())

    def test_with_no_flag_it_reads_the_stores_ledgers(self):
        self.write_session("peer-sid", [turn("p1", usd=2.0)])
        self.plan("7")
        self.assign("7", "peer-sid", "peer")
        self.sweep()
        p = self.report()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f"| {EPIC} | 7 | lane-7 | peer | assigned | 2.00 |", p.stdout)

    def test_a_store_file_that_is_no_ledger_is_skipped_and_said(self):
        self.plan("7")
        (self.root / "state" / "swarm" / "stray.json").write_text("{not json", encoding="utf-8")
        p = self.report()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("skipped, not a ledger: stray.json", p.stdout)
        self.assertIn(f"| {EPIC} |", p.stdout)

    def test_a_named_file_that_is_no_ledger_exits_2_naming_it(self):
        p = self.report("--swarm-ledger", str(self.DAILY))
        self.assertEqual(p.returncode, 2)
        self.assertIn(str(self.DAILY), p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_with_no_ledger_the_basis_says_so_and_there_is_no_section(self):
        p = self.report()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("| Swarm | no task ledger |", p.stdout)
        self.assertNotIn("## Swarm", p.stdout)

    def test_the_skill_keeps_its_description_and_names_the_section(self):
        text = (self.SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("`--swarm-ledger <file>`", text)
        self.assertNotIn("swarm", re.search(r"^description: (.*)$", text, re.M).group(1).lower())


class ConfigKeys(unittest.TestCase):
    """The `swarm.budget` block sits in the template, `kb.blank_config()` and `docs/env-facts.md` with one set of defaults."""

    WANT = {"epic_usd": None, "worker_usd": None, "worker_forks": 4, "worker_minutes": None, "split_turns": 10, "hard_stop": False}

    def test_template_and_blank_config_carry_the_block(self):
        template = json.loads((KIT / "environment-template" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(template["swarm"]["budget"], self.WANT)
        self.assertEqual(kb.blank_config()["swarm"]["budget"], self.WANT)

    def test_the_scripts_defaults_are_the_templates(self):
        self.assertEqual({**swarm_budget.CONFIG_DEFAULTS, "hard_stop": False}, self.WANT)

    def test_env_facts_names_every_key(self):
        text = (KIT / "docs" / "env-facts.md").read_text(encoding="utf-8")
        for name in self.WANT:
            self.assertIn(f"`swarm.budget.{name}`", text)


if __name__ == "__main__":
    unittest.main()
