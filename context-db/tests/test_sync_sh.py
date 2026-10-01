"""sync.sh + sync-check.sh against a temp clone and a bare origin: the .sync-status states (pending, ok, held,
offline, error, lock-busy skipped), the fetch timeout with and without a `timeout` binary, sync.log rotation and
the sha line only when HEAD moved, the release-tag channel (tag selection, the held preview, --accept, the
`kit.channel main` opt-out), the manifest verification `--accept` runs before applying a release tag (verified,
failed attestation, a manifest naming the wrong commit, no `gh` on PATH, a release with no manifest.txt asset —
against a stub `gh` on PATH, never the network or the real `gh`), and which states sync-check warns about (#42).
Stdlib unittest, no network: a hung or unreachable origin is faked with `remote.origin.uploadpack`.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SH = shutil.which("sh") or "/bin/sh"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import hermetic_env  # noqa: E402


def _env(home: Path, path: str | None = None) -> dict:
    """hermetic_env(home) with any inherited proxy variable stripped too — sync.sh's own concern, not
    hermetic_env's — and PATH pinned when a caller (the "no `timeout` binary" scenario) needs one without it."""
    env = {k: v for k, v in hermetic_env(home).items() if "proxy" not in k.lower()}
    if path is not None:
        env["PATH"] = path
    return env


# A stub `gh` for verify_release (sync.sh): every call is logged (one line per invocation) to
# $GH_STUB_LOG, and GH_AUTH_RC / GH_DOWNLOAD_RC / GH_DOWNLOAD_ERR / GH_ATTEST_HELP_RC / GH_ATTEST_RC / GH_ATTEST_ERR /
# GH_MANIFEST_CONTENT (all optional; unset reads as success) choose what each subcommand does. A
# successful "release download" writes $GH_MANIFEST_CONTENT to <the --dir value>/manifest.txt, same
# as the real `gh` would land the asset. Never the real `gh`, never the network.
GH_STUB = r"""#!/bin/bash
echo "$*" >> "$GH_STUB_LOG"
case "$*" in
  "auth status"*)
    if [ "${GH_AUTH_RC:-0}" != 0 ]; then
      echo "${GH_AUTH_ERR:-gh: not logged in (stub)}" >&2
      exit "${GH_AUTH_RC:-1}"
    fi
    exit 0 ;;
  "release download "*)
    rc="${GH_DOWNLOAD_RC:-0}"
    if [ "$rc" != 0 ]; then
      echo "${GH_DOWNLOAD_ERR:-gh: release not found (stub)}" >&2
      exit "$rc"
    fi
    dir=""
    prev=""
    for a in "$@"; do
      if [ "$prev" = "--dir" ]; then dir="$a"; fi
      prev="$a"
    done
    if [ -z "$dir" ]; then echo "stub gh: no --dir given" >&2; exit 9; fi
    mkdir -p "$dir"
    printf '%s' "$GH_MANIFEST_CONTENT" > "$dir/manifest.txt"
    exit 0 ;;
  "attestation verify --help"*)
    exit "${GH_ATTEST_HELP_RC:-0}" ;;
  "attestation verify "*)
    rc="${GH_ATTEST_RC:-0}"
    if [ "$rc" != 0 ]; then
      echo "${GH_ATTEST_ERR:-gh: attestation verification failed (stub)}" >&2
      exit "$rc"
    fi
    exit 0 ;;
  *) echo "stub gh: unexpected call: $*" >&2; exit 9 ;;
esac
"""


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
        (seed / ".gitignore").write_text(".sync.lock\n.sync.lock.d/\n.sync-status\n.sync-preview\nsync.log\n")
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-qm", "seed", cwd=seed)
        self.git("push", "-q", str(self.origin), "HEAD:main", cwd=seed)
        self.seed = seed
        self.kit = self.tmp / "kit"
        self.git("clone", "-q", "-b", "main", str(self.origin), str(self.kit), cwd=self.tmp)
        self.status_file = self.kit / ".sync-status"
        self.log_file = self.kit / "sync.log"
        self.preview_file = self.kit / ".sync-preview"

    # ── helpers ──
    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True, timeout=60)

    def rev_parse(self, ref, cwd) -> str:
        return subprocess.run(["git", "rev-parse", ref], cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True, timeout=60).stdout.strip()

    def sync(self, env=None, timeout=60, args=None):
        return subprocess.run([SH, str(self.kit / "sync.sh"), *(args or [])], env=env or self.env,
                              capture_output=True, text=True, timeout=timeout)

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

    def origin_commit(self, name="f", msg=None):
        """`msg` defaults to `name` (every existing caller wants that); pass a distinct one when a
        test greps the preview/log for `name` and the commit subject must not also match it."""
        path = self.seed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        self.git("add", name, cwd=self.seed)
        self.git("commit", "-qm", msg or name, cwd=self.seed)
        self.git("push", "-q", str(self.origin), "HEAD:main", cwd=self.seed)

    def seed_tag(self, name):
        """A lightweight tag on the seed's current HEAD, pushed to origin (`sync.sh` fetches --tags)."""
        self.git("tag", name, cwd=self.seed)
        self.git("push", "-q", str(self.origin), name, cwd=self.seed)

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

    def fake_github_remote(self, suffix: str = ""):
        """Give the kit checkout's origin a github.com-shaped URL — a made-up placeholder repo, never
        a real one — for verify_release's owner/repo parsing, while a `url.<base>.insteadOf` rewrite
        keeps every actual git operation hitting the real local `origin.git`. `git config --get
        remote.origin.url` (what origin_owner_repo() reads) returns the literal value unexpanded;
        `git remote get-url` would instead hand back the rewrite target, so a test must not use that
        to check what got configured."""
        url = "https://github.com/example/kit-sync-test" + suffix
        self.git("config", f"url.{self.origin}.insteadOf", url, cwd=self.kit)
        self.git("remote", "set-url", "origin", url, cwd=self.kit)

    def gh_bin(self) -> Path:
        """The stub `gh`'s directory, written once per test."""
        d = self.tmp / "ghbin"
        if not d.exists():
            d.mkdir()
            gh = d / "gh"
            gh.write_text(GH_STUB)
            gh.chmod(0o755)
        return d

    def gh_env(self, **gh_vars) -> dict:
        """self.env with PATH resolving `gh` to the stub only (any real `gh` on the host is excluded,
        per the brief: never the real `gh`), GH_STUB_LOG set, and the given GH_* overrides applied."""
        env = _env(self.tmp, path=str(self.gh_bin()) + os.pathsep + self.path_without("gh"))
        env["GH_STUB_LOG"] = str(self.tmp / "gh.log")
        env.update({k: str(v) for k, v in gh_vars.items()})
        return env

    def backdate(self, secs: int):
        """Move the epoch (field 3) of a pending/offline status `secs` into the past."""
        f = self.status()
        f[2] = str(int(f[2]) - secs)
        self.status_file.write_text(" ".join(f) + "\n")

    # ── ok, sha line, rotation ──
    def test_no_origin_and_no_ref_are_reported_not_silent(self):
        # silence reads as "in step with origin" (kit-health's ✅), so a state that cannot be compared must say so
        self.git("update-ref", "-d", "refs/remotes/origin/main", cwd=self.kit)
        self.assertIn("no origin/main ref", self.check())
        self.git("remote", "remove", "origin", cwd=self.kit)
        self.assertIn("no `origin` remote", self.check())

    def test_a_copy_without_git_is_reported(self):
        shutil.rmtree(self.kit / ".git")
        self.assertIn("is not a git checkout", self.check())

    def test_a_dotclaude_copy_without_git_is_silent(self):
        # `.claude` without git is the documented copy sync.sh itself skips (docs/packaging.md), not a
        # fault: only a directory that is neither `.claude` nor a plugin install is worth flagging here
        dotclaude = self.tmp / ".claude"
        (dotclaude / "context-db" / "bin").mkdir(parents=True)
        shutil.copy(KIT / "sync-check.sh", dotclaude / "sync-check.sh")
        shutil.copy(KIT / "context-db" / "bin" / "kit_profile.py", dotclaude / "context-db" / "bin" / "kit_profile.py")
        r = subprocess.run([SH, str(dotclaude / "sync-check.sh")], env=self.env, capture_output=True, text=True,
                            timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("is not a git checkout", r.stderr)

    def test_a_worktree_kit_is_checked_too(self):
        # a worktree's `.git` is a file: `[ -d .git ]` skipped every check
        wt = self.tmp / "wt"
        self.git("worktree", "add", "-q", "-b", "topic", str(wt), cwd=self.kit)
        r = subprocess.run([SH, str(wt / "sync-check.sh")], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertIn("checked out on 'topic'", r.stderr)

    def test_ahead_behind_failure_reports_the_failing_commands_own_error(self):
        # the `ahead` rev-list succeeds (a plain count, always non-empty) while the `behind` one fails:
        # the warn must carry *that* command's own stderr, not the count mistaken for an error
        real_git = shutil.which("git")
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        (fakebin / "git").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = rev-list ] && [ "$3" = "HEAD..origin/main" ]; then\n'
            '  echo "fatal: injected rev-list failure" >&2\n'
            "  exit 128\n"
            "fi\n"
            f'exec "{real_git}" "$@"\n'
        )
        (fakebin / "git").chmod(0o755)
        env = dict(self.env, PATH=str(fakebin) + os.pathsep + self.env["PATH"])
        r = subprocess.run([SH, str(self.kit / "sync-check.sh")], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("fatal: injected rev-list failure", r.stderr)
        self.assertNotIn("git: 0)", r.stderr, "the ahead count must not be reported as the error")

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

    # ── lock owner, stale lock, busy exit ──
    def _dead_pid(self) -> int:
        p = subprocess.Popen(["true"])
        p.wait()
        return p.pid

    def _owned_lockdir(self, pid: int, start: str | None = None) -> Path:
        """An owner file for `pid`; with `start` omitted this is the old two-field format (pid + the time
        the lock was taken, no process start time) — every existing caller wants that. Pass `start` (a
        `ps -o lstart=` string) to write the current three-field format instead."""
        d = self.kit / ".sync.lock.d"
        d.mkdir()
        line = f"{pid} 2026-01-01T00:00:00Z"
        if start is not None:
            line += f" {start}"
        (d / "owner").write_text(line + "\n")
        return d

    def _pid_start(self, pid: int) -> str:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
        return r.stdout.strip()

    def test_lock_busy_exits_3_and_names_the_holder(self):
        self._owned_lockdir(os.getpid())
        r = self.sync(env=_env(self.tmp, self.path_without("flock")))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn(f"held by pid {os.getpid()} since 2026-01-01T00:00:00Z", r.stderr)
        self.assertIn(f"pid {os.getpid()}", self.log_file.read_text())
        self.assertFalse(self.status_file.exists())

    def test_flock_busy_exits_3_and_names_the_holder(self):
        if not shutil.which("flock"):
            self.skipTest("no flock on this host")
        with open(self.kit / ".sync.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(f"{os.getpid()} 2026-01-01T00:00:00Z\n")
            fh.flush()
            r = self.sync(env=dict(self.env, SYNC_LOCK_WAIT="1"))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn(f"held by pid {os.getpid()}", r.stderr)

    def test_flock_holder_records_its_pid(self):
        if not shutil.which("flock"):
            self.skipTest("no flock on this host")
        self.assertEqual(self.sync().returncode, 0)
        self.assertRegex((self.kit / ".sync.lock").read_text(), r"^\d+ \d{4}-\d\d-\d\dT")

    def test_mkdir_lock_of_a_dead_owner_is_reclaimed_at_once(self):
        self._owned_lockdir(self._dead_pid())
        r = self.sync(env=_env(self.tmp, self.path_without("flock")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        self.assertIn("removed a stale lock dir (owner pid", self.log_file.read_text())
        self.assertFalse((self.kit / ".sync.lock.d").exists())

    def test_mkdir_lock_of_a_live_owner_is_never_aged_out(self):
        # old two-field owner line (no process start time, as written before that check existed):
        # judged by kill -0 alone, same as ever
        d = self._owned_lockdir(os.getpid())
        old = time.time() - 3600
        os.utime(d, (old, old))
        r = self.sync(env=_env(self.tmp, self.path_without("flock")))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertTrue((d / "owner").is_file(), "a live run's lock is left alone however old")

    def test_mkdir_lock_of_a_live_owner_with_matching_start_time_is_never_aged_out(self):
        if not shutil.which("ps"):
            self.skipTest("no ps on this host")
        # current three-field owner line, start time matching the real (live, ours) owner pid: a slow
        # but genuinely live sync must never lose its lock, no matter its age
        d = self._owned_lockdir(os.getpid(), self._pid_start(os.getpid()))
        old = time.time() - 3600
        os.utime(d, (old, old))
        r = self.sync(env=_env(self.tmp, self.path_without("flock")))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertTrue((d / "owner").is_file(), "a live owner, start time and all, is left alone however old")

    def test_mkdir_lock_of_a_reused_pid_is_reclaimed_at_once(self):
        """The owner pid is alive (it's ours) but its recorded process start time does not match its
        real one: the classic reused-pid case (a reboot or pid wraparound recycled the number onto an
        unrelated process). Must be reclaimed immediately — same as a dead owner, not aged out by the
        10-minute ownerless threshold."""
        if not shutil.which("ps"):
            self.skipTest("no ps on this host")
        d = self._owned_lockdir(os.getpid(), "Mon Jan  1 00:00:00 1999")
        r = self.sync(env=_env(self.tmp, self.path_without("flock")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        log = self.log_file.read_text()
        self.assertIn("removed a stale lock dir (owner pid", log)
        self.assertIn("different process", log)
        self.assertFalse((d).exists())

    def test_lockdir_vanishing_mid_check_is_retaken_not_reported_busy(self):
        """The fast `mkdir $LOCKDIR` in take_lockdir can lose to a holder that releases the lock in the
        gap before lockdir_stale runs: `find`/`owner_of` then see a directory that simply isn't there any
        more. That must read as a free lock (retake it), not a busy one (exit 3) — a fake `mkdir` stands
        in for the racing holder, removing the pre-seeded lock dir the instant our fast attempt fails."""
        path = self.path_without("flock", "mkdir")
        real_mkdir = shutil.which("mkdir")
        raced_marker = self.tmp / "mkdir.raced"  # outside the kit checkout: must not trip the dirty-tree check
        fake = Path(path) / "mkdir"
        fake.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  */.sync.lock.d)\n'
            f'    if [ ! -e "{raced_marker}" ]; then\n'
            f'      : >"{raced_marker}"\n'
            f'      {real_mkdir} "$@" 2>/dev/null && exit 0\n'
            '      rm -f "$1/owner"; rmdir "$1" 2>/dev/null\n'
            "      exit 1\n"
            "    fi\n"
            "    ;;\n"
            "esac\n"
            f'exec {real_mkdir} "$@"\n'
        )
        fake.chmod(0o755)
        (self.kit / ".sync.lock.d").mkdir()  # no owner file: just something for the fast mkdir to fail against
        r = self.sync(env=_env(self.tmp, path))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        self.assertIn("removed a stale lock dir (lock dir vanished", self.log_file.read_text())
        self.assertFalse((self.kit / ".sync.lock.d").exists())

    def test_concurrent_runs_break_a_stale_lock_once(self):
        """Six runs start together on a stale (ownerless, old) mkdir lock. `find` (the age check) answers
        at once but each run then stalls a little longer than the one before, so every run acts on a
        staleness verdict taken before the first one broke the lock: exactly one run may take it and
        fetch, the rest exit 3 without removing the fresh lock."""
        path = self.path_without("flock", "find")
        real_find = shutil.which("find")
        turns = self.tmp / "turns"
        turns.mkdir()
        slow = Path(path) / "find"
        slow.write_text("#!/bin/sh\n"
                        f"out=$({real_find} \"$@\")\n"
                        f"n=1; until mkdir {turns}/$n 2>/dev/null; do n=$((n + 1)); done\n"
                        "sleep \"$(awk \"BEGIN{print $n * 0.2}\")\"\n"
                        "[ -z \"$out\" ] || printf '%s\\n' \"$out\"\n")
        slow.chmod(0o755)
        fetches = self.tmp / "fetches"
        self.upload_pack(f"echo x >>{fetches}; sleep 8; exec git upload-pack \"$@\"")
        d = self.kit / ".sync.lock.d"
        d.mkdir()
        old = time.time() - 3600
        os.utime(d, (old, old))
        env = _env(self.tmp, path)
        procs = [subprocess.Popen([SH, str(self.kit / "sync.sh")], env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, text=True) for _ in range(6)]
        rcs = sorted(p.wait(timeout=60) for p in procs)
        for p in procs:
            p.stderr.close()
        self.assertEqual(len(fetches.read_text().splitlines()), 1, f"runs that fetched; exit codes {rcs}")
        self.assertEqual(rcs, [0, 3, 3, 3, 3, 3])
        self.assertEqual(self.status()[1], "ok")
        self.assertFalse(d.exists())

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

    # ── release channel: tag selection, the held preview, --accept, the `main` opt-out ──
    def test_default_channel_holds_the_highest_merged_release_tag(self):
        # two releases, then an unreleased commit: a bare run holds the highest tag origin/main
        # contains, not whatever commit origin/main is actually at
        start_sha = self.rev_parse("HEAD", cwd=self.kit)
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.origin_commit("b")
        self.seed_tag("v0.2.0")
        self.origin_commit("c")
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "held")
        self.assertEqual(self.status()[2], "v0.2.0")
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), start_sha,
                          "a held run applies nothing: HEAD stays where it started")

    def test_held_preview_lists_commits_and_unattended_files(self):
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.origin_commit("skills/widget/SKILL.md", msg="add the widget skill")
        self.origin_commit("settings.json", msg="wire the widget hook")
        self.seed_tag("v0.2.0")
        self.sync()
        self.assertEqual(self.status()[1], "held")
        preview = self.preview_file.read_text()
        self.assertIn("v0.2.0", preview)
        self.assertIn("commits:", preview)
        skills_marker = preview.index("changed skills/agents/hooks:")
        unattended_marker = preview.index("other files that run unattended:")
        skills_hit = preview.index("skills/widget/SKILL.md")
        settings_hit = preview.index("settings.json")
        self.assertTrue(skills_marker < skills_hit < unattended_marker, "the skill shows under its own heading")
        self.assertTrue(unattended_marker < settings_hit, "settings.json shows under the unattended-files heading")

    def test_accept_applies_the_held_tag_not_further(self):
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.origin_commit("b")  # unreleased: must stay un-applied
        self.sync()
        self.assertEqual(self.status()[1], "held")
        tag_sha = self.rev_parse("v0.1.0^{commit}", cwd=self.seed)
        # no gh on this PATH: manifest verification (the dedicated tests further down cover it on its
        # own) cannot run, so this also exercises the "cannot run" contract — applied, `unverified`
        # in the detail — without that being what this test is about
        r = self.sync(env=_env(self.tmp, path=self.path_without("gh")), args=["--accept"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        self.assertIn("v0.1.0", self.status_file.read_text())
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), tag_sha, "accept stops at the tag, not origin/main")
        self.assertFalse(self.preview_file.exists())

    def test_kit_channel_main_bypasses_the_hold(self):
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.origin_commit("b")  # unreleased
        self.git("config", "kit.channel", "main", cwd=self.kit)
        tip_sha = self.rev_parse("HEAD", cwd=self.seed)
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        self.assertFalse(self.preview_file.exists())
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), tip_sha, "kit.channel main tracks origin/main, tags aside")

    def test_sync_check_names_a_waiting_release_not_a_commit_count(self):
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        self.assertEqual(self.status()[1], "held")
        out = self.check()
        self.assertIn("release v0.1.0 is waiting", out)
        self.assertNotIn("commit(s) ahead", out)

    def test_sync_check_is_silent_at_the_tag_with_unreleased_commits_on_origin(self):
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync(env=_env(self.tmp, path=self.path_without("gh")), args=["--accept"])
        self.origin_commit("b")  # unreleased: the release channel is not behind because of it
        self.sync()
        self.assertEqual(self.status()[1], "ok")
        out = self.check()
        self.assertNotIn("is waiting", out)
        self.assertNotIn("commit(s) ahead", out)

    def test_sync_check_on_the_main_channel_still_counts_commits(self):
        self.git("config", "kit.channel", "main", cwd=self.kit)
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.git("fetch", "-q", "--tags", "origin", cwd=self.kit)
        self.assertIn("origin/main is 1 commit(s) ahead", self.check())

    # ── --accept: manifest verification before applying a release tag ──
    def test_accept_verified_manifest_applies_cleanly(self):
        self.fake_github_remote()
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        self.assertEqual(self.status()[1], "held")
        tag_sha = self.rev_parse("v0.1.0^{commit}", cwd=self.seed)
        env = self.gh_env(GH_MANIFEST_CONTENT=f"commit {tag_sha}\n")
        r = self.sync(env=env, args=["--accept"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        self.assertNotIn("unverified", self.status_file.read_text())
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), tag_sha)
        log = (self.tmp / "gh.log").read_text()
        self.assertIn("attestation verify", log)
        self.assertIn("release download v0.1.0", log)

    def test_accept_names_the_repo_without_a_dot_git_suffix(self):
        for suffix in (".git", ".git/", "/"):
            with self.subTest(suffix=suffix):
                self.fake_github_remote(suffix)
                out = subprocess.run(
                    ["bash", "-c", 'eval "$(sed -n \'/^origin_owner_repo() {/,/^}/p\' "$0")"; origin_owner_repo',
                     str(self.kit / "sync.sh")], cwd=self.kit, capture_output=True, text=True)
                self.assertEqual(out.stdout.strip(), "example/kit-sync-test", out.stderr)

    def test_accept_passes_the_origin_repo_to_gh(self):
        self.fake_github_remote(".git")
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        tag_sha = self.rev_parse("v0.1.0^{commit}", cwd=self.seed)
        r = self.sync(env=self.gh_env(GH_MANIFEST_CONTENT=f"commit {tag_sha}\n"), args=["--accept"])
        self.assertEqual(r.returncode, 0, r.stderr)
        log = (self.tmp / "gh.log").read_text()
        self.assertIn("--repo example/kit-sync-test ", log)
        self.assertIn("--signer-workflow example/kit-sync-test/.github/workflows/release.yml", log)
        self.assertNotIn(".git/", log.replace("/.github/", "/"))

    def test_accept_non_github_origin_never_calls_gh(self):
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        r = self.sync(env=self.gh_env(), args=["--accept"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("unverified", self.status_file.read_text())
        self.assertFalse((self.tmp / "gh.log").exists(), "no gh call for an origin that is not on github.com")

    def test_accept_failed_attestation_applies_nothing(self):
        self.fake_github_remote()
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        before = self.rev_parse("HEAD", cwd=self.kit)
        tag_sha = self.rev_parse("v0.1.0^{commit}", cwd=self.seed)
        env = self.gh_env(GH_MANIFEST_CONTENT=f"commit {tag_sha}\n", GH_ATTEST_RC="1",
                           GH_ATTEST_ERR="gh: attestation verification failed (stub)")
        r = self.sync(env=env, args=["--accept"])
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("nothing", r.stderr.lower())
        self.assertIn("applied", r.stderr.lower())
        self.assertEqual(self.status()[1], "error")
        self.assertIn("v0.1.0", self.status_file.read_text())
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), before, "a failed check applies nothing")

    def test_accept_manifest_commit_mismatch_applies_nothing(self):
        self.fake_github_remote()
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        before = self.rev_parse("HEAD", cwd=self.kit)
        wrong_sha = "0" * 40
        env = self.gh_env(GH_MANIFEST_CONTENT=f"commit {wrong_sha}\n")
        r = self.sync(env=env, args=["--accept"])
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("nothing", r.stderr.lower())
        self.assertEqual(self.status()[1], "error")
        self.assertIn("v0.1.0", self.status_file.read_text())
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), before, "a commit mismatch applies nothing")

    def test_accept_no_gh_on_path_applies_unverified(self):
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        tag_sha = self.rev_parse("v0.1.0^{commit}", cwd=self.seed)
        env = _env(self.tmp, path=self.path_without("gh"))
        r = self.sync(env=env, args=["--accept"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        self.assertIn("unverified", self.status_file.read_text())
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), tag_sha, "cannot-run still applies the update")

    def test_accept_release_without_manifest_asset_applies_unverified(self):
        self.fake_github_remote()
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        tag_sha = self.rev_parse("v0.1.0^{commit}", cwd=self.seed)
        env = self.gh_env(GH_DOWNLOAD_RC="1",
                           GH_DOWNLOAD_ERR='gh: no assets found matching pattern "manifest.txt" (stub)')
        r = self.sync(env=env, args=["--accept"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        self.assertIn("unverified", self.status_file.read_text())
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), tag_sha, "no asset still applies the update")

    def test_accept_gh_without_attestation_command_applies_unverified(self):
        """A gh too old to know `gh attestation` cannot run the check: that is not a failed check."""
        self.fake_github_remote()
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        tag_sha = self.rev_parse("v0.1.0^{commit}", cwd=self.seed)
        r = self.sync(env=self.gh_env(GH_ATTEST_HELP_RC="1"), args=["--accept"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        self.assertIn("unverified (this gh has no attestation command", self.status_file.read_text())
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), tag_sha, "cannot-run still applies the update")
        self.assertNotIn("release download", (self.tmp / "gh.log").read_text())

    def test_accept_attestation_verify_without_an_answer_applies_unverified(self):
        """The network going away between the download and the verify call is no verdict on the
        manifest; gh's own rejection (the failed-attestation test above) still applies nothing."""
        self.fake_github_remote()
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        self.sync()
        before = self.rev_parse("HEAD", cwd=self.kit)
        tag_sha = self.rev_parse("v0.1.0^{commit}", cwd=self.seed)
        env = self.gh_env(GH_MANIFEST_CONTENT=f"commit {tag_sha}\n", GH_ATTEST_RC="1")
        for err in ("dial tcp: lookup api.github.com: no such host", "error connecting to api.github.com"):
            with self.subTest(err=err):
                self.git("reset", "-q", "--hard", before, cwd=self.kit)
                r = self.sync(env=dict(env, GH_ATTEST_ERR=err), args=["--accept"])
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.status()[1], "ok")
                self.assertIn("unverified (attestation verify could not reach github)",
                              self.status_file.read_text())
                self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), tag_sha)

    def test_accept_kit_channel_main_skips_verification(self):
        self.git("config", "kit.channel", "main", cwd=self.kit)
        self.origin_commit("a")
        self.seed_tag("v0.1.0")
        tip_sha = self.rev_parse("HEAD", cwd=self.seed)
        # no gh at all: if verify_release ran anyway it would show "unverified" (no gh on PATH)
        env = _env(self.tmp, path=self.path_without("gh"))
        r = self.sync(env=env, args=["--accept"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status()[1], "ok")
        self.assertNotIn("unverified", self.status_file.read_text())
        self.assertEqual(self.rev_parse("HEAD", cwd=self.kit), tip_sha)

    def test_sync_check_silent_on_unverified_ok(self):
        # the `unverified` word is kit-health's to warn on — sync-check has no `ok)` case at
        # all, so this locks that in rather than relying on an absence to stay accidental. A real
        # sync first, so hooks/origin/main are all in order; other checks (e.g. the env-store check)
        # may still warn about unrelated fixture state, so assert on the sync status line only,
        # matching the count-based style used elsewhere in this file rather than requiring total
        # silence.
        self.sync()
        self.status_file.write_text("2026-01-01T00:00:00Z ok kit@abc1234 (v0.1.0) unverified (no gh on PATH)\n")
        out = self.check()
        self.assertEqual(out.count("unverified"), 0)
        self.assertEqual(out.count("last sync"), 0)


if __name__ == "__main__":
    unittest.main()
