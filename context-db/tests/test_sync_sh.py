"""sync.sh + sync-check.sh against a temp clone and a bare origin: the .sync-status states (pending, ok, offline,
error, lock-busy skipped), the fetch timeout with and without a `timeout` binary, sync.log rotation and the sha line
only when HEAD moved, and which states sync-check warns about (#42). Stdlib unittest, no network: a hung or
unreachable origin is faked with `remote.origin.uploadpack`. Run: make -C .claude/context-db test."""
from __future__ import annotations
import fcntl
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SH = shutil.which("sh") or "/bin/sh"


def _env(home: Path, path: str | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower() and not k.startswith("GIT_")}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    if path is not None:
        env["PATH"] = path
    return env


class SyncSh(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-sync-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = _env(self.tmp)
        self.origin = self.tmp / "origin.git"
        seed = self.tmp / "seed"
        self.git("init", "-q", "--bare", str(self.origin), cwd=self.tmp)
        self.git("-c", "init.defaultBranch=main", "init", "-q", str(seed), cwd=self.tmp)
        shutil.copy(KIT / "sync.sh", seed / "sync.sh")
        shutil.copy(KIT / "sync-check.sh", seed / "sync-check.sh")
        (seed / ".gitignore").write_text(".sync.lock\n.sync.lock.d/\n.sync-status\nsync.log\n")
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-qm", "seed", cwd=seed)
        self.git("push", "-q", str(self.origin), "HEAD:main", cwd=seed)
        self.seed = seed
        self.kit = self.tmp / "kit"
        self.git("clone", "-q", "-b", "main", str(self.origin), str(self.kit), cwd=self.tmp)
        self.status_file = self.kit / ".sync-status"
        self.log_file = self.kit / "sync.log"

    # ── helpers ──
    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True, timeout=60)

    def sync(self, env=None, timeout=60):
        return subprocess.run([SH, str(self.kit / "sync.sh")], env=env or self.env, capture_output=True,
                              text=True, timeout=timeout)

    def check(self) -> str:
        r = subprocess.run([SH, str(self.kit / "sync-check.sh")], env=self.env, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0)
        return r.stderr

    def status(self) -> list[str]:
        return self.status_file.read_text().split()

    def upload_pack(self, body: str):
        script = self.tmp / "upload-pack"
        script.write_text("#!/bin/sh\n" + body + "\n")
        script.chmod(0o755)
        self.git("config", "remote.origin.uploadpack", str(script), cwd=self.kit)

    def origin_commit(self, name="f"):
        (self.seed / name).write_text(name)
        self.git("add", name, cwd=self.seed)
        self.git("commit", "-qm", name, cwd=self.seed)
        self.git("push", "-q", str(self.origin), "HEAD:main", cwd=self.seed)

    def path_without(self, *names) -> str:
        """A PATH of symlinks to every executable on PATH except `names` (e.g. no `timeout`, no `flock`)."""
        d = self.tmp / ("bin-" + "-".join(names))
        d.mkdir()
        for p in os.environ.get("PATH", "").split(os.pathsep):
            if not os.path.isdir(p):
                continue
            for e in os.listdir(p):
                if e in names or (d / e).exists():
                    continue
                f = os.path.join(p, e)
                if os.path.isfile(f) and os.access(f, os.X_OK):
                    (d / e).symlink_to(f)
        return str(d)

    def backdate(self, secs: int):
        """Move the epoch (field 3) of a pending/offline status `secs` into the past."""
        f = self.status()
        f[2] = str(int(f[2]) - secs)
        self.status_file.write_text(" ".join(f) + "\n")

    # ── ok, sha line, rotation ──
    def test_ok_logs_sha_only_when_head_moved(self):
        self.sync()
        self.assertEqual(self.status()[1], "ok")
        self.assertNotIn("main at", self.log_file.read_text() if self.log_file.exists() else "")
        self.origin_commit()
        self.sync()
        self.assertEqual(self.status()[1], "ok")
        self.assertEqual(self.log_file.read_text().count("kit: main at"), 1)
        self.sync()
        self.assertEqual(self.log_file.read_text().count("kit: main at"), 1, "in step: no second sha line")
        self.assertNotIn("WARN kit sync: last sync", self.check())

    def test_log_rotated(self):
        self.log_file.write_text("".join(f"old {i}\n" for i in range(500)))
        self.sync()
        lines = self.log_file.read_text().splitlines()
        self.assertLessEqual(len(lines), 210)
        self.assertNotIn("old 0", lines)
        self.assertIn("old 499", lines)
        self.assertEqual(self.status()[1], "ok", "the trim leaves no untracked file behind")

    def test_not_a_repo_takes_no_lock(self):
        shutil.rmtree(self.kit / ".git")
        self.sync()
        self.assertFalse(self.status_file.exists())
        self.assertFalse((self.kit / ".sync.lock").exists())
        self.assertIn("not a git repo", self.log_file.read_text())

    # ── pending → killed ──
    def test_pending_survives_a_kill_and_warns_once_stale(self):
        self.status_file.write_text("2026-01-01T00:00:00Z ok kit@abc\n")
        self.upload_pack("exec sleep 30 2>/dev/null")
        p = subprocess.Popen([SH, str(self.kit / "sync.sh")], env=self.env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 20
            while time.time() < deadline and self.status()[1] != "pending":
                time.sleep(0.1)
        finally:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(10)
        self.assertEqual(self.status()[1], "pending", "the killed run leaves pending, not the previous ok")
        self.assertNotIn("never finished", self.check(), "a fresh pending may still be running")
        self.backdate(600)
        self.assertIn("never finished", self.check())

    # ── timeout ──
    def _timed_out(self, env):
        self.upload_pack("exec sleep 30 2>/dev/null")
        env = dict(env, SYNC_FETCH_TIMEOUT="2")
        t = time.time()
        self.sync(env=env, timeout=40)
        self.assertLess(time.time() - t, 25)
        st = self.status_file.read_text()
        self.assertIn(" error ", st)
        self.assertIn("timed out", st)
        self.assertIn("failed", self.check())

    def test_fetch_timeout(self):
        if not (shutil.which("timeout") or shutil.which("gtimeout")):
            self.skipTest("no timeout binary; the watchdog test covers this host")
        self._timed_out(self.env)

    def test_fetch_timeout_watchdog_without_timeout_binary(self):
        self._timed_out(_env(self.tmp, self.path_without("timeout", "gtimeout")))

    # ── lock busy ──
    def test_lock_busy_is_skipped_and_keeps_status(self):
        if not shutil.which("flock"):
            self.skipTest("no flock on this host")
        self.status_file.write_text("2026-01-01T00:00:00Z ok kit@abc\n")
        with open(self.kit / ".sync.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            r = self.sync(env=dict(self.env, SYNC_LOCK_WAIT="1"))
        self.assertEqual(self.status_file.read_text(), "2026-01-01T00:00:00Z ok kit@abc\n")
        self.assertIn("skipped: lock busy", self.log_file.read_text())
        self.assertIn("skipped", r.stderr)
        self.assertEqual(self.check().count("last sync"), 0)

    def test_lock_busy_mkdir_fallback(self):
        self.status_file.write_text("2026-01-01T00:00:00Z pending 1 x\n")
        (self.kit / ".sync.lock.d").mkdir()
        self.sync(env=_env(self.tmp, self.path_without("flock")))
        self.assertEqual(self.status_file.read_text(), "2026-01-01T00:00:00Z pending 1 x\n")
        self.assertIn("skipped: lock busy", self.log_file.read_text())
        self.assertTrue((self.kit / ".sync.lock.d").is_dir(), "another run's lock dir is left alone")

    def test_mkdir_lock_released(self):
        self.sync(env=_env(self.tmp, self.path_without("flock")))
        self.assertEqual(self.status()[1], "ok")
        self.assertFalse((self.kit / ".sync.lock.d").exists())

    # ── offline ──
    def test_offline_streak_warns_after_days(self):
        self.upload_pack("echo 'ssh: Could not resolve hostname github.com: nodename nor servname provided' >&2; exit 128")
        self.sync()
        st = self.status()
        self.assertEqual(st[1], "offline")
        self.assertTrue(st[2].isdigit())
        self.assertEqual(self.check().count("unreachable"), 0, "a day offline is silent")
        self.backdate(4 * 86400)
        first = self.status()[2]
        self.sync()
        self.assertEqual(self.status()[2], first, "the streak keeps its first epoch")
        self.assertIn("unreachable for 4 day(s)", self.check())
        self.git("config", "--unset", "remote.origin.uploadpack", cwd=self.kit)
        self.sync()
        self.assertEqual(self.status()[1], "ok")
        self.assertNotIn("unreachable", self.check())

    def test_other_fetch_failure_is_error(self):
        self.upload_pack("echo 'ERROR: Repository not found.' >&2; exit 128")
        self.sync()
        self.assertEqual(self.status()[1], "error")
        self.assertIn("fetch failed", self.check())

    # ── refusals still error ──
    def test_dirty_is_error(self):
        (self.kit / "stray").write_text("x")
        self.sync()
        self.assertEqual(self.status()[1], "error")
        self.assertIn("uncommitted", self.check())


if __name__ == "__main__":
    unittest.main()
