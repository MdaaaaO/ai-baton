"""skills/_lib/portable.sh — the shared helpers (`iso_to_epoch`, `epoch_to_iso`, `with_lock`,
`detach`) that let a kit script run the same on GNU/Linux and macOS/BSD (bash 3.2, BSD `date`, no
`flock`/`setsid` on PATH), plus regression checks that the GNU-only constructs the helpers replace
(`mapfile`, `date … -d`, a bare `flock` with no fallback, `setsid` with no fallback, `sed -i -E`
with no backup suffix) are gone from the scripts that used to carry them. Stdlib unittest, no
network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
LIB = KIT / "skills" / "_lib" / "portable.sh"
SH = shutil.which("sh") or "/bin/sh"


def path_without(*names) -> str:
    """A PATH of symlinks to every executable on this host's PATH except `names` (e.g. no `flock`,
    no `setsid`) — the same technique test_sync_sh.py uses to exercise sync.sh's own mkdir fallback."""
    d = Path(tempfile.mkdtemp(prefix="kit-portable-nobin-"))
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


def run_sh(script: str, path: str | None = None, timeout: int = 20) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    return subprocess.run([SH, "-c", f'. "{LIB}"\n{script}'], capture_output=True, text=True, env=env, timeout=timeout)


class IsoEpoch(unittest.TestCase):
    def test_round_trip(self):
        r = run_sh('epoch_to_iso "$(iso_to_epoch "2026-09-19T12:34:56Z")"')
        self.assertEqual(r.stdout.strip(), "2026-09-19T12:34:56Z", r.stderr)

    def test_iso_to_epoch_matches_a_known_value(self):
        # 2026-01-01T00:00:00Z is a fixed, host-independent epoch — not derived from `date` itself,
        # so this catches iso_to_epoch returning the wrong value, not just failing to return one
        r = run_sh('iso_to_epoch "2026-01-01T00:00:00Z"')
        self.assertEqual(r.stdout.strip(), "1767225600", r.stderr)

    def test_unparsable_input_fails_closed_not_a_wrong_answer(self):
        r = run_sh('v=$(iso_to_epoch "not a date"); echo "rc=$? v=$v"')
        self.assertIn("v=", r.stdout)
        self.assertEqual(r.stdout.split("v=")[-1].strip(), "", r.stdout)  # nothing printed, never a bogus epoch


class WithLock(unittest.TestCase):
    def _second_call_is_busy(self, path: str | None):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".x.lock"
            first = subprocess.Popen([SH, "-c", f'. "{LIB}"\nwith_lock "{lock}" || exit 1\nsleep 2'],
                                     env={**os.environ, **({"PATH": path} if path else {})})
            try:
                import time
                time.sleep(0.3)  # let the first process take the lock
                second = run_sh(f'with_lock "{lock}"; echo "rc=$?"', path=path)
                self.assertIn("rc=1", second.stdout, second.stderr)
            finally:
                first.wait(timeout=10)

    def test_flock_path_second_caller_is_busy(self):
        if not shutil.which("flock"):
            self.skipTest("no flock on this host")
        self._second_call_is_busy(path=None)

    def test_mkdir_fallback_second_caller_is_busy(self):
        self._second_call_is_busy(path=path_without("flock"))

    def test_mkdir_fallback_releases_on_normal_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".x.lock"
            r = run_sh(f'with_lock "{lock}"; echo "rc=$?"', path=path_without("flock"))
            self.assertIn("rc=0", r.stdout, r.stderr)
            self.assertFalse(Path(f"{lock}.d").exists(), "the lock dir must not outlive the process")

    def test_mkdir_fallback_releases_on_a_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".x.lock"
            # a single long `sleep N` defers signal delivery in some shells until it returns; a loop of
            # short sleeps gives the shell a command boundary to run the pending trap at almost at once
            p = subprocess.Popen([SH, "-c", f'. "{LIB}"\nwith_lock "{lock}" || exit 1\n'
                                             'i=0; while [ $i -lt 200 ]; do sleep 0.1; i=$((i+1)); done'],
                                 env={**os.environ, "PATH": path_without("flock")})
            import time
            time.sleep(0.5)
            self.assertTrue(Path(f"{lock}.d").is_dir())
            p.terminate()
            p.wait(timeout=10)
            self.assertFalse(Path(f"{lock}.d").exists(), "TERM must still run the release trap")

    @staticmethod
    def _dead_pid() -> int:
        p = subprocess.Popen(["true"])
        p.wait()
        return p.pid

    def test_mkdir_fallback_reclaims_a_dead_owners_lock(self):
        # same shape and wording as sync.sh's own take_lockdir: a lock dir stamped with a pid that is
        # no longer running (on this host) is stale, not busy, and is taken over — never left to wedge
        # every later caller forever just because the process that made it never got to clean up.
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".x.lock"
            lockdir = Path(f"{lock}.d")
            lockdir.mkdir()
            hostname = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
            (lockdir / "owner").write_text(f"{self._dead_pid()} {hostname}\n")
            # rc=0 (not just the dir vanishing) is what proves it was reclaimed and re-taken, not just
            # deleted-and-abandoned; the EXIT trap releases it again the moment this one-liner ends,
            # so checking the dir's existence afterwards would not tell reclaim and no-op apart.
            r = run_sh(f'with_lock "{lock}"; echo "rc=$?"', path=path_without("flock"))
            self.assertIn("rc=0", r.stdout, r.stderr)
            self.assertIn("removed a stale lock", r.stderr)

    def test_mkdir_fallback_never_reclaims_a_live_owners_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".x.lock"
            lockdir = Path(f"{lock}.d")
            lockdir.mkdir()
            hostname = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
            (lockdir / "owner").write_text(f"{os.getpid()} {hostname}\n")
            r = run_sh(f'with_lock "{lock}"; echo "rc=$?"', path=path_without("flock"))
            self.assertIn("rc=1", r.stdout, r.stderr)
            self.assertNotIn("removed a stale lock", r.stderr)
            self.assertEqual((lockdir / "owner").read_text(), f"{os.getpid()} {hostname}\n",
                             "a live owner's lock must be left alone, not overwritten")


class Detach(unittest.TestCase):
    def _detach_gets_its_own_process_group(self, path: str | None):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "out.log"
            child = Path(tmp) / "child.sh"
            # the detached command records its own pid and process-group id; a real detach makes them
            # equal (it is its own group leader) and different from the caller shell's group
            child.write_text('#!/bin/sh\necho "pid=$$ pgid=$(ps -o pgid= -p $$ | tr -d \' \')"\n')
            child.chmod(0o755)
            r = run_sh(
                f'mypgid=$(ps -o pgid= -p $$ | tr -d " ")\n'
                f'detach "{log}" "{child}"\n'
                f'sleep 0.5\n'
                f'echo "caller_pgid=$mypgid"',
                path=path)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = log.read_text() if log.exists() else ""
            self.assertIn("pid=", out, f"stdout={r.stdout!r} log={out!r} stderr={r.stderr!r}")
            child_pid = out.split("pid=")[1].split()[0]
            child_pgid = out.split("pgid=")[1].split()[0]
            caller_pgid = r.stdout.split("caller_pgid=")[1].split()[0]
            self.assertEqual(child_pid, child_pgid, "a detached process must be its own group leader")
            self.assertNotEqual(child_pgid, caller_pgid, "a detached process must leave the caller's process group")

    def test_setsid_path(self):
        if not shutil.which("setsid"):
            self.skipTest("no setsid on this host")
        self._detach_gets_its_own_process_group(path=None)

    def test_no_setsid_fallback_path(self):
        self._detach_gets_its_own_process_group(path=path_without("setsid"))


class ScriptsNoLongerCarryGnuOnlyConstructs(unittest.TestCase):
    """Regression guards for the GNU-only constructs the portability fix named as evidence: each of
    these used to be true on main and would break bash 3.2 / BSD date / a host without flock or
    setsid; each must now be false."""

    def test_pr_scan_has_no_mapfile_and_uses_with_lock(self):
        text = (KIT / "skills" / "pr-scan" / "pr-scan.sh").read_text()
        self.assertNotIn("mapfile", text)  # bash 4+ only; macOS ships bash 3.2 as /bin/bash
        self.assertIn("with_lock", text)   # replaces the bare `flock -n 8` with no macOS fallback

    def test_pr_scan_never_calls_flock_without_a_guard(self):
        # every `flock` call sits behind a `command -v flock` check (or inside with_lock), so a host
        # without it still appends the surfaced rows instead of failing the whole ledger write
        import re
        text = (KIT / "skills" / "pr-scan" / "pr-scan.sh").read_text()
        calls = [m.start() for m in re.finditer(r"\(\s*flock\b", text)]
        for pos in calls:
            before = text[max(0, pos - 200):pos]
            self.assertIn("command -v flock", before, "a bare `( flock …` with no fallback for a host without flock")

    def test_pr_scan_has_no_gnu_only_relative_date(self):
        text = (KIT / "skills" / "pr-scan" / "pr-scan.sh").read_text()
        self.assertNotIn("date -u -d", text)  # BSD date has no -d

    def test_pr_watch_has_no_gnu_only_date_dash_d(self):
        text = (KIT / "skills" / "pr-watch" / "pr-watch.sh").read_text()
        self.assertNotIn('date -d "', text)
        self.assertIn("iso_to_epoch", text)

    def test_heartbeat_detaches_portably_and_avoids_a_shared_tmp_path(self):
        text = (KIT / "skills" / "session-register" / "heartbeat.sh").read_text()
        self.assertNotIn("setsid nohup bash", text)  # no macOS fallback in the old form
        self.assertIn("detach ", text)
        self.assertNotIn("LOG=/tmp/heartbeat-", text)   # a bare /tmp path is shared between every user
        self.assertNotIn("PIDFILE=/tmp/heartbeat-", text)

    def test_alerts_sweep_state_update_has_no_bare_sed_dash_i(self):
        text = (KIT / "skills" / "alerts-sweep" / "SKILL.md").read_text()
        self.assertNotIn("sed -i -E", text)  # BSD sed's -i needs a backup-suffix argument


if __name__ == "__main__":
    unittest.main()
