"""The drain lock: `run` moves jobs a pre-workspace-queue kit left under the kit dir itself, under the lock it
holds for the whole drain, and `migrate-legacy` takes the same lock for its move. The lock file names the
holder's pid and the job it is on, so a second concurrent `run` can say so; a `run` that takes over the lock of
a drain that was killed outright tells a job that is still running from a clean handoff and refuses to start a
second job on top of it. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import contextlib
import fcntl
import io
import json
import signal
import sys
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
import importlib.util
import os

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SIGNQ = KIT / "skills" / "sign-queue" / "signq.py"
WORKSPACE_MK = KIT / "workspace.mk"
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402


def _env(tmp, ctx=None) -> dict:
    """hermetic_env(tmp) with any ambient SIGN_QUEUE_* stripped (never leak a developer's own queue dir into a
    fixture) and ctx, when given, as CONTEXT_ROOT — the same shape the other sign-queue test files use."""
    env = {k: v for k, v in hermetic_env(tmp).items() if not k.startswith("SIGN_QUEUE_")}
    if ctx is not None:
        env["CONTEXT_ROOT"] = str(ctx)
    return env


def load_signq(name: str = "signq_drain_test"):
    spec = importlib.util.spec_from_file_location(name, SIGNQ)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_in(ctx: Path):
    with mock.patch.dict(os.environ, _env(ctx, ctx), clear=True):
        return load_signq()


def _recipe(target: str) -> str:
    """The recipe body (its tab-indented lines) that follows `<target>:` in workspace.mk, stopping at the
    first line that is not a tab-indented continuation — enough to check what one `make` target actually
    runs without parsing the whole file as a Makefile."""
    text = WORKSPACE_MK.read_text()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line == f"{target}:":
            body = []
            for cont in lines[i + 1:]:
                if not cont.startswith("\t"):
                    break
                body.append(cont)
            return "\n".join(body)
    raise AssertionError(f"no {target!r} target found in {WORKSPACE_MK}")


def _legacy_job(tmp: Path) -> Path:
    legacy = tmp / "kit" / "sign-queue"
    legacy.mkdir(parents=True)
    job = legacy / "a.sh"
    job.write_text('# META {"topic": "key-123-legacy", "ticket": "KEY-123"}\n'
                   'echo "pushed deadbeefdeadbeef G legacy"\n')
    return job


class SignTargetsAndTheMigration(unittest.TestCase):
    """`make sign` calls `signq.py run` and nothing else: `run` migrates itself, under its lock. `make
    sign_list` still runs `migrate-legacy` before the overview — the step that tells a user where their old
    jobs went — and that subcommand takes the drain lock for the move."""

    def test_sign_recipe_has_no_separate_migrate_legacy_step(self):
        recipe = _recipe("sign")
        self.assertIn("run", recipe)
        self.assertNotIn("migrate-legacy", recipe)

    def test_sign_list_recipe_migrates_then_lists(self):
        recipe = _recipe("sign_list")
        self.assertRegex(recipe, r"migrate-legacy -q && \S+ list")

    def test_migrate_legacy_moves_nothing_while_a_drain_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            legacy_job = _legacy_job(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.migrate_legacy.__defaults__ = (legacy_job.parent, sq.Q)
            sq.Q.mkdir(parents=True, exist_ok=True)
            held = open(sq.LOCK, "a+")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            sq._write_lock_state(held, holder_pid=4321, job="KEY-123-p1", job_pid=4322, job_started="then")
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = sq.main(["migrate-legacy", "-q"])
                self.assertEqual(rc, 0)
                self.assertTrue(legacy_job.exists(), "the move must wait for the drain that holds the lock")
                self.assertEqual(sq._read_lock_state(held).get("job_pid"), 4322,
                                 "the holder's record must survive a locked-out migrate-legacy")
            finally:
                fcntl.flock(held.fileno(), fcntl.LOCK_UN)
                held.close()

    def test_migrate_legacy_moves_the_jobs_once_the_lock_is_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            legacy_job = _legacy_job(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.migrate_legacy.__defaults__ = (legacy_job.parent, sq.Q)
            sq.Q.mkdir(parents=True, exist_ok=True)
            # what a drain that was killed mid-job leaves behind: no lock held, a job still on record
            with open(sq.LOCK, "a+") as left:
                sq._write_lock_state(left, holder_pid=4321, job="KEY-123-p1", job_pid=4322, job_started="then")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = sq.main(["migrate-legacy", "-q"])
            self.assertEqual(rc, 0)
            self.assertFalse(legacy_job.exists())
            self.assertTrue((sq.Q / "a.sh").exists())
            with open(sq.LOCK) as lock_f:
                self.assertEqual(sq._read_lock_state(lock_f).get("job_pid"), 4322,
                                 "migrate-legacy must not erase the record the next `run` reads")


class MigrationRunsUnderTheLock(unittest.TestCase):
    """`run` moves a pre-workspace-queue kit's leftover jobs itself, once it holds the drain lock and before
    it loads a job — never as a step a second concurrent `run` could race against a drain in progress."""

    def test_an_uncontended_run_migrates_while_holding_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            legacy_job = _legacy_job(tmp)
            legacy = legacy_job.parent
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            q = sq.Q
            real_migrate = sq.migrate_legacy
            held_while_migrating = []

            def spy(legacy_arg=legacy, q_arg=q):
                # a fresh fd on the same lock path: flock is per open file description, so a second one
                # from this very process still fails to take it while cmd_run's own fd holds it exclusively
                probe = open(sq.LOCK, "a+")
                try:
                    fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held_while_migrating.append(False)
                    fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
                except OSError:
                    held_while_migrating.append(True)
                finally:
                    probe.close()
                return real_migrate(legacy_arg, q_arg)

            with mock.patch.object(sq, "migrate_legacy", side_effect=spy):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = sq.main(["run"])
            out = buf.getvalue()
            self.assertEqual(rc, 0, out)
            self.assertEqual(held_while_migrating, [True], "migrate_legacy ran without the drain lock held")
            self.assertFalse(legacy_job.exists(), "the legacy job must have moved into the workspace queue")
            self.assertIn("pushed", out, "the migrated job must have been picked up and run by the same drain")

    def test_a_held_lock_blocks_the_migration_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            legacy_job = _legacy_job(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.Q.mkdir(parents=True, exist_ok=True)
            held = open(sq.LOCK, "a+")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            migrate_mock = mock.Mock(wraps=sq.migrate_legacy)
            try:
                with mock.patch.object(sq, "migrate_legacy", migrate_mock):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        rc = sq.main(["run"])
            finally:
                fcntl.flock(held.fileno(), fcntl.LOCK_UN)
                held.close()
            self.assertEqual(rc, 0, buf.getvalue())
            migrate_mock.assert_not_called()
            self.assertTrue(legacy_job.exists(), "a locked-out run must not touch the legacy queue either")


class HeldLockNamesTheHolder(unittest.TestCase):
    """A second `run` that finds the lock held prints one line naming the holder's pid and the job it is on,
    and the drain itself is what writes that record: while a job runs the lock file names it, afterwards the
    record is idle again."""

    def test_the_message_names_pid_and_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.Q.mkdir(parents=True, exist_ok=True)
            held = open(sq.LOCK, "a+")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            sq._write_lock_state(held, holder_pid=4321, job="KEY-123-p1")
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = sq.main(["run"])
            finally:
                fcntl.flock(held.fileno(), fcntl.LOCK_UN)
                held.close()
            self.assertEqual(rc, 0)
            self.assertIn("another drain (pid 4321, on KEY-123-p1) already holds the lock — exiting",
                          buf.getvalue())


    def test_a_running_job_is_on_record_and_the_record_is_idle_afterwards(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.Q.mkdir(parents=True, exist_ok=True)
            job = sq.Q / "20260101T000000Z-000-r.sh"
            # the job reads the lock file itself: it succeeds only once the record names this job's file and
            # this very process (`$$` is the `sh` the drain started)
            job.write_text(
                '# META {"topic": "key-123-rec", "ticket": "KEY-123"}\n'
                'i=0\n'
                'while [ $i -lt 100 ]; do\n'
                f'  if grep -q "\\"job\\": \\"{job.name}\\", \\"job_pid\\": $$[,}}]" "{sq.LOCK}"; then\n'
                '    echo "pushed deadbeefdeadbeef G recorded"\n'
                '    exit 0\n'
                '  fi\n'
                '  i=$((i + 1))\n'
                '  sleep 0.1\n'
                'done\n'
                f'echo "not on record: `cat "{sq.LOCK}"`"\n'
                'exit 1\n'
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sq.main(["run", "-v"])
            out = buf.getvalue()
            self.assertEqual(rc, 0, out)
            self.assertIn("pushed", out)
            self.assertNotIn("not on record", out)
            with open(sq.LOCK) as lock_f:
                state = sq._read_lock_state(lock_f)
            self.assertEqual((state.get("pid"), state.get("job"), state.get("job_pid")), (os.getpid(), "", None))


class StaleJobBlocksTheNextDrain(unittest.TestCase):
    """A `run` that takes a just-freed lock but finds the recorded job process still alive (the previous
    drain was killed outright mid-job, and its job runs on) names that job and pid, exits without starting a
    job, and leaves the queue as it is."""

    def test_a_drain_killed_mid_job_blocks_the_next_run_until_its_job_is_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.Q.mkdir(parents=True, exist_ok=True)
            job = sq.Q / "20260101T000000Z-000-k.sh"
            job.write_text('# META {"topic": "key-123-kill", "ticket": "KEY-123"}\nexec sleep 30\n')
            env = _env(ctx, ctx)
            run = [sys.executable, str(SIGNQ), "run"]
            drain = subprocess.Popen(run, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            job_pid = None
            try:
                deadline = time.monotonic() + 20
                while job_pid is None and time.monotonic() < deadline:
                    try:
                        job_pid = json.loads(sq.LOCK.read_text() or "{}").get("job_pid")
                    except (OSError, ValueError):
                        pass
                    if job_pid is None:
                        time.sleep(0.05)
                self.assertIsNotNone(job_pid, "the drain never put its job on record")
                drain.kill()  # SIGKILL: no handler runs, the job is left behind
                drain.wait(timeout=10)
                self.assertTrue(sq._alive(job_pid), "the job must outlive a drain that was killed outright")
                second = subprocess.run(run, env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(second.returncode, sq.EXIT_STALE_JOB, second.stdout + second.stderr)
                self.assertIn(job.name, second.stdout)
                self.assertIn(f"pid {job_pid}", second.stdout)
                self.assertTrue(job.exists(), "the job file must be left alone")
                os.kill(job_pid, signal.SIGKILL)
                deadline = time.monotonic() + 10
                while sq._alive(job_pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                third = subprocess.run(run + ["--dry-run"], env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
            finally:
                if drain.poll() is None:
                    drain.kill()
                    drain.wait(timeout=10)
                if job_pid is not None:
                    with contextlib.suppress(OSError):
                        os.kill(job_pid, signal.SIGKILL)

    def test_a_live_recorded_job_stops_the_drain_before_it_starts_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.Q.mkdir(parents=True, exist_ok=True)
            pending = sq.Q / "20260101T000000Z-000-p.sh"
            pending.write_text('# META {"topic": "key-123-p2", "ticket": "KEY-123"}\n'
                                'echo "pushed deadbeefdeadbeef G p2"\n')
            # a real child this test spawns and kills itself — never a sleep raced against a timer
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                    start_new_session=True)
            try:
                lock_f = open(sq.LOCK, "a+")
                sq._write_lock_state(lock_f, holder_pid=999999, job="key-123-p1", job_pid=proc.pid,
                                     job_started=sq._pid_start(proc.pid))
                lock_f.close()
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = sq.cmd_run([])
                out = buf.getvalue()
            finally:
                proc.kill()
                proc.wait(timeout=5)
            self.assertEqual(rc, sq.EXIT_STALE_JOB)
            self.assertIn("key-123-p1", out)
            self.assertIn(str(proc.pid), out)
            self.assertNotIn("pushed", out, "no new job may start on top of a still-running one")
            self.assertTrue(pending.exists(), "the pending job must be left alone, not consumed")

    def test_a_dead_recorded_job_pid_is_cleared_and_the_drain_runs_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.Q.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
            started = sq._pid_start(proc.pid)
            proc.wait(timeout=5)  # fully reaped: its pid is now free to be (cheaply) checked as dead
            self.assertFalse(sq._job_still_alive(proc.pid, started),
                             "the test's own child must read as gone once reaped")
            lock_f = open(sq.LOCK, "a+")
            sq._write_lock_state(lock_f, holder_pid=999999, job="key-123-p0", job_pid=proc.pid, job_started=started)
            lock_f.close()
            pending = sq.Q / "20260101T000000Z-000-p.sh"
            pending.write_text('# META {"topic": "key-123-p3", "ticket": "KEY-123"}\n'
                                'echo "pushed deadbeefdeadbeef G p3"\n')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sq.main(["run"])
            out = buf.getvalue()
            self.assertEqual(rc, 0, out)
            self.assertIn("pushed", out)
            self.assertFalse(pending.exists(), "a cleared stale pid must not stop the drain from doing its job")


if __name__ == "__main__":
    unittest.main()
