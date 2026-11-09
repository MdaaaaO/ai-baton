"""session-register/zombie-sweep.sh — lists, and with --kill ends, the claude processes still running for
sessions whose registry row already says `ended` (session-handoff ran, the pane never closed — #655). The map
from process to session comes from the heartbeat logs' `start name=<name> owner=<pid>` lines, so the fixture
is a throw-away log dir under TMPDIR, a throw-away .context/ with rows, and sleep processes standing in for
the owners (a shell script named `node` carries a claude-shaped comm — a script's comm is its own basename —
a plain sleep does not). Every verdict
the script can print is covered once, the verdict is per owner across every log that names it (a handoff, then
a new session registered in the same pane), an active row keeps an owner LIVE even when only a dead-loop log
names it while a dead-loop log's missing row never blocks a kill (the two review findings on #665), and --kill
must reach exactly the ZOMBIE owners — never a live, unregistered, stale, reused or dead one, never one with an
active row, nor the sweep's own ancestry. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SWEEP = KIT / "skills" / "session-register" / "zombie-sweep.sh"
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402

LOG_LINE = "2026-11-02 10:00 AM EST start name={name} owner={owner} interval=21600 session=none\n"
NODE_SCRIPT = """#!/bin/sh
# stands in for a claude process: a script's comm is its own basename (`node`); SIGTERM ends it and its sleep
trap 'kill $! 2>/dev/null; exit 0' TERM
sleep 300 &
wait $!
"""


def comm_of(pid: int) -> str:
    return subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


class ZombieSweepFixture(unittest.TestCase):
    """One heartbeat log per case, each with its own owner process; one shared live heartbeat-loop stand-in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.tdir = t / "t"                                   # TMPDIR for the script: logs live under it
        self.logs = self.tdir / f"ai-baton-{os.getuid()}"
        self.logs.mkdir(parents=True)
        self.ctx = t / "ws" / ".context"
        (self.ctx / "sessions" / "archive").mkdir(parents=True)
        self.node = t / "bin" / "node"                        # the stand-in claude process (comm `node`)
        self.node.parent.mkdir()
        self.node.write_text(NODE_SCRIPT)
        self.node.chmod(0o755)
        self.procs: list[subprocess.Popen] = []
        self.loop = self.spawn("sleep")                       # the live heartbeat loop every mapped log points at
        probe = self.spawn("node")
        if comm_of(probe.pid) != "node":
            self.skipTest("a script's comm is not its own name here")
        self.env = dict(hermetic_env(t), TMPDIR=str(self.tdir), CONTEXT_ROOT=str(self.ctx))

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
            p.wait(timeout=10)
        self.tmp.cleanup()

    def spawn(self, kind: str) -> subprocess.Popen:
        exe = str(self.node) if kind == "node" else "sleep"
        p = subprocess.Popen([exe, "300"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def gone(self) -> int:
        """A pid nothing runs under any more."""
        p = subprocess.Popen(["true"])
        p.wait(timeout=10)
        return p.pid

    def log(self, key: str, name: str, owner: int, loop: int | None = None) -> None:
        (self.logs / f"heartbeat-{key}.log").write_text(LOG_LINE.format(name=name, owner=owner))
        if loop is not None:
            (self.logs / f"heartbeat-{key}.pid").write_text(f"{loop}\n")

    def row(self, name: str, status: str, archived: str | None = None, ended_at: str | None = None) -> None:
        text = f"---\nsession: {name}\nsession_id: none\nstatus: {status}\n" + (
            f"ended_at: {ended_at}\n" if ended_at else "") + "---\n"
        if archived:
            (self.ctx / "sessions" / "archive" / archived).write_text(text)
        else:
            (self.ctx / "sessions" / f"{name}.md").write_text(text)

    def sweep(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SWEEP), *args], env=self.env, capture_output=True, text=True,
                              timeout=60)

    def standby(self, name: str, expires: str) -> Path:
        f = self.ctx / "sessions" / f"{name}.md"
        f.write_text(f"---\nsession: {name}\nstatus: active\nrole: worker\nstate: standby\nexpires: {expires}\n"
                     "heartbeat: 2026-11-01T00:00:00Z\n---\n")
        (self.ctx / "state" / "swarm").mkdir(parents=True, exist_ok=True)
        (self.ctx / "state" / "swarm" / f"E-{name}.json").write_text(json.dumps(
            {"key": "E-1", "repo": "o/r", "session": "orch",
             "tickets": {"7": {"status": "assigned", "repo": "o/r", "pr": None, "worker": {"name": name}}}}))
        return f

    def build_all_cases(self) -> dict[str, subprocess.Popen]:
        owners = {}
        loop = self.loop.pid
        owners["z-ended"] = self.spawn("node")                       # ended row, claude-shaped, loop alive → ZOMBIE
        self.log("z-ended", "z-ended", owners["z-ended"].pid, loop)
        self.row("z-ended", "ended")
        self.log("z-ended-oldkey", "z-ended", owners["z-ended"].pid, loop)   # a second log for the same owner
        owners["z-arch"] = self.spawn("node")                        # row only under archive/ → ZOMBIE row=archived
        self.log("z-arch", "z-arch", owners["z-arch"].pid, loop)
        self.row("z-arch", "ended", archived="z-arch-20261101.md")
        owners["l-active"] = self.spawn("node")                      # row active → LIVE
        self.log("l-active", "l-active", owners["l-active"].pid, loop)
        self.row("l-active", "active")
        owners["u-none"] = self.spawn("node")                        # no row anywhere → UNREGISTERED
        self.log("u-none", "u-none", owners["u-none"].pid, loop)
        owners["s-stale"] = self.spawn("node")                       # loop pid dead → STALE (whatever the row says)
        self.log("s-stale", "s-stale", owners["s-stale"].pid, self.gone())
        self.row("s-stale", "ended")
        owners["r-reused"] = self.spawn("sleep")                     # comm not claude-shaped → REUSED
        self.log("r-reused", "r-reused", owners["r-reused"].pid, loop)
        self.row("r-reused", "ended")
        self.log("d-dead", "d-dead", self.gone(), loop)              # owner gone → DEAD
        self.row("d-dead", "ended")
        (self.logs / "heartbeat-nostart.log").write_text("nothing here\n")   # → UNMAPPED
        return owners

    def build_shared_owner_cases(self) -> dict[str, subprocess.Popen]:
        """One owner, several logs: what a handoff followed by a new registration in the same pane, an earlier
        key, a rename without a session id, or a log from before a reboot leave behind. The ended log sorts first
        in every pair, the shape the first review finding on #665 named; the last two pairs are the second."""
        owners = {}
        loop = self.loop.pid
        owners["pair-live"] = self.spawn("node")                     # A ended, then B registered → LIVE
        self.log("aa-ended", "aa-ended", owners["pair-live"].pid, loop)
        self.row("aa-ended", "ended")
        self.log("ab-active", "ab-active", owners["pair-live"].pid, loop)
        self.row("ab-active", "active")
        owners["pair-unreg"] = self.spawn("node")                    # A ended, B has no row → UNREGISTERED
        self.log("ba-ended", "ba-ended", owners["pair-unreg"].pid, loop)
        self.row("ba-ended", "ended")
        self.log("bb-none", "bb-none", owners["pair-unreg"].pid, loop)
        owners["pair-zombie"] = self.spawn("node")                   # A ended, B archived → one ZOMBIE, both named
        self.log("ca-ended", "ca-ended", owners["pair-zombie"].pid, loop)
        self.row("ca-ended", "ended")
        self.log("cb-arch", "cb-arch", owners["pair-zombie"].pid, loop)
        self.row("cb-arch", "ended", archived="cb-arch-20261101.md")
        owners["stale-beside"] = self.spawn("node")                  # a dead-loop log (row active) beside a
        self.log("da-old", "da-old", owners["stale-beside"].pid, self.gone())   # live-loop log (row ended): the
        self.row("da-old", "active")                                            # loop died under a working
        self.log("db-ended", "db-ended", owners["stale-beside"].pid, loop)      # session → its active row keeps
        self.row("db-ended", "ended")                                           # the owner LIVE
        owners["renamed-beside"] = self.spawn("node")                # a dead-loop log (no row: the name was
        self.log("ea-gone", "ea-gone", owners["renamed-beside"].pid, self.gone())   # renamed away) beside a
        self.log("eb-ended", "eb-ended", owners["renamed-beside"].pid, loop)        # live-loop log (row ended)
        self.row("eb-ended", "ended")                                               # → ZOMBIE, the gap no block
        return owners


class ListMode(ZombieSweepFixture):
    def test_every_verdict_and_the_summary(self):
        owners = self.build_all_cases()
        r = self.sweep()
        out = r.stdout.splitlines()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        pid = {k: v.pid for k, v in owners.items()}
        self.assertIn(f"ZOMBIE z-ended owner={pid['z-ended']} comm=node row=ended", out)
        self.assertIn(f"ZOMBIE z-arch owner={pid['z-arch']} comm=node row=archived", out)
        self.assertIn(f"LIVE l-active owner={pid['l-active']} row=active", out)
        self.assertIn(f"UNREGISTERED u-none owner={pid['u-none']} row=none", out)
        self.assertTrue(any(line.startswith(f"STALE s-stale owner={pid['s-stale']} ") for line in out), out)
        self.assertIn(f"REUSED r-reused owner={pid['r-reused']} comm=sleep", out)
        self.assertTrue(any(line.startswith("DEAD d-dead owner=") for line in out), out)
        self.assertIn("UNMAPPED heartbeat-nostart.log", out)
        # the second log for z-ended's owner adds no second verdict
        self.assertEqual(sum(1 for line in out if " z-ended " in line), 1, out)
        self.assertEqual(out[-1], "zombie-sweep: 2 zombie(s), 1 live, 1 unregistered, 1 stale, 1 reused, 1 dead, 1 unmapped, 0 recent")
        for name, p in owners.items():
            self.assertIsNone(p.poll(), f"list mode must not touch any process ({name})")

    def test_one_verdict_per_owner_across_its_logs(self):
        owners = self.build_shared_owner_cases()
        r = self.sweep()
        out = r.stdout.splitlines()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        pid = {k: v.pid for k, v in owners.items()}
        self.assertIn(f"LIVE aa-ended,ab-active owner={pid['pair-live']} row=ended,active", out)
        self.assertIn(f"UNREGISTERED ba-ended,bb-none owner={pid['pair-unreg']} row=ended,none", out)
        self.assertIn(f"ZOMBIE ca-ended,cb-arch owner={pid['pair-zombie']} comm=node row=ended,archived", out)
        self.assertIn(f"LIVE da-old,db-ended owner={pid['stale-beside']} row=active,ended", out)
        self.assertIn(f"ZOMBIE ea-gone,eb-ended owner={pid['renamed-beside']} comm=node row=none,ended", out)
        self.assertEqual(out[-1], "zombie-sweep: 2 zombie(s), 2 live, 1 unregistered, 0 stale, 0 reused, 0 dead, 0 unmapped, 0 recent")
        for name, p in owners.items():
            self.assertIsNone(p.poll(), f"list mode must not touch any process ({name})")

    def test_nothing_to_sweep_exits_zero(self):
        r = self.sweep()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "zombie-sweep: 0 zombie(s), 0 live, 0 unregistered, 0 stale, 0 reused, 0 dead, 0 unmapped, 0 recent")

    def test_list_mode_names_a_standby_row_past_its_time_and_retires_nothing(self):
        f = self.standby("w-late", "2026-11-02T00:00:00Z")
        self.standby("w-early", "2999-01-01T00:00:00Z")
        before = f.read_text()
        r = self.sweep()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would retire w-late", r.stdout)
        self.assertIn("owns: ticket 7", r.stdout)
        self.assertNotIn("w-early", r.stdout)
        self.assertEqual(f.read_text(), before)

    def test_no_registry_is_a_usage_error(self):
        shutil.rmtree(self.ctx / "sessions")
        r = self.sweep()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no sessions/", r.stderr)

    def test_unknown_flag_is_refused(self):
        r = self.sweep("--nuke")
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage:", r.stderr)


class KillMode(ZombieSweepFixture):
    def test_kill_mode_retires_a_standby_row_past_its_time(self):
        f = self.standby("w-late", "2026-11-02T00:00:00Z")
        r = self.sweep("--kill")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("retired w-late", r.stdout)
        self.assertIn("owns: ticket 7", r.stdout)
        self.assertIn("state: retired", f.read_text())

    def test_kill_reaches_exactly_the_zombie_owners(self):
        owners = self.build_all_cases()
        r = self.sweep("--kill")
        out = r.stdout.splitlines()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pid = {k: v.pid for k, v in owners.items()}
        self.assertIn(f"KILLED z-ended owner={pid['z-ended']} comm=node row=ended", out)
        self.assertIn(f"KILLED z-arch owner={pid['z-arch']} comm=node row=archived", out)
        self.assertFalse(any(line.startswith("ZOMBIE ") for line in out), out)
        self.assertTrue(out[-1].startswith("zombie-sweep: 2 zombie(s), "), out[-1])
        for name in ("z-ended", "z-arch"):
            self.assertIsNotNone(owners[name].wait(timeout=10), f"{name}'s owner must have been ended")
        for name in ("l-active", "u-none", "s-stale", "r-reused"):
            self.assertIsNone(owners[name].poll(), f"--kill must never reach {name}'s owner")
        self.assertIsNone(self.loop.poll(), "the heartbeat loops are never a target — they end the rows themselves")

    def test_kill_spares_an_owner_with_an_active_or_missing_row(self):
        owners = self.build_shared_owner_cases()
        r = self.sweep("--kill")
        out = r.stdout.splitlines()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pid = {k: v.pid for k, v in owners.items()}
        self.assertIn(f"KILLED ca-ended,cb-arch owner={pid['pair-zombie']} comm=node row=ended,archived", out)
        self.assertIn(f"KILLED ea-gone,eb-ended owner={pid['renamed-beside']} comm=node row=none,ended", out)
        for name in ("pair-zombie", "renamed-beside"):
            self.assertIsNotNone(owners[name].wait(timeout=10), f"{name}'s owner must have been ended")
        for name in ("pair-live", "pair-unreg", "stale-beside"):
            self.assertIsNone(owners[name].poll(), f"--kill must never reach {name}'s owner: a row of its is not ended")
        self.assertIsNone(self.loop.poll(), "the heartbeat loops are never a target")

    def test_min_ended_minutes_spares_a_row_that_ended_recently(self):
        # the scheduled run (heartbeat.sh) passes --min-ended-minutes 30: session-end marks the row ended before
        # the rest of the handoff close-out runs, so a session that ended minutes ago is spared (#726)
        from datetime import datetime, timedelta, timezone
        ago = lambda m: (datetime.now(timezone.utc) - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
        loop = self.loop.pid
        owners = {k: self.spawn("node") for k in ("young", "old", "unstamped")}
        for k, p in owners.items():
            self.log(k, k, p.pid, loop)
        self.row("young", "ended", ended_at=ago(5))
        self.row("old", "ended", ended_at=ago(120))
        self.row("unstamped", "ended")                               # ended before the field existed: long ago
        r = self.sweep("--kill", "--min-ended-minutes", "30")
        out = r.stdout.splitlines()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"RECENT young owner={owners['young'].pid} row=ended ended 5m ago (under 30m)", out)
        self.assertIn(f"KILLED old owner={owners['old'].pid} comm=node row=ended", out)
        self.assertIn(f"KILLED unstamped owner={owners['unstamped'].pid} comm=node row=ended", out)
        self.assertTrue(out[-1].startswith("zombie-sweep: 2 zombie(s), ") and out[-1].endswith(", 1 recent"), out[-1])
        self.assertIsNone(owners["young"].poll(), "a session that ended 5 minutes ago may still be closing out")
        for k in ("old", "unstamped"):
            self.assertIsNotNone(owners[k].wait(timeout=10), f"{k}'s owner must have been ended")
        r = self.sweep("--kill")                                     # without the flag (a manual run): no grace
        self.assertIn(f"KILLED young owner={owners['young'].pid} comm=node row=ended", r.stdout.splitlines())

    def test_min_ended_minutes_takes_a_whole_number_only(self):
        for bad in (["--min-ended-minutes"], ["--min-ended-minutes", "half"], ["--min-ended-minutes=-1"]):
            r = self.sweep(*bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("usage: zombie-sweep.sh", r.stderr)

    def test_kill_never_targets_the_scripts_own_ancestry(self):
        # a claude-shaped process (a script named `node`) whose own heartbeat log maps it to an ended row runs
        # the sweep with --kill itself: it is the sweep's parent, so it reads LIVE (this session) and survives
        runner = Path(self.tmp.name) / "run" / "node"
        runner.parent.mkdir()
        runner.write_text("#!/bin/sh\n"
                          "printf '2026-11-02 10:00 AM EST start name=self-run owner=%s interval=21600 session=none\\n' "
                          "\"$$\" > \"$1/heartbeat-self-run.log\"\n"
                          "printf '%s\\n' \"$2\" > \"$1/heartbeat-self-run.pid\"\n"
                          "bash \"$3\" --kill\n")
        runner.chmod(0o755)
        (self.ctx / "sessions" / "self-run.md").write_text("---\nsession: self-run\nsession_id: none\nstatus: ended\n---\n")
        r = subprocess.run([str(runner), str(self.logs), str(self.loop.pid), str(SWEEP)],
                           env=self.env, capture_output=True, text=True, timeout=60)
        out = r.stdout.splitlines()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(any(line.startswith("LIVE self-run owner=") and line.endswith("(this session)") for line in out), out)
        self.assertFalse(any(line.startswith("KILLED ") or line.startswith("ZOMBIE ") for line in out), out)
        self.assertEqual(out[-1], "zombie-sweep: 0 zombie(s), 1 live, 0 unregistered, 0 stale, 0 reused, 0 dead, 0 unmapped, 0 recent")


if __name__ == "__main__":
    unittest.main()
