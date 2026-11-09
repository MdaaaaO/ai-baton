"""skills/session-register/heartbeat.sh (#155) — the two behaviours its own header promises without needing
a real 60s poll wait: a second start for the same NAME while the first's pidfile still names a live process
is a no-op (never a second detached copy racing the first), and once the owning `claude` process is gone the
detached loop writes the "ended" line and calls `make session-end` exactly once, then removes its pidfile —
proven by handing it an ALREADY-DEAD owner pid so the outer liveness loop's very first check fails and the
end path runs immediately (no real sleep needed; the loop's periodic-touch path sleeps in fixed 60s chunks
by design — the header's own "checks liveness every 60s" — so it is not economical to unit-test here). Each
touch and the final session-end are followed by `make mission-build` (the local mission-control page follows
the registry): in that order, never on a no-op start, and a failing build never stops the beat. A
stub `make` on PATH stands in for the real context-db Makefile, so this never touches a live env store.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SCRIPT = KIT / "skills" / "session-register" / "heartbeat.sh"


@unittest.skipUnless(shutil.which("bash") and shutil.which("sh"), "bash and sh needed")
class Heartbeat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-heartbeat-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.make_log = self.tmp / "make.log"
        make = self.bin / "make"
        make.write_text(f'#!/bin/bash\necho "$*" >> {self.make_log}\nexit 0\n', encoding="utf-8")
        make.chmod(0o755)
        self.tmphome = self.tmp / "tmphome"
        self.tmphome.mkdir()

    CALLED = ("session-touch", "session-end", "mission-build")

    def called(self) -> list:
        """The make targets the stub saw, in call order — the stub logs `$*`, whose path and variable words
        are not what these tests are about."""
        if not self.make_log.exists():
            return []
        return [next((w for w in line.split() if w in self.CALLED), "?") for line in self.make_log.read_text().splitlines()]

    def make_fails_on(self, target: str) -> None:
        make = self.bin / "make"
        make.write_text(f'#!/bin/bash\necho "$*" >> {self.make_log}\ncase "$*" in *{target}*) exit 1;; esac\nexit 0\n',
                         encoding="utf-8")

    def env(self, **extra) -> dict:
        base = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "TMPDIR": str(self.tmphome),
                "CONTEXT_ROOT": str(self.tmp / "ctx")}
        base.update(extra)
        return base

    def pidfile_dir(self) -> Path:
        return self.tmphome / f"ai-baton-{os.getuid()}"

    def write_ps_stub(self, owner_pid: int, comm: str = "2.1.281", adopt: bool = False) -> None:
        """Make the owner walk resolve to the real pid given, reported with `comm` (default: the desktop
        app's shape, a bare version string) — a real, guaranteed-alive process stands in for "the owning
        claude process" without starting one. `adopt` also answers every parent lookup with that pid, so
        a process outside this test's own ancestry (one `sleep` per simulated session) can be the owner."""
        real_ps = shutil.which("ps")
        ps = self.bin / "ps"
        parent = (
            'if [ "$1" = "-o" ] && [ "$2" = "ppid=" ] && [ "$3" = "-p" ]; then\n'
            f'  echo "{owner_pid}"; exit 0\n'
            "fi\n"
        ) if adopt else ""
        ps.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "-o" ] && [ "$2" = "comm=" ] && [ "$3" = "-p" ]; then\n'
            f'  if [ "$4" = "{owner_pid}" ]; then echo "{comm}"; else echo "bash"; fi\n'
            "  exit 0\n"
            "fi\n"
            + parent +
            f'exec "{real_ps}" "$@"\n',
            encoding="utf-8",
        )
        ps.chmod(0o755)

    def owner(self) -> subprocess.Popen:
        """A live process that stands in for one session's owning `claude` process."""
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        return proc

    def kill_loops(self) -> None:
        """Stop every detached loop that left a pidfile in this test's own tmp dir."""
        for pf in self.pidfile_dir().glob("heartbeat-*.pid"):
            try:
                self.kill_detached(int(pf.read_text().strip()))
            except (OSError, ValueError):
                pass

    def start(self, name: str, session_id: str = "") -> subprocess.CompletedProcess:
        """Run the script's start path the way a session's Bash tool does, then wait until the loop it
        detached has written its pidfile and its start line (the next start reads both)."""
        d = self.pidfile_dir()
        before = set(d.glob("heartbeat-*.pid")) if d.exists() else set()
        extra = {"CLAUDE_CODE_SESSION_ID": session_id} if session_id else {}
        r = subprocess.run(["bash", str(SCRIPT), name, "focus", "3600"], env=self.env(**extra),
                            capture_output=True, text=True, timeout=15)
        if " started " in r.stdout:
            def up() -> bool:
                new = set(d.glob("heartbeat-*.pid")) - before
                logs = [p.with_suffix(".log") for p in new]
                return bool(logs) and all(log.exists() and " start name=" in log.read_text() for log in logs)
            self.assertTrue(self.wait_for(up), "the detached loop should have written its pidfile and start line")
        return r

    def kill_detached(self, pid: int) -> None:
        """Best-effort teardown for a setsid-detached loop a test started — a probe must never
        leave a real heartbeat polling after the test that started it ends."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, OSError):
                    return
        time.sleep(0.1)

    def wait_for(self, predicate, seconds: float = 5.0) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()

    def test_a_second_start_while_the_first_still_runs_is_a_no_op(self):
        alive = subprocess.Popen(["sleep", "30"])
        self.addCleanup(alive.wait)
        self.addCleanup(alive.terminate)
        d = self.pidfile_dir()
        d.mkdir(parents=True)
        (d / "heartbeat-demo.pid").write_text(str(alive.pid), encoding="utf-8")
        r = subprocess.run(["bash", str(SCRIPT), "demo", "focus", "1"], env=self.env(),
                            capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already running", r.stdout)
        self.assertEqual(self.make_log.read_text() if self.make_log.exists() else "", "",
                          "a no-op start must never touch the registry nor rebuild the page")

    def test_an_already_dead_owner_ends_the_row_and_removes_the_pidfile_without_touching_first(self):
        dead = subprocess.Popen(["sh", "-c", "exit 0"])
        dead.wait()
        d = self.pidfile_dir()
        d.mkdir(parents=True)
        env = self.env(HEARTBEAT_DETACHED="1", CLAUDE_PID=str(dead.pid), CLAUDE_CODE_SESSION_ID="sess-demo")
        r = subprocess.run(["bash", str(SCRIPT), "demo2", "focus", "3600"], env=env,
                            capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("gone — ending demo2", r.stdout)
        calls = self.make_log.read_text()
        self.assertIn("session-end", calls)
        self.assertNotIn("session-touch", calls, "an owner already dead at entry must never touch first")
        self.assertEqual(self.called(), ["session-end", "mission-build"],
                          "the row is ended first, then the page is rebuilt so it shows the ended row")
        self.assertEqual(list(d.glob("heartbeat-*.pid")), [], "the pidfile must be cleaned up on exit")

    def test_a_failing_build_on_the_end_path_still_ends_the_row_and_removes_the_pidfile(self):
        self.make_fails_on("mission-build")
        dead = subprocess.Popen(["sh", "-c", "exit 0"])
        dead.wait()
        d = self.pidfile_dir()
        d.mkdir(parents=True)
        env = self.env(HEARTBEAT_DETACHED="1", CLAUDE_PID=str(dead.pid), CLAUDE_CODE_SESSION_ID="sess-demo")
        r = subprocess.run(["bash", str(SCRIPT), "demo4", "focus", "3600"], env=env,
                            capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.called(), ["session-end", "mission-build"])
        self.assertEqual(list(d.glob("heartbeat-*.pid")), [], "a failed build must not leave the pidfile behind")

    def test_owner_walk_matches_a_bare_version_string_comm(self):
        owner_pid = os.getpid()  # this test process: guaranteed alive for as long as the test runs
        self.write_ps_stub(owner_pid)
        r = subprocess.run(["bash", str(SCRIPT), "verstr", "focus", "3600"], env=self.env(),
                            capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("started", r.stdout)
        self.assertIn(f"owner claude pid {owner_pid}", r.stdout,
                      "a version-string comm (the desktop app's shape) must resolve as the owner")
        pidfile = self.pidfile_dir() / "heartbeat-verstr.pid"
        self.assertTrue(self.wait_for(pidfile.exists), "the detached copy should have written its pidfile")
        self.addCleanup(self.kill_detached, int(pidfile.read_text().strip()))

    def test_two_sessions_sharing_a_name_each_get_a_running_heartbeat(self):
        # A naming accident — two live sessions registered under the same NAME, each with its own
        # owner process and session id. The second start must not read the first loop as its own.
        self.addCleanup(self.kill_loops)
        owner_a, owner_b = self.owner(), self.owner()
        self.write_ps_stub(owner_a.pid, comm="claude", adopt=True)
        ra = self.start("shared-name", "sess-a")
        self.assertIn(" started ", ra.stdout, ra.stdout + ra.stderr)
        self.write_ps_stub(owner_b.pid, comm="claude", adopt=True)
        rb = self.start("shared-name", "sess-b")
        self.assertEqual(rb.returncode, 0, rb.stdout + rb.stderr)
        self.assertNotIn("already running", rb.stdout)
        self.assertIn(" started ", rb.stdout)
        d = self.pidfile_dir()
        pid_a = (d / "heartbeat-sess-a.pid").read_text().strip()
        pid_b = (d / "heartbeat-sess-b.pid").read_text().strip()
        self.assertNotEqual(pid_a, pid_b, "two sessions, two loops")
        for pid in (pid_a, pid_b):
            os.kill(int(pid), 0)  # both loops are alive (raises when one is not)

    def test_a_second_start_by_the_same_owner_under_another_key_is_a_no_op(self):
        # The same session starts again after its key changed: the first loop runs under the bare name
        # (no session id yet, the way an older kit keyed every loop), the second start carries an id.
        self.addCleanup(self.kill_loops)
        owner = self.owner()
        self.write_ps_stub(owner.pid, comm="claude", adopt=True)
        first = self.start("upgraded")
        self.assertIn(" started ", first.stdout, first.stdout + first.stderr)
        again = self.start("upgraded", "sess-new")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("already running", again.stdout)
        d = self.pidfile_dir()
        self.assertEqual(sorted(p.name for p in d.glob("heartbeat-*.pid")), ["heartbeat-upgraded.pid"],
                         "the same owner must never get a second loop for the same name")

    def test_a_loop_of_the_same_owner_for_another_name_does_not_block_a_start(self):
        self.addCleanup(self.kill_loops)
        owner = self.owner()
        self.write_ps_stub(owner.pid, comm="claude", adopt=True)
        self.assertIn(" started ", self.start("first-name").stdout)
        r = self.start("second-name", "sess-x")
        self.assertIn(" started ", r.stdout, r.stdout + r.stderr)

    def test_first_touch_passes_working_if_empty_not_working(self):
        # The short positional focus argument must reach session.py as WORKING_IF_EMPTY, never as
        # WORKING — only a blank registered working_on may be filled by it, never overwritten.
        owner = subprocess.Popen(["sleep", "30"])
        env = self.env(HEARTBEAT_DETACHED="1", CLAUDE_PID=str(owner.pid), CLAUDE_CODE_SESSION_ID="sess-wie")
        proc = subprocess.Popen(["bash", str(SCRIPT), "demo3", "short focus", "3600"], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        self.addCleanup(owner.wait)
        self.addCleanup(owner.terminate)
        self.assertTrue(self.wait_for(lambda: self.make_log.exists() and bool(self.make_log.read_text())),
                         "the first touch should have run by now")
        calls = self.make_log.read_text()
        self.assertIn("WORKING_IF_EMPTY=short focus", calls)
        self.assertNotIn("WORKING=short focus", calls)


    def run_first_tick(self, name: str, sid: str):
        """Start the detached loop body with a live owner and return its Popen once the first tick's calls are logged
        (the stub make records them as they happen); the loop then sits in its 60 s liveness poll."""
        owner = subprocess.Popen(["sleep", "30"])
        env = self.env(HEARTBEAT_DETACHED="1", CLAUDE_PID=str(owner.pid), CLAUDE_CODE_SESSION_ID=sid)
        proc = subprocess.Popen(["bash", str(SCRIPT), name, "focus", "3600"], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        self.addCleanup(owner.wait)
        self.addCleanup(owner.terminate)
        self.assertTrue(self.wait_for(lambda: "mission-build" in self.called()),
                         "the first tick should have rebuilt the page by now")
        return proc

    def test_a_tick_rebuilds_the_page_right_after_the_touch(self):
        self.run_first_tick("tick", "sess-tick")
        self.assertEqual(self.called(), ["session-touch", "mission-build"],
                          "one touch, then one local rebuild; nothing is published and nothing else runs")

    def test_a_failing_build_never_stops_the_beat(self):
        self.make_fails_on("mission-build")
        proc = self.run_first_tick("tick-fail", "sess-fail")
        time.sleep(0.3)  # a loop that died on the failed build would have exited by now
        self.assertIsNone(proc.poll(), "the heartbeat must keep running after a failed build")
        self.assertEqual(self.called(), ["session-touch", "mission-build"])


@unittest.skipUnless(shutil.which("bash") and shutil.which("sh"), "bash and sh needed")
class ScheduledSweep(unittest.TestCase):
    """The minute tick runs `zombie-sweep.sh --kill` when the machine-wide stamp is missing or older than
    `session.sweep_hours`. A stub `sleep` ends the owner on its first call, so the inner loop runs exactly one
    tick; a stub sweep script (HEARTBEAT_SWEEP_SCRIPT) counts its runs. The fake clock is the stamp's content (an
    epoch). The sweep's own ancestry guard (never kill the heartbeat that runs it) is proven by
    test_zombie_sweep's test_kill_never_targets_the_scripts_own_ancestry."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-heartbeat-sweep."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "make").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        (self.bin / "make").chmod(0o755)
        self.tmphome = self.tmp / "tmphome"
        self.tmphome.mkdir()
        self.runs = self.tmp / "sweep-runs"
        self.sweep = self.tmp / "sweep.sh"
        self.set_sweep('echo "ZOMBIE demo owner=1"; echo "zombie-sweep: 1 zombie(s)"')
        self.stamp = self.tmphome / f"ai-baton-{os.getuid()}" / "zombie-sweep.stamp"
        self.stamp.parent.mkdir(parents=True)

    def set_sweep(self, body: str, pause: str = "") -> None:
        self.sweep.write_text(f'#!/bin/bash\necho run >> {self.runs}\n{pause}\n{body}\n', encoding="utf-8")

    def config(self, value) -> None:
        d = self.tmp / "ctx" / "reference" / "env"
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps({"session": {"sweep_hours": value}}), encoding="utf-8")

    def count(self) -> int:
        return len(self.runs.read_text().split()) if self.runs.exists() else 0

    def tick(self, name: str = "sw") -> subprocess.Popen:
        """Start one detached-loop body with a live owner whose `sleep` stub ends it after one tick."""
        # the owner is a grandchild a shell reaps, so a kill leaves no zombie that `kill -0` would still see
        holder = subprocess.Popen(["bash", "-c", "sleep 30 & echo $!; wait"], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        owner_pid = int(holder.stdout.readline())
        self.addCleanup(lambda: subprocess.run(["kill", str(owner_pid)], capture_output=True))
        mybin = self.tmp / f"bin-{owner_pid}"
        mybin.mkdir()
        shutil.copy(self.bin / "make", mybin / "make")
        sleep = mybin / "sleep"
        sleep.write_text(f"#!/bin/bash\nkill {owner_pid} 2>/dev/null\nwhile kill -0 {owner_pid} 2>/dev/null; do /bin/sleep 0.05; done\nexit 0\n",
                         encoding="utf-8")
        sleep.chmod(0o755)
        env = {"PATH": f"{mybin}{os.pathsep}{os.environ['PATH']}", "TMPDIR": str(self.tmphome),
               "CONTEXT_ROOT": str(self.tmp / "ctx"), "HEARTBEAT_DETACHED": "1", "CLAUDE_PID": str(owner_pid),
               "CLAUDE_CODE_SESSION_ID": f"sess-{name}", "HEARTBEAT_SWEEP_SCRIPT": str(self.sweep)}
        return subprocess.Popen(["bash", str(SCRIPT), name, "focus", "3600"], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)

    def run_tick(self, name: str = "sw") -> str:
        out = self.tick(name).communicate(timeout=30)[0]
        return out

    def test_missing_stamp_sweeps_and_prefixes_the_lines(self):
        out = self.run_tick()
        self.assertEqual(self.count(), 1)
        self.assertIn("sweep: ZOMBIE demo owner=1", out.splitlines())
        self.assertIn("sweep: zombie-sweep: 1 zombie(s)", out.splitlines())
        self.assertLessEqual(time.time() - int(self.stamp.read_text()), 60, "the stamp is refreshed")

    def test_fresh_stamp_skips_and_an_old_one_sweeps(self):
        self.stamp.write_text(str(int(time.time()) - 3600), encoding="utf-8")
        self.run_tick()
        self.assertEqual(self.count(), 0, "swept 1h ago, due in 24h")
        self.stamp.write_text(str(int(time.time()) - 25 * 3600), encoding="utf-8")
        self.run_tick()
        self.assertEqual(self.count(), 1)

    def test_the_interval_is_session_sweep_hours_and_null_turns_it_off(self):
        self.config(2)
        self.stamp.write_text(str(int(time.time()) - 3 * 3600), encoding="utf-8")
        self.run_tick()
        self.assertEqual(self.count(), 1, "3h old, due every 2h")
        self.config(None)
        self.stamp.write_text("0", encoding="utf-8")
        self.run_tick()
        self.assertEqual(self.count(), 1, "null: never")

    def test_two_loops_due_together_sweep_once(self):
        self.set_sweep("echo swept", pause="sleep 1")  # the real sleep, not the stub: nothing on this script's PATH
        self.sweep.write_text(self.sweep.read_text().replace("sleep 1", f"/bin/sleep 1"), encoding="utf-8")
        a, b = self.tick("one"), self.tick("two")
        outs = [a.communicate(timeout=30)[0], b.communicate(timeout=30)[0]]
        self.assertEqual(self.count(), 1)
        self.assertEqual(sum("sweep: swept" in o for o in outs), 1)

    def test_a_failing_sweep_never_stops_the_heartbeat(self):
        self.set_sweep('echo "boom"; exit 7')
        out = self.run_tick()
        self.assertIn("sweep: boom", out.splitlines())
        self.assertIn("gone — ending sw", out, "the loop still ends its row after a failed sweep")


if __name__ == "__main__":
    unittest.main()
