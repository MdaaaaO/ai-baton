"""skills/session-register/heartbeat.sh (#155) — the two behaviours its own header promises without needing
a real 60s poll wait: a second start for the same NAME while the first's pidfile still names a live process
is a no-op (never a second detached copy racing the first), and once the owning `claude` process is gone the
detached loop writes the "ended" line and calls `make session-end` exactly once, then removes its pidfile —
proven by handing it an ALREADY-DEAD owner pid so the outer liveness loop's very first check fails and the
end path runs immediately (no real sleep needed; the loop's periodic-touch path sleeps in fixed 60s chunks
by design — the header's own "checks liveness every 60s" — so it is not economical to unit-test here). A
stub `make` on PATH stands in for the real context-db Makefile, so this never touches a live env store.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
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

    def env(self, **extra) -> dict:
        base = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "TMPDIR": str(self.tmphome),
                "CONTEXT_ROOT": str(self.tmp / "ctx")}
        base.update(extra)
        return base

    def pidfile_dir(self) -> Path:
        return self.tmphome / f"ai-baton-{os.getuid()}"

    def write_ps_stub(self, owner_pid: int) -> None:
        """Make the walk from this test process's own ancestry report the desktop app's shape —
        a bare version string as `comm` — one level up, for the real pid given (this lets a real,
        guaranteed-alive process stand in for "the owning claude process" without starting one)."""
        real_ps = shutil.which("ps")
        ps = self.bin / "ps"
        ps.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "-o" ] && [ "$2" = "comm=" ] && [ "$3" = "-p" ]; then\n'
            f'  if [ "$4" = "{owner_pid}" ]; then echo "2.1.281"; else echo "bash"; fi\n'
            "  exit 0\n"
            "fi\n"
            f'exec "{real_ps}" "$@"\n',
            encoding="utf-8",
        )
        ps.chmod(0o755)

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
                          "a no-op start must never touch the registry")

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
        # keyed by CLAUDE_CODE_SESSION_ID ("sess-demo" above), not by the NAME ("demo2") — two live
        # sessions that happen to share a NAME must never share a pidfile
        self.assertFalse((d / "heartbeat-sess-demo.pid").exists(), "the pidfile must be cleaned up on exit")

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

    def test_two_sessions_sharing_a_name_get_independent_pidfiles(self):
        # A naming accident — two live sessions registered under the same NAME — must not make the
        # second treat the first's pidfile as its own.
        alive = subprocess.Popen(["sleep", "30"])
        self.addCleanup(alive.wait)
        self.addCleanup(alive.terminate)
        d = self.pidfile_dir()
        d.mkdir(parents=True)
        (d / "heartbeat-sess-a.pid").write_text(str(alive.pid), encoding="utf-8")

        owner_pid = os.getpid()
        self.write_ps_stub(owner_pid)
        r = subprocess.run(["bash", str(SCRIPT), "shared-name", "focus", "3600"],
                            env=self.env(CLAUDE_CODE_SESSION_ID="sess-b"),
                            capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("already running", r.stdout)
        self.assertIn("started", r.stdout)
        pidfile_b = d / "heartbeat-sess-b.pid"
        self.assertTrue(self.wait_for(pidfile_b.exists), "session b must get its own pidfile")
        self.addCleanup(self.kill_detached, int(pidfile_b.read_text().strip()))
        self.assertEqual((d / "heartbeat-sess-a.pid").read_text(), str(alive.pid),
                          "session a's pidfile, keyed separately, must be left alone")

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


if __name__ == "__main__":
    unittest.main()
