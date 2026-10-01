"""The drain lock, extended: `run` moves jobs a pre-workspace-queue kit left under the kit dir itself, under
the very lock it already holds for the whole drain — `make sign` never runs a separate migration step first.
The lock file also names the holder's pid and the job it is currently on, so a second concurrent `run` can say
so by name; and a `run` that takes over a lock a dead holder left behind can tell a job still running out there
(detached into its own process group, so it outlives a killed drain) from a clean handoff, refusing to start a
second job on top of it. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import contextlib
import fcntl
import io
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


class SignTargetCallsRunOnly(unittest.TestCase):
    """`make sign` must call `signq.py run` and nothing else — the legacy-queue migration moved inside `run`
    itself, so a separate `migrate-legacy` step ahead of it would race an already-migrating drain's lock."""

    def test_sign_recipe_has_no_separate_migrate_legacy_step(self):
        recipe = _recipe("sign")
        self.assertIn("run", recipe)
        self.assertNotIn("migrate-legacy", recipe)

    def test_sign_list_recipe_also_has_no_migration_step(self):
        # the chosen policy: an overview only reads the queue, it never migrates anything itself
        recipe = _recipe("sign_list")
        self.assertIn("list", recipe)
        self.assertNotIn("migrate-legacy", recipe)


class MigrationRunsUnderTheLock(unittest.TestCase):
    """`run` moves a pre-workspace-queue kit's leftover jobs itself, once it holds the drain lock, before
    loading any job — never as a side effect of `list`, and never as a step a second concurrent `run` could
    race against an in-progress drain."""

    def test_an_uncontended_run_migrates_while_holding_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            legacy = tmp / "kit" / "sign-queue"
            legacy.mkdir(parents=True)
            legacy_job = legacy / "a.sh"
            legacy_job.write_text('# META {"topic": "key-123-legacy", "ticket": "KEY-123"}\n'
                                   'echo "pushed deadbeefdeadbeef G legacy"\n')
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
            legacy = tmp / "kit" / "sign-queue"
            legacy.mkdir(parents=True)
            legacy_job = legacy / "a.sh"
            legacy_job.write_text('# META {"topic": "key-123-legacy", "ticket": "KEY-123"}\n'
                                   'echo "pushed deadbeefdeadbeef G legacy"\n')
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
    """A second `run` that finds the lock already held must print exactly one line naming both the holder's
    pid and the job it is currently on — never a bare "already holds the lock"."""

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


class StaleJobBlocksTheNextDrain(unittest.TestCase):
    """A `run` that takes a just-freed lock but finds the lock file's recorded job pid still alive (the
    previous holder died mid-job — e.g. a `kill -9` — while its own-process-group job kept running) must
    name that job and pid, exit without starting any new job, and leave the rest of the queue untouched."""

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


class JobOwnProcessGroup(unittest.TestCase):
    """Each job runs in its own session/process group (`start_new_session=True`), not the drain's — the
    detachment `StaleJobBlocksTheNextDrain` above relies on to tell a leftover live job apart from the drain
    process that spawned it."""

    def test_a_job_is_its_own_process_group_leader(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.Q.mkdir(parents=True, exist_ok=True)
            job = sq.Q / "20260101T000000Z-000-g.sh"
            # setsid() makes the new session's process group id equal to the session leader's own pid —
            # here that leader is the `sh` process itself (Popen's start_new_session, before the exec).
            job.write_text(
                '# META {"topic": "key-123-pg", "ticket": "KEY-123"}\n'
                'pgid=`ps -o pgid= -p $$`\n'
                'pgid=`echo $pgid`\n'
                'if [ "$pgid" = "$$" ]; then\n'
                '  echo "pushed deadbeefdeadbeef G own-group"\n'
                'else\n'
                '  echo "pgid [$pgid] pid [$$]"\n'
                '  exit 1\n'
                'fi\n'
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sq.main(["run"])
            out = buf.getvalue()
            self.assertEqual(rc, 0, out)
            self.assertIn("pushed", out)
            self.assertNotIn("pgid [", out)


if __name__ == "__main__":
    unittest.main()
