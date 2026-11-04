"""lease.py (leases on serial resources, STOP files) and the PreToolUse hook's STOP branch, plus the two shell
wirings (zombie-sweep.sh, heartbeat.sh). Every store is a temp dir; session ids and lane names are assembled at run
time. Stdlib unittest. Run: make -C .claude/context-db test T=test_lease."""
from __future__ import annotations
import contextlib
import fcntl
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
LEASE = BIN / "lease.py"
ADAPTER = BIN / "ctx_adapter.py"
ZOMBIE = KIT / "skills" / "session-register" / "zombie-sweep.sh"
HEARTBEAT = KIT / "skills" / "session-register" / "heartbeat.sh"
sys.path.insert(0, str(BIN))
import lease as L  # noqa: E402


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.root = self.t / ".context"
        (self.root / "sessions").mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("WORKSPACE_", "BATON_", "CLAUDE_"))}
        self.env.update(CONTEXT_ROOT=str(self.root), KIT_SCRATCH=str(self.t / "scratch"))
        self.addCleanup(self.tmp.cleanup)
        self.lane = "w-" + uuid.uuid4().hex[:6]
        self.other = "o-" + uuid.uuid4().hex[:6]

    def run_(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(LEASE), *args], env={**self.env, **env}, capture_output=True,
                              text=True, cwd=self.t, timeout=60)

    def ok(self, *args: str) -> str:
        r = self.run_(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def refused(self, *args: str, rc: int = 1, **env: str) -> str:
        r = self.run_(*args, **env)
        self.assertEqual(r.returncode, rc, r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        return r.stderr

    def acquire(self, res: str, lane: str, pid: int | None = None, *extra: str) -> subprocess.CompletedProcess:
        return self.run_("acquire", res, "--lane", lane, "--pid", str(pid or os.getpid()), *extra)

    def lease_file(self, res: str) -> dict:
        return json.loads((self.root / "state" / "leases" / f"{res}.json").read_text())


class Leases(Base):
    def test_acquire_refuses_a_held_lease_and_names_the_holder(self):  # DW-1
        self.assertEqual(self.acquire("client", self.lane, None, "--ref", "T1").returncode, 0)
        r = self.acquire("client", self.other)
        self.assertEqual(r.returncode, 1)
        self.assertIn(self.lane, r.stderr)
        h = self.lease_file("client")["holders"][0]
        self.assertEqual((h["lane"], h["ref"], h["pid"], h["releaser"]), (self.lane, "T1", os.getpid(), "holder"))
        self.assertTrue(h["acquired"] and h["expires"])

    def test_the_same_lane_renews(self):
        self.acquire("client", self.lane)
        r = self.acquire("client", self.lane)
        self.assertIn("renewed", r.stdout)

    def test_acquire_grants_a_dead_pid_lease(self):  # DW-1
        self.acquire("client", self.lane, dead_pid())
        r = self.acquire("client", self.other)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dead pid", r.stdout)
        self.assertEqual(self.lease_file("client")["holders"][0]["lane"], self.other)

    def test_acquire_grants_an_expired_lease(self):
        self.acquire("client", self.lane, None, "--minutes", "0")
        self.assertEqual(self.acquire("client", self.other).returncode, 0)

    def test_release_honours_the_releaser_rule(self):  # DW-1
        self.acquire("client", self.lane, None, "--releaser", "orchestrator")
        self.refused("release", "client", "--as", "holder", rc=2)  # no lane known to the caller
        # the holder of an active lease always gives it back, whoever is named releaser (it enforces its own expiry)
        self.assertIn("released", self.ok("release", "client", "--lane", self.lane, "--as", "holder"))
        self.acquire("client", self.lane, None, "--releaser", "orchestrator")
        self.assertIn("release", self.run_("release", "client", "--lane", self.lane, "--as", "orchestrator").stdout)
        self.acquire("client", self.lane, None, "--releaser", "holder")
        self.assertIn("releaser is holder", self.refused("release", "client", "--lane", self.lane, "--as", "orchestrator"))
        self.ok("release", "client", "--as", "owner")  # the owner may always release
        self.assertFalse((self.root / "state" / "leases" / "client.json").exists())

    def test_the_holder_releases_its_own_and_not_a_peers(self):
        self.acquire("client", self.lane)
        self.assertIn("not", self.refused("release", "client", "--lane", self.lane, "--as", "orchestrator"))
        self.refused("release", "client", "--lane", self.other, "--as", "holder")
        self.assertEqual(self.run_("release", "client", "--as", "holder", "--lane", self.lane).returncode, 0)

    def test_resource_must_be_named_when_the_environment_lists_some(self):
        from unittest import mock
        with mock.patch.object(L.kit_profile, "get", lambda k, d=None: ["client"] if k == "swarm.resources" else d):
            L.check_resource("client")
            with self.assertRaises(L.Usage):
                L.check_resource("other")

    def test_bad_names_are_usage_errors(self):
        self.refused("acquire", "../x", "--lane", self.lane, "--pid", "1", rc=2)


RACER = (
    "import os, sys, time\n"
    f"sys.path.insert(0, {str(BIN)!r})\n"
    "import lease as L\n"
    "t = float(sys.argv[1])\n"
    "while time.time() < t:\n"
    "    pass\n"
    "sys.exit(L.main(sys.argv[2:]))\n"
)


class Race(Base):
    """N processes released by one deadline; each must fail without the per-resource lock, so a pass is not luck."""
    N = 8

    def race(self, argv_for) -> list[int]:
        at = time.time() + 1.5
        procs = [subprocess.Popen([sys.executable, "-c", RACER, str(at), *argv_for(i)], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(self.N)]
        rcs = []
        for p in procs:
            p.communicate(timeout=60)
            rcs.append(p.returncode)
        return rcs

    def test_racing_acquires_on_a_free_resource_grant_exactly_one(self):
        for rnd in range(3):
            res = f"free{rnd}"
            rcs = self.race(lambda i: ["acquire", res, "--lane", f"r{i}", "--pid", str(os.getpid())])
            self.assertEqual(rcs.count(0), 1, rcs)
            self.assertEqual(rcs.count(1), self.N - 1, rcs)
            self.assertEqual(len(self.lease_file(res)["holders"]), 1)

    def test_racing_quota_acquires_never_pass_the_units(self):
        for rnd in range(3):
            res = f"quota{rnd}"
            rcs = self.race(lambda i: ["acquire", res, "--lane", f"r{i}", "--pid", str(os.getpid()),
                                       "--capacity", "5", "--units", "2"])
            d = self.lease_file(res)
            self.assertEqual(rcs.count(0), 2, rcs)
            self.assertLessEqual(sum(h["units"] for h in d["holders"]), 5)
            self.assertEqual(len(d["holders"]), 2)

    def test_racing_takeovers_of_a_dead_pid_lease_give_one_winner(self):
        for rnd in range(3):
            res = f"dead{rnd}"
            self.assertEqual(self.acquire(res, "old", dead_pid()).returncode, 0)
            rcs = self.race(lambda i: ["acquire", res, "--lane", f"r{i}", "--pid", str(os.getpid())])
            self.assertEqual(rcs.count(0), 1, rcs)
            self.assertEqual(len(self.lease_file(res)["holders"]), 1)


class Locking(Base):
    def call(self, *argv: str) -> tuple[int, str]:
        err = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(L, "LOCK_WAIT", 0.3), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = L.main(list(argv))
        return rc, err.getvalue()

    def test_a_busy_lock_refuses_with_a_line_instead_of_hanging(self):
        self.assertEqual(self.acquire("db", self.lane).returncode, 0)
        lk = open(self.root / "state" / "leases" / "db.json.lock", "a")
        self.addCleanup(lk.close)
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        t0 = time.monotonic()
        rc, err = self.call("acquire", "db", "--lane", self.other, "--pid", str(os.getpid()))
        self.assertEqual(rc, 1)
        self.assertIn("lock busy", err)
        self.assertLess(time.monotonic() - t0, 5)
        rc, _ = self.call("sweep")  # the sweep skips the busy resource and still exits 0
        self.assertEqual(rc, 0)

    def test_a_stop_write_leaves_no_phantom_stop_behind(self):
        self.ok("stop", "epic-9", "--reason", "pause", "--as", "orchestrator")
        self.assertEqual([p.name for p in (self.root / "state" / "swarm").glob("STOP-*")], ["STOP-epic-9"])
        self.assertEqual(list(L.stops(self.root)), ["epic-9"])


class Review(Base):
    """The findings of the review of the first head: each test fails on it."""

    def row(self, lane: str, sid: str, epic: str, repos: str = "") -> None:
        extra = f"repos: {repos}\n" if repos else ""
        (self.root / "sessions" / f"{lane}.md").write_text(
            f"---\nsession: {lane}\nsession_id: {sid}\nstatus: active\nepic: {epic}\n{extra}---\n# x\n")

    def hook(self, sid: str, tool: str = "Write", **inp) -> dict | None:
        payload = json.dumps({"session_id": sid, "cwd": str(self.t), "tool_name": tool, "tool_input": inp})
        r = subprocess.run([sys.executable, str(ADAPTER), "hook", "pre-tool-use"], input=payload, text=True,
                           env={**self.env, "KIT_CTX": str(self.t / "none")}, capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def test_a_capacity_flag_never_shares_a_held_serial_lease(self):  # DW-1
        self.assertEqual(self.acquire("client", "a").returncode, 0)
        r = self.acquire("client", "b", None, "--capacity", "5")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual([h["lane"] for h in self.lease_file("client")["holders"]], ["a"])

    def test_the_config_capacity_wins_over_the_flag(self):
        with mock.patch.object(L.kit_profile, "get", lambda k, d=None: {"api": 2} if k == "swarm.units" else d):
            self.assertEqual(L.config_capacity("api"), 2)
        r = self.run_("acquire", "api", "--lane", "a", "--pid", str(os.getpid()), "--capacity", "9", "--units", "1")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.lease_file("api")["capacity"], 9)  # no swarm.units here: the flag names it

    def test_units_and_capacity_must_be_at_least_one(self):  # DW-4
        for flag in ("--units", "--capacity"):
            for v in ("0", "-100"):
                args = ["--capacity", "5", flag, v] if flag == "--units" else [flag, v, "--units", "1"]
                self.refused("acquire", "api", "--lane", "a", "--pid", str(os.getpid()), *args, rc=2)
        self.assertFalse((self.root / "state" / "leases" / "api.json").exists())

    def test_one_unreadable_lease_file_never_hides_the_rest(self):
        self.acquire("a", self.lane, dead_pid())
        d = self.root / "state" / "leases"
        (d / "aa-bad.json").write_text("{not json")
        (d / "ab-bad.json").write_text(json.dumps({"holders": ["x"]}))
        self.acquire("z", self.lane, dead_pid())
        out = self.ok("sweep")
        self.assertIn("freed a", out)
        self.assertIn("freed z", out)
        self.assertIn("aa-bad", out)
        self.assertIn("ab-bad", out)
        out = self.ok("expire", "--lane", self.lane)
        self.assertIn("skipped", out)
        self.assertNotIn("Traceback", self.run_("status").stderr)

    def test_a_stopped_worker_cannot_lift_or_write_its_own_stop(self):  # DW-5
        sid = str(uuid.uuid4())
        self.row(self.lane, sid, "674")
        self.ok("stop", "674", "--reason", "x")
        for cmd in ("python3 context-db/bin/lease.py go 674 --as owner", "python3 lease.py stop 674 --reason y"):
            self.assertEqual(self.hook(sid, "Bash", command=cmd)["permissionDecision"], "deny", cmd)

    def test_the_holder_releases_even_when_another_releaser_is_named(self):
        self.acquire("client", self.lane, None, "--releaser", "orchestrator")
        self.ok("release", "client", "--lane", self.lane, "--as", "holder")
        self.assertFalse((self.root / "state" / "leases" / "client.json").exists())

    def test_a_resource_under_stop_takes_no_new_holder(self):  # DW-3
        self.ok("stop", "client", "--reason", "budget hard stop")
        err = self.refused("acquire", "client", "--lane", "a", "--pid", str(os.getpid()))
        self.assertIn("budget hard stop", err)
        self.ok("go", "client")
        self.assertEqual(self.acquire("client", "a").returncode, 0)

    def test_a_parked_hold_is_listed_ended_by_its_releaser_and_a_resume_says_so(self):  # DW-6
        self.acquire("client", self.lane, None, "--ref", "#7", "--releaser", "orchestrator")
        self.ok("release", "client", "--lane", self.lane, "--hold")
        self.assertIn("PARKED", self.ok("status"))
        self.assertNotIn("no leases", self.ok("status"))
        self.assertIn("releaser is orchestrator", self.refused("release", "client", "--lane", self.lane, "--as", "holder"))
        out = self.ok("acquire", "client", "--lane", self.lane, "--pid", str(os.getpid()))
        self.assertIn("took over the parked terms", out)
        self.assertIn("#7", out)
        self.ok("release", "client", "--lane", self.lane, "--hold")
        self.assertIn("ended the parked hold", self.ok("release", "client", "--lane", self.lane, "--as", "orchestrator"))
        self.assertFalse((self.root / "state" / "leases" / "client.json").exists())
        self.acquire("client", self.other, None, "--releaser", "owner")
        self.ok("release", "client", "--lane", self.other, "--hold")
        self.ok("release", "client", "--lane", self.other, "--as", "owner")
        self.assertFalse((self.root / "state" / "leases" / "client.json").exists())

    def test_an_epic_stop_matches_the_repo_and_the_number(self):  # DW-3
        a, b, c = (str(uuid.uuid4()) for _ in range(3))
        self.row("one", a, "acme/widgets#674")
        self.row("two", b, "Other/repo#674")
        self.row("three", c, "674", "other-repo")
        self.ok("stop", "acme/widgets#674", "--reason", "x")
        self.assertEqual([p.name for p in (self.root / "state" / "swarm").glob("STOP-*")], ["STOP-acme+widgets=674"])
        self.assertEqual(self.hook(a)["permissionDecision"], "deny")
        self.assertIsNone(self.hook(b))
        self.assertIsNone(self.hook(c))
        self.ok("go", "acme/widgets#674")
        self.assertIsNone(self.hook(a))

    def test_a_plain_number_epic_reads_the_rows_repos(self):
        self.assertTrue(L.epic_matches("acme/widgets#674", "674", "acme/widgets"))
        self.assertFalse(L.epic_matches("acme/widgets#674", "674", "other-repo"))
        self.assertTrue(L.epic_matches("674", "#674", "x"))
        self.assertFalse(L.epic_matches("675", "#674", "x"))
        with mock.patch.object(L.kit_profile, "get", lambda k, d=None: ["acme/widgets"] if k == "tracker.repos" else d):
            self.assertTrue(L.epic_matches("674", "674", "acme/widgets"))
            self.assertFalse(L.epic_matches("674", "Other/repo#674", ""))

    def test_the_stop_text_says_what_is_blocked_today(self):
        out = self.ok("stop", "674", "--reason", "x")
        self.assertIn("file writes only", out)
        self.assertIn("Bash and MCP calls pass", out)
        self.assertNotIn("next work-tool call", out)

    def test_release_reason_is_printed(self):
        self.acquire("client", self.lane)
        self.assertIn('reason="done early"', self.ok("release", "client", "--lane", self.lane, "--reason", "done early"))

    def test_go_takes_the_stops_lock_and_names_the_owner_in_its_refusal(self):
        self.ok("stop", "674", "--reason", "x", "--as", "owner")
        err = self.refused("go", "674", "--as", "orchestrator")
        self.assertIn("only the owner lifts it", err)
        self.assertNotIn("owner or the owner", err)
        lk = open(self.root / "state" / "swarm" / "stops.lock", "a")
        self.addCleanup(lk.close)
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(L, "LOCK_WAIT", 0.3), \
                contextlib.redirect_stderr(io.StringIO()) as e:
            self.assertEqual(L.main(["go", "674", "--as", "owner"]), 1)
        self.assertIn("lock busy", e.getvalue())
        self.assertEqual([x.name for x in (self.root / "state" / "swarm").glob("STOP-*")], ["STOP" + "-674"])


class Quota(Base):
    def test_a_quota_lease_refuses_a_reservation_past_the_units(self):  # DW-4
        self.assertEqual(self.acquire("api", self.lane, None, "--capacity", "10", "--units", "6").returncode, 0)
        self.assertEqual(self.acquire("api", self.other, None, "--units", "4").returncode, 0)
        r = self.acquire("api", "third", None, "--units", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("quota", r.stderr)
        self.assertEqual(self.lease_file("api")["capacity"], 10)
        self.ok("release", "api", "--lane", self.lane, "--as", "owner")
        self.assertEqual(self.acquire("api", "third", None, "--units", "6").returncode, 0)

    def test_units_without_a_capacity_is_a_usage_error(self):
        self.assertEqual(self.acquire("api", self.lane, None, "--units", "2").returncode, 2)


class Sweep(Base):
    def test_sweep_frees_expired_and_dead_pid_leases(self):  # DW-2
        self.acquire("a", self.lane, dead_pid())
        self.acquire("b", self.lane, None, "--minutes", "0")
        self.acquire("c", self.lane)
        out = self.ok("sweep").splitlines()
        self.assertEqual(sorted(x.split(":")[0] for x in out), ["freed a", "freed b"])
        self.assertIn("dead pid", out[0] + out[1])
        self.assertEqual(L.resources(self.root), ["c"])

    def test_expire_frees_only_this_lanes_expired_leases_and_prints_release(self):
        self.acquire("a", self.lane, None, "--minutes", "0")
        self.acquire("b", self.other, None, "--minutes", "0")
        self.assertEqual(self.ok("expire", "--lane", self.lane).strip(), 'RELEASE a reason="lease expired"')
        self.assertEqual(L.resources(self.root), ["b"])

    def test_zombie_sweep_calls_it(self):  # DW-2
        self.assertIn("lease.py", ZOMBIE.read_text())
        self.acquire("a", self.lane, dead_pid())
        (self.root / "sessions").mkdir(exist_ok=True)

        def zs(*args):
            return subprocess.run(["bash", str(ZOMBIE), *args], env={**self.env, "TMPDIR": str(self.t)},
                                  capture_output=True, text=True, timeout=60)
        r = zs()  # list mode is a read: it names the lease and leaves it
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would free a", r.stdout)
        self.assertEqual(L.resources(self.root), ["a"])
        r = zs("--kill")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("freed a", r.stdout)
        self.assertEqual(L.resources(self.root), [])

    def test_heartbeat_frees_its_lanes_expired_lease(self):
        self.assertIn("lease.py", HEARTBEAT.read_text())
        self.assertIn("expire --lane", HEARTBEAT.read_text())
        self.ok("acquire", "a", "--lane", self.lane, "--pid", str(os.getpid()), "--minutes", "0")
        self.assertIn(f'RELEASE a reason="lease expired"', self.ok("expire", "--lane", self.lane))
        self.assertEqual(L.resources(self.root), [])


class Stop(Base):
    def setUp(self):
        super().setUp()
        self.sid = str(uuid.uuid4())
        (self.root / "sessions" / f"{self.lane}.md").write_text(
            f"---\nsession: {self.lane}\nsession_id: {self.sid}\nstatus: active\nepic: acme/widgets#674\n---\n# x\n")

    def hook(self, tool: str = "Bash", sid: str | None = None, **inp) -> dict | None:
        payload = json.dumps({"session_id": self.sid if sid is None else sid, "cwd": str(self.t), "tool_name": tool, "tool_input": inp})
        r = subprocess.run([sys.executable, str(ADAPTER), "hook", "pre-tool-use"], input=payload, text=True,
                           env={**self.env, "KIT_CTX": str(self.t / "none")}, capture_output=True, timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def test_a_stop_blocks_the_holding_lane_with_the_reason_and_go_lifts_it(self):  # DW-3
        self.acquire("client", self.lane)
        self.assertIsNone(self.hook(command="make ci"))
        self.ok("stop", "client", "--reason", "owner needs the client")
        out = self.hook(command="make ci")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("owner needs the client", out["permissionDecisionReason"])
        self.assertEqual(self.hook("Edit", file_path="x")["permissionDecision"], "deny")
        self.assertIsNone(self.hook(sid=str(uuid.uuid4()), command="make ci"))  # another session: untouched
        self.ok("go", "client")
        self.assertIsNone(self.hook(command="make ci"))

    def test_an_epic_stop_blocks_the_lane_working_that_epic(self):  # DW-3
        self.ok("stop", "674", "--reason", "budget hard stop")
        self.assertIn("budget hard stop", self.hook(command="ls")["permissionDecisionReason"])
        self.ok("stop", "other", "--reason", "x")
        self.ok("go", "674")
        self.assertIsNone(self.hook(command="ls"))  # STOP-other names a scope this lane neither holds nor works

    def test_a_stop_for_a_lease_the_lane_does_not_hold_leaves_it_alone(self):
        self.acquire("client", self.other)
        self.ok("stop", "client", "--reason", "x")
        self.assertIsNone(self.hook(command="ls"))

    def test_the_calls_the_contract_requires_pass_a_stop(self):  # DW-5
        self.ok("stop", "674", "--reason", "x")
        for cmd in ("python3 context-db/bin/swarm_msg.py send --worker w --line 'BLOCKED 1 stop owner ran=\"a; b\"'",
                    "python3 context-db/bin/swarm_msg.py send w 'BLOCKED 1 stop o ran=\"make $(x)\"'",
                    "python3 context-db/bin/swarm_msg.py send w \"BLOCKED 1 stop o ran=\\\"make \\$(x)\\\"\"",
                    "python3 $BATON/context-db/bin/swarm_msg.py drain w",
                    "python3 context-db/bin/swarm_msg.py drain w", "python3 swarm_msg.py ack w",
                    "python3 context-db/bin/lease.py release client --as holder"):
            self.assertIsNone(self.hook(command=cmd), cmd)
        self.assertIsNone(self.hook("TaskStop", task_id="t1"))
        for cmd in ("make ci", "python3 swarm_msg.py send w x; rm -rf y", "python3 swarm_msg.py send w x && ls",
                    "python3 swarm_msg.py drain w > out", "python3 swarm_msg.py drain $(ls)", "swarm_msg.py drain w\nls",
                    "python3 swarm_msg.py bogus", "python3 evil.py send",
                    "python3 context-db/bin/lease.py go 674", "python3 context-db/bin/lease.py stop x --reason y",
                    "python3 context-db/bin/lease.py status", "python3 swarm_msg.py pending w",
                    "LD_PRELOAD=/e.so python3 context-db/bin/lease.py release client", "FOO=1 python3 swarm_msg.py ack w",
                    "python3 swarm_msg.py send w \"x $(ls)\"", "python3 swarm_msg.py send w x`ls`"):
            self.assertEqual(self.hook(command=cmd)["permissionDecision"], "deny", cmd)
        self.assertEqual(self.hook("Write", file_path="a")["permissionDecision"], "deny")

    def test_only_the_next_work_call_is_blocked_not_every_tool_of_other_lanes(self):  # DW-6
        self.ok("stop", "674", "--reason", "x")
        self.assertIsNone(self.hook(sid="", command="ls"))  # no session id: no lane, no block

    def test_stop_prints_the_in_flight_rule_and_the_owner_keeps_its_stop(self):  # DW-4
        out = self.ok("stop", "674", "--reason", "x", "--as", "owner")
        self.assertIn("cannot beat a call already in flight", out)
        self.assertIn("kills a peer's pid", out.replace("nobody kills", "kills"))
        self.refused("go", "674", "--as", "orchestrator")
        self.refused("stop", "674", "--reason", "y", "--as", "orchestrator")
        self.ok("go", "674", "--as", "owner")
        self.refused("go", "674")  # already lifted

    def test_an_orchestrator_stop_lifts_by_the_orchestrator(self):
        self.ok("stop", "674", "--reason", "x")
        self.ok("go", "674", "--as", "orchestrator")

    def test_status_lists_leases_and_stops(self):
        self.acquire("client", self.lane)
        self.ok("stop", "client", "--reason", "r")
        out = self.ok("status")
        self.assertIn("client:", out)
        self.assertIn("STOP-client", out)


class Resume(Base):
    def test_a_hold_keeps_the_terms_and_acquire_serves_the_resume(self):  # DW-6
        self.acquire("api", self.lane, None, "--capacity", "5", "--units", "3", "--ref", "T9", "--releaser", "orchestrator")
        self.ok("release", "api", "--lane", self.lane, "--as", "orchestrator", "--hold")
        self.assertEqual(self.lease_file("api")["holders"], [])
        r = self.acquire("api", self.lane)  # no new assignment: the terms come back
        self.assertEqual(r.returncode, 0, r.stderr)
        h = self.lease_file("api")["holders"][0]
        self.assertEqual((h["ref"], h["units"], h["releaser"]), ("T9", 3, "orchestrator"))
        self.assertEqual(self.lease_file("api")["parked"], [])

    def test_a_resume_finds_the_lease_taken_and_names_the_holder(self):  # DW-6
        self.acquire("client", self.lane, None, "--ref", "T9")
        self.ok("release", "client", "--as", "holder", "--hold", "--lane", self.lane)
        self.assertEqual(self.acquire("client", self.other).returncode, 0)
        r = self.acquire("client", self.lane)
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"BLOCKED T9 lease {self.other}", r.stderr)


if __name__ == "__main__":
    unittest.main()
