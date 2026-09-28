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
import subprocess
import tempfile
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
        self.assertFalse((d / "heartbeat-demo2.pid").exists(), "the pidfile must be cleaned up on exit")


if __name__ == "__main__":
    unittest.main()
