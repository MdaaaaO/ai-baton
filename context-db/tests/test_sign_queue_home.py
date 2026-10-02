"""#7: the sign queue lives in the workspace (`<.context>/state/sign-queue/`), never under the kit, which is the plugin
cache on a plugin install; jobs a pre-#7 kit queued under the kit dir move over on first use, never overwriting.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SIGNQ = KIT / "skills" / "sign-queue" / "signq.py"
ENQUEUE = KIT / "skills" / "sign-queue" / "enqueue.sh"
sys.path.insert(0, str(HERE.parent))
from tests import extend_git_config, hermetic_env  # noqa: E402


def _env(tmp, ctx=None) -> dict:
    """hermetic_env(tmp) with any ambient SIGN_QUEUE_* stripped, so a developer's own sign-queue env (or queue
    dir) can never leak into a fixture; ctx, when given, becomes CONTEXT_ROOT — the shape every test below needs
    for enqueue.sh / signq.py, plus the git isolation a seeded fixture repo needs."""
    env = {k: v for k, v in hermetic_env(tmp).items() if not k.startswith("SIGN_QUEUE_")}
    if ctx is not None:
        env["CONTEXT_ROOT"] = str(ctx)
    return env


def load_signq():
    spec = importlib.util.spec_from_file_location("signq_home_test", SIGNQ)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class QueueHome(unittest.TestCase):
    def test_queue_is_under_the_workspace_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Path(tmp) / "ws" / ".context"
            ctx.mkdir(parents=True)
            with mock.patch.dict(os.environ, _env(tmp, ctx), clear=True):
                sq = load_signq()
            self.assertEqual((sq.Q, sq.CONTEXT, sq.ROOT), (ctx / "state" / "sign-queue", ctx, ctx.parent))
            self.assertEqual(sq.LEGACY_Q, KIT / "sign-queue")
            self.assertFalse(str(sq.Q).startswith(str(KIT)))  # never below the kit

    def test_enqueue_refuses_without_a_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["sh", str(ENQUEUE), "t", "/nonexistent", "b", "/nonexistent/msg"],
                               env=_env(tmp, "/nonexistent/ws/.context"), capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("no workspace .context/ found", r.stderr)


class FirstEnqueue(unittest.TestCase):
    def test_a_fresh_workspace_gets_its_queue_dir_on_the_first_enqueue(self):
        # review of #27: nothing ships or seeds `.context/state/sign-queue/` — enqueue.sh must create it
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Path(tmp) / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = Path(tmp) / "ws" / "repo"
            wt.mkdir()
            git_env = _env(tmp)  # every git call below ignores the host's own commit.gpgsign / gpg.format
            git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
            subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True, env=git_env)
            (wt / "a.txt").write_text("a\n")
            subprocess.run([*git, "add", "a.txt"], check=True, env=git_env)
            subprocess.run([*git, "commit", "-q", "-m", "chore: seed"], check=True, env=git_env)
            (wt / "a.txt").write_text("b\n")
            msg = Path(tmp) / "msg.txt"
            msg.write_text("fix: change a\n")
            r = subprocess.run(["sh", str(ENQUEUE), "topic-a", str(wt), "main", str(msg), "--files", "a.txt",
                                "--ticket", "none", "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                               env=_env(tmp, ctx), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            jobs = list((ctx / "state" / "sign-queue").glob("*-topic-a.sh"))
            self.assertEqual(len(jobs), 1, r.stdout + r.stderr)


class Migration(unittest.TestCase):
    def test_legacy_jobs_move_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy, q = Path(tmp) / "kit" / "sign-queue", Path(tmp) / "ws" / ".context" / "state" / "sign-queue"
            (legacy / "logs").mkdir(parents=True)
            (legacy / "a.sh").write_text("job a\n")
            (legacy / "b.sh.failed").write_text("old b\n")
            (legacy / "logs" / "a.log").write_text("log a\n")
            (legacy / ".gitkeep").write_text("")
            q.mkdir(parents=True)
            (q / "b.sh.failed").write_text("new b\n")
            with mock.patch.dict(os.environ, _env(tmp, Path(tmp) / "ws" / ".context"), clear=True):
                sq = load_signq()
                moved = sq.migrate_legacy(legacy, q)
            self.assertEqual(moved, 2)
            self.assertEqual((q / "a.sh").read_text(), "job a\n")
            self.assertEqual((q / "logs" / "a.log").read_text(), "log a\n")
            self.assertEqual((q / "b.sh.failed").read_text(), "new b\n")  # never overwritten
            self.assertTrue((legacy / "b.sh.failed").exists())  # left in place, named on stderr
            with mock.patch.dict(os.environ, {"SIGN_QUEUE_DIR": str(q)}):
                (legacy / "c.sh").write_text("c\n")
                self.assertEqual(sq.migrate_legacy(legacy, q), 0)  # an explicit SIGN_QUEUE_DIR: never touched


if __name__ == "__main__":
    unittest.main()


class HostileValues(unittest.TestCase):
    """#123: the job is shell code the owner runs on the host with the signing key — a branch, path or file name
    carrying `'`, `$(…)`, a backtick or `;` must reach git as data, never run."""

    def test_job_runs_hostile_values_as_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            origin, wt = tmp / "origin.git", tmp / "ws" / "re'po $(touch PWNED-wt)"
            git_env = _env(tmp, ctx)  # the baseline; the full env below layers the ssh-signing config on top
            subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, env=git_env)
            subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True, env=git_env)
            key = tmp / "key"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
            cfg = {"user.name": "t", "user.email": "t@example.invalid", "gpg.format": "ssh",
                   "user.signingkey": str(key), "remote.origin.url": str(origin)}
            env = extend_git_config(git_env, cfg)  # appended after git_env's own gc.auto / maintenance.auto
            git = ["git", "-C", str(wt)]
            (wt / "seed").write_text("s\n")
            subprocess.run([*git, "add", "seed"], check=True, env=env)
            subprocess.run([*git, "commit", "-q", "-m", "chore: seed"], check=True, env=env)
            branch = "fix/x';touch${IFS}PWNED-br;'`touch${IFS}PWNED-tick`"
            subprocess.run([*git, "checkout", "-q", "-b", branch], check=True, env=env)
            names = ["it's.txt", "$param.txt", "a`touch${IFS}PWNED-f`;.txt"]
            for n in names:
                (wt / n).write_text(n)
            (wt / "untouched.txt").write_text("not listed\n")
            msg = tmp / "m'sg $(touch PWNED-msg).txt"
            text = "fix: don't $(touch PWNED-body) `id`\n\nsecond line; 'quoted'\n"
            msg.write_text(text)
            r = subprocess.run(["sh", str(ENQUEUE), "hostile", str(wt), branch, str(msg), "--new-branch",
                                "--files", " ".join(names), "--ticket", "none", "--epic", "none", "--pr", "1",
                                "--summary", "s", "--by", "t"], env=env, capture_output=True, text=True, timeout=60,
                               cwd=tmp)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            job = Path(r.stdout.strip().splitlines()[-1])
            run = subprocess.run(["sh", str(job)], env=env, capture_output=True, text=True, timeout=60, cwd=tmp)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual([p.name for p in tmp.rglob("PWNED*")], [], "a value ran as code")
            got = subprocess.run(["git", "--git-dir", str(origin), "log", "-1", "--format=%B", branch],
                                 capture_output=True, text=True, check=True, env=env).stdout
            self.assertEqual(got.rstrip("\n"), text.rstrip("\n"))
            files = subprocess.run(["git", "--git-dir", str(origin), "show", "--name-only", "--format=", branch],
                                   capture_output=True, text=True, check=True, env=env).stdout.split("\n")
            self.assertEqual(sorted(f for f in files if f), sorted(names))
            sq = load_signq()
            self.assertEqual(sq.Job(job).meta.get("files"), 3)
            legacy = tmp / "legacy.sh"  # no META line: signq.py reads the quoted assignments back as sh would
            legacy.write_text("".join(l for l in job.read_text().splitlines(True) if not l.startswith("# META")))
            self.assertEqual(sq.Job(legacy)._assignments(), {"WT": str(wt), "BR": branch, "MSG": str(msg)})
            self.assertEqual(sq.Job(legacy)._files_from_script(), 3)

    def test_newline_in_a_value_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".context").mkdir()
            r = subprocess.run(["sh", str(ENQUEUE), "t", tmp, "fix/a\nb", "/nonexistent", "--by", "t"],
                               env=_env(tmp, Path(tmp) / ".context"), capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("contains a newline", r.stderr)


def _load_in(ctx: Path):
    """load_signq() with CONTEXT_ROOT pointed at a temp workspace's .context, like the other tests here."""
    with mock.patch.dict(os.environ, _env(ctx, ctx), clear=True):
        return load_signq()


def _seed_repo(root: Path) -> Path:
    wt = root / "repo"
    wt.mkdir()
    git_env = _env(root)  # every git call below ignores the host's own commit.gpgsign / gpg.format
    git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True, env=git_env)
    (wt / "a.txt").write_text("a\n")
    subprocess.run([*git, "add", "a.txt"], check=True, env=git_env)
    subprocess.run([*git, "commit", "-q", "-m", "chore: seed"], check=True, env=git_env)
    (wt / "a.txt").write_text("b\n")
    return wt


class LegacyMigrationIsExplicit(unittest.TestCase):
    """the migration a pre-workspace-queue kit needs (jobs/logs it queued under the kit dir) must never run as a
    side effect of some other subcommand — only an explicit `migrate-legacy` moves anything. A checkout that still
    holds a legacy `sign-queue/logs/` must not lose it to whatever queue a plain `overview`/`list` happens to resolve."""

    @staticmethod
    def _kit(tmp: Path):
        legacy = tmp / "kit" / "sign-queue"
        ctx = tmp / "ws" / ".context"
        q = ctx / "state" / "sign-queue"
        legacy.mkdir(parents=True)
        (legacy / "a.sh").write_text("job a\n")
        ctx.mkdir(parents=True)
        return legacy, ctx, q

    def test_overview_does_not_migrate_legacy_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            legacy, ctx, q = self._kit(tmp)
            sq = _load_in(ctx)
            sq.migrate_legacy.__defaults__ = (legacy, q)  # stand in for the real kit-dir / workspace-queue paths
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sq.main(["list"])  # the overview `make sign_list` runs
            self.assertEqual(rc, 0)
            self.assertTrue((legacy / "a.sh").exists(), "an overview must not move a legacy job")
            self.assertFalse(q.exists(), "an overview must not even create the new queue dir via a migration")

    def test_migrate_legacy_subcommand_moves_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            legacy, ctx, q = self._kit(tmp)
            sq = _load_in(ctx)
            sq.migrate_legacy.__defaults__ = (legacy, q)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sq.main(["migrate-legacy"])
            self.assertEqual(rc, 0)
            self.assertIn("moved 1 file", buf.getvalue())
            self.assertTrue((q / "a.sh").exists())
            self.assertFalse((legacy / "a.sh").exists())


class RunJobPushConfirmation(unittest.TestCase):
    """the drain must decide "pushed" from the job's own push confirmation, verbose or not — not from the exit
    code alone (a job that exits 0 without ever confirming a push is not proof anything was pushed)."""

    def test_verbose_still_parses_the_push_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            job = tmp / "job.sh"
            job.write_text("#!/bin/sh\necho 'pushed abc1234 G subject text'\n")
            r = sq.run_job(sq.Job(job), verbose=True, dry=False)
            self.assertEqual(r["status"], "pushed")
            self.assertEqual(r["sha"], "abc1234")
            self.assertEqual(r["sig"], "G")

    def test_exit_zero_without_a_push_confirmation_is_not_recorded_as_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            job = tmp / "job2.sh"
            job.write_text("#!/bin/sh\necho 'nothing to see here'\n")  # exits 0, never confirms a push
            r = sq.run_job(sq.Job(job), verbose=False, dry=False)
            self.assertNotEqual(r["status"], "pushed")


class PrFlagValidation(unittest.TestCase):
    """--pr must be a bare PR number — a malformed value (a stray letter, a URL fragment) used to be silently
    digit-stripped into another PR's number instead of being refused."""

    def test_build_meta_rejects_a_non_digit_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            with self.assertRaises(ValueError):
                sq.build_meta(str(tmp), "b", str(tmp / "m"), pr="12a")
            meta = sq.build_meta(str(tmp), "b", str(tmp / "m"), pr="12")
            self.assertEqual(meta["pr"], 12)
            # "none" (and any case of it) still means "no PR yet", as callers have always passed it
            for none in ("none", "None"):
                self.assertNotIn("pr_url", sq.build_meta(str(tmp), "b", str(tmp / "m"), pr=none))

    def test_enqueue_fails_when_signq_meta_rejects_a_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            msg = tmp / "msg.txt"
            msg.write_text("fix: change a\n")
            r = subprocess.run(["sh", str(ENQUEUE), "topic-b", str(wt), "main", str(msg), "--files", "a.txt",
                                "--ticket", "none", "--epic", "none", "--pr", "12a", "--summary", "s", "--by", "t"],
                               env=_env(tmp, ctx), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("signq.py meta failed", r.stderr)
            self.assertEqual(list((ctx / "state" / "sign-queue").glob("*.sh")), [], "a rejected flag must not queue a job")


class StagingRuleIsExplicit(unittest.TestCase):
    """one staging rule: explicit paths are the norm; `git add -A` only runs when asked for (`--all`) or as the
    documented, warned fallback — `--all` itself did not exist before this fix."""

    def test_all_flag_is_accepted_and_stages_everything_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            msg = tmp / "msg.txt"
            msg.write_text("fix: change a\n")
            r = subprocess.run(["sh", str(ENQUEUE), "topic-c", str(wt), "main", str(msg), "--all",
                                "--ticket", "none", "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                               env=_env(tmp, ctx), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("staging", r.stderr)
            jobs = list((ctx / "state" / "sign-queue").glob("*-topic-c.sh"))
            self.assertEqual(len(jobs), 1, r.stdout + r.stderr)
            self.assertIn("add -A", jobs[0].read_text())


class RunJobTimeout(unittest.TestCase):
    """A job script that hangs (a stuck `git push`/network call inside "sh <job>") used to block
    `run_job` forever; it must now be killed after SIGN_QUEUE_JOB_TIMEOUT and reported as a timeout
    instead of holding the drain open indefinitely — including when the hang is a GRANDCHILD (a job
    that shells out, and that in turn hangs), not just the job's own top-level "sh <job>" process."""

    def test_a_hung_grandchild_is_killed_and_the_reader_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            env = {k: v for k, v in os.environ.items() if not k.startswith("SIGN_QUEUE_")}
            with mock.patch.dict(os.environ, {**env, "CONTEXT_ROOT": str(ctx)}, clear=True):
                sq = load_signq()
            job_path = tmp / "20260101T000000Z-topic-t.sh"
            # `sh -c "sleep 999; :" &` forks a real grandchild (the trailing `:` stops the nested
            # shell from exec-replacing itself with `sleep`, the usual tail-call optimisation) —
            # `sleep 999` sits two process levels below the job's own "sh <job>", the shape a job
            # that shells out to git/gh and THAT hangs would actually have.
            job_path.write_text(
                '# META {"topic": "t"}\n'
                'sh -c "sleep 999; :" &\n'
                'wait\n'
                'echo "pushed deadbeefdeadbeef G subject"\n')
            j = sq.Job(job_path)
            os.environ["SIGN_QUEUE_JOB_TIMEOUT"] = "1"
            try:
                start = time.monotonic()
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    result = sq.run_job(j, verbose=False, dry=False)
                elapsed = time.monotonic() - start
            finally:
                os.environ.pop("SIGN_QUEUE_JOB_TIMEOUT", None)
            self.assertLess(elapsed, 6, "run_job did not return until the grandchild's own 999s sleep "
                                        "finished — the timeout did not reach the whole process tree")
            self.assertNotEqual(result["status"], "pushed")
            self.assertIn("timed out after 1s", buf.getvalue())

    def _load(self, tmp):
        ctx = Path(tmp) / "ws" / ".context"
        ctx.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("SIGN_QUEUE_")}
        with mock.patch.dict(os.environ, {**env, "CONTEXT_ROOT": str(ctx)}, clear=True):
            return load_signq()

    def test_no_pgrep_still_finds_children_or_at_least_the_root(self):
        # a minimal image without procps: `pgrep` raises FileNotFoundError; the walk must fall back to
        # /proc (or return nothing) instead of raising out of the Timer thread before any kill runs
        with tempfile.TemporaryDirectory() as tmp:
            sq = self._load(tmp)
        child = subprocess.Popen(["sh", "-c", "sleep 30; :"])
        try:
            real_run = subprocess.run
            def no_pgrep(argv, *a, **k):
                if argv and argv[0] == "pgrep":
                    raise FileNotFoundError("pgrep")
                return real_run(argv, *a, **k)
            with mock.patch.object(sq.subprocess, "run", side_effect=no_pgrep):
                kids = sq._descendants(os.getpid())
            if Path("/proc").is_dir():
                self.assertIn(child.pid, kids)
            else:
                self.assertIsInstance(kids, list)
        finally:
            child.kill(); child.wait()

    def test_kill_order_is_children_before_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            sq = self._load(tmp)
        sent = []
        with mock.patch.object(sq, "_descendants", return_value=[11, 12, 21]), \
             mock.patch.object(sq, "_alive", return_value=False), \
             mock.patch.object(sq.os, "kill", side_effect=lambda pid, sig: sent.append(pid)):
            sq._kill_tree(10, grace=0)
        self.assertEqual(sent[:4], [21, 12, 11, 10])

    def test_run_job_never_starts_a_new_session_or_process_group(self):
        # a job may need the controlling terminal for ssh-keygen -Y sign / ssh to prompt for a
        # passphrase; start_new_session (or any setpgrp/preexec_fn=os.setsid) would take that tty
        # away and turn a passphrase prompt into a silent SIGTTIN hang instead — worse than the bug
        # this file fixes. This greps the actual Popen call, not just behaviour, so a future edit
        # that re-adds either one fails here even before it can change run_job's observable timing.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Path(tmp) / "ws" / ".context"
            ctx.mkdir(parents=True)
            env = {k: v for k, v in os.environ.items() if not k.startswith("SIGN_QUEUE_")}
            with mock.patch.dict(os.environ, {**env, "CONTEXT_ROOT": str(ctx)}, clear=True):
                sq = load_signq()
        import inspect
        src = inspect.getsource(sq.run_job)
        self.assertNotIn("start_new_session", src)
        self.assertNotIn("process_group", src)
        self.assertNotIn("preexec_fn", src)


if __name__ == "__main__":
    unittest.main()
