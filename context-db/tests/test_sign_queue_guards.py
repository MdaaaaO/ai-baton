"""sign-queue guard rails: a second concurrent drain must never run (or print) anything beyond one line, the
ticket-key shape comes from this environment's own tracker config (never a hardcoded one), two jobs enqueued
in the same second get distinct names that still sort in enqueue order, and enqueue.sh's PR column tells a
real lookup failure apart from "no PR yet" and a found one. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import importlib.util
import io
import contextlib
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SIGNQ = KIT / "skills" / "sign-queue" / "signq.py"
ENQUEUE = KIT / "skills" / "sign-queue" / "enqueue.sh"
BIN = KIT / "context-db" / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402
import kit_profile  # noqa: E402
import kb  # noqa: E402


def _env(tmp, ctx=None) -> dict:
    """hermetic_env(tmp) with any ambient SIGN_QUEUE_* stripped (never leak a developer's own queue dir into
    a fixture) and ctx, when given, as CONTEXT_ROOT — the same shape test_sign_queue_home.py's fixtures use."""
    env = {k: v for k, v in hermetic_env(tmp).items() if not k.startswith("SIGN_QUEUE_")}
    if ctx is not None:
        env["CONTEXT_ROOT"] = str(ctx)
    return env


def load_signq(name: str = "signq_guards_test"):
    spec = importlib.util.spec_from_file_location(name, SIGNQ)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_in(ctx: Path):
    with mock.patch.dict(os.environ, _env(ctx, ctx), clear=True):
        return load_signq()


def _seed_repo(root: Path) -> Path:
    wt = root / "repo"
    wt.mkdir()
    git_env = _env(root)
    git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True, env=git_env)
    (wt / "a.txt").write_text("a\n")
    subprocess.run([*git, "add", "a.txt"], check=True, env=git_env)
    subprocess.run([*git, "commit", "-q", "-m", "chore: seed"], check=True, env=git_env)
    (wt / "a.txt").write_text("b\n")
    return wt


def _fake_date_bin(tmp: Path) -> Path:
    """A `date` that ignores the real clock and always answers for one fixed instant — so two enqueues run
    back to back in the test are provably "the same second" instead of hoping the real clock cooperates."""
    fakebin = tmp / "fakebin-date"
    fakebin.mkdir()
    date = fakebin / "date"
    date.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, datetime\n"
        "fmt = next((a[1:] for a in sys.argv[1:] if a.startswith('+')), None)\n"
        "now = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)\n"
        "if fmt is None:\n"
        "    print(now.strftime('%a %b %d %H:%M:%S UTC %Y'))\n"
        "else:\n"
        "    print(now.strftime(fmt.replace('%F', '%Y-%m-%d').replace('%T', '%H:%M:%S')))\n"
    )
    date.chmod(0o755)
    return fakebin


def _fake_gh_bin(tmp: Path) -> Path:
    """A `gh` whose single behaviour knob is $FAKE_GH_MODE: found/none/failed — so the PR column's three
    outcomes can be driven without a real GitHub lookup."""
    fakebin = tmp / "fakebin-gh"
    fakebin.mkdir()
    gh = fakebin / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        "case \"$FAKE_GH_MODE\" in\n"
        "  found) echo '[{\"number\":7,\"url\":\"https://example.invalid/pull/7\",\"title\":\"t\",\"isDraft\":false}]'; exit 0;;\n"
        "  none) echo '[]'; exit 0;;\n"
        "  failed) echo 'gh: authentication required' >&2; exit 1;;\n"
        "  *) echo \"fake gh: unset FAKE_GH_MODE\" >&2; exit 2;;\n"
        "esac\n"
    )
    gh.chmod(0o755)
    return fakebin


class DrainLock(unittest.TestCase):
    """`make sign` holds an exclusive lock on the queue for the whole drain — a second `run` started while the
    first still holds it must print exactly one line and return 0, never the overview table, and never touch
    a job file."""

    def test_second_drain_sees_the_lock_held_and_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.Q.mkdir(parents=True, exist_ok=True)
            job = sq.Q / "20260101T000000Z-000-t.sh"
            job.write_text('# META {"topic": "t"}\necho "pushed deadbeefdeadbeef G subject"\n')
            held = open(sq.LOCK, "a+")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = sq.main(["run"])
            finally:
                fcntl.flock(held.fileno(), fcntl.LOCK_UN)
                held.close()
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("another drain already holds the lock", out)
            # never got far enough to print the overview table or touch the pending job
            self.assertNotIn("nothing to sign", out)
            self.assertNotIn("pushed", out)
            self.assertTrue(job.exists(), "a locked-out drain must not touch the job file")

    def test_an_uncontended_drain_still_runs_normally(self):
        # sanity check on the fixture: the SAME lock file, unheld, must not block a normal drain
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            sq = _load_in(ctx)
            sq.Q.mkdir(parents=True, exist_ok=True)
            job = sq.Q / "20260101T000000Z-000-t.sh"
            job.write_text('# META {"topic": "t"}\necho "pushed deadbeefdeadbeef G subject"\n')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sq.main(["run"])
            self.assertEqual(rc, 0)
            self.assertIn("pushed", buf.getvalue())
            self.assertFalse(job.exists(), "a successful push must still remove the job file")


class KeyRegexSource(unittest.TestCase):
    """The ticket-key shape is this environment's own `tracker.key_regex` (the same source
    commit_style.py's key_regex() reads), never a hardcoded Jira-like constant — no configured regex means
    no key matching, not a fallback shape."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        env_dir = self.root / "reference" / "env"
        self._saved = (kit_profile.ENV_DIR, kb.ENV)
        kit_profile.ENV_DIR = env_dir
        kb.ENV = env_dir
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        kb.init_blank()

    def tearDown(self):
        kit_profile.ENV_DIR, kb.ENV = self._saved
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.tmp.cleanup()

    def _signq(self):
        ctx = Path(self.tmp.name) / "ws" / ".context"
        ctx.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict(os.environ, {**os.environ, "CONTEXT_ROOT": str(ctx)}):
            return load_signq()

    def test_no_configured_regex_means_no_key_matching(self):
        sq = self._signq()
        self.assertIsNone(sq.key_regex())
        self.assertEqual(sq.ticket_of("feat/key-123-thing", "key-123-thing", "add the thing"), "")

    def test_configured_regex_is_read_from_tracker_key_regex(self):
        kb.save_config({**kb.load_config(), "tracker": {"kind": "github", "key_regex": r"\b(KEY-\d+)\b"}})
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        sq = self._signq()
        self.assertIsNotNone(sq.key_regex())
        self.assertEqual(sq.ticket_of("feat/key-123-thing", "key-123-thing", "add the thing"), "KEY-123")

    def test_an_unparseable_configured_regex_also_means_no_key_matching(self):
        kb.save_config({**kb.load_config(), "tracker": {"kind": "github", "key_regex": "(unclosed"}})
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        sq = self._signq()
        self.assertIsNone(sq.key_regex())

    def _issue_number_regex(self):
        kb.save_config({**kb.load_config(), "tracker": {"kind": "github", "key_regex": r"(?:^|[^\w/])#(\d+)\b"}})
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        return self._signq()

    def test_the_key_is_the_capture_group_not_the_text_the_regex_anchors_on(self):
        sq = self._issue_number_regex()
        self.assertEqual(sq.ticket_of("fix/the-thing", "the-thing", "fix: the thing (#123)"), "123")
        self.assertEqual(sq.ticket_of("#123 leads the subject"), "123")

    def test_a_bare_number_finds_its_epic_only_where_it_is_written_as_a_reference(self):
        sq = self._issue_number_regex()
        docs = sq.CONTEXT / "kit"
        docs.mkdir(parents=True)
        (docs / "right.md").write_text('---\ntype: epic\ntitle: "#7 — the plan"\n---\nsteps: #123, then #123 again\n')
        (docs / "wrong.md").write_text('---\ntype: epic\ntitle: "#8 — something else"\n---\n'
                                       'v0.123 shipped; 1123 rows; see #1123 and run 5123\n')
        self.assertEqual(sq.epic_of("123").get("epic"), "7")


class UniqueJobNames(unittest.TestCase):
    """A second-resolution timestamp alone collides when two jobs are enqueued within the same second — the
    older job file used to be silently overwritten. A zero-padded sequence suffix after the timestamp must
    make both names unique while keeping them sortable in enqueue order."""

    def test_two_enqueues_in_the_same_second_get_distinct_sortable_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            fakebin = _fake_date_bin(tmp)
            env = _env(tmp, ctx)
            env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
            jobs = []
            for i in range(3):
                msg = tmp / f"msg{i}.txt"
                msg.write_text(f"fix: change a ({i})\n")
                r = subprocess.run(["sh", str(ENQUEUE), "same-sec", str(wt), "main", str(msg), "--files", "a.txt",
                                    "--ticket", "none", "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                                   env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                jobs.append(Path(r.stdout.strip().splitlines()[-1]))
            self.assertEqual(len(set(jobs)), 3, "three same-second enqueues must not collide on one job file")
            names = [p.name for p in jobs]
            self.assertEqual(names, sorted(names), "job names must already sort in the order they were enqueued")
            for p in jobs:
                self.assertTrue(p.exists())
            self.assertEqual(list(ctx.rglob("*.claim")), [], "no .claim marker should survive a finished enqueue")

    def test_a_parked_job_keeps_its_name(self):
        # `sign_retry` renames `<job>.sh.failed` back to `<job>.sh`: a new job must never take that name
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            fakebin = _fake_date_bin(tmp)
            env = _env(tmp, ctx)
            env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]

            def enqueue(i):
                msg = tmp / f"msg{i}.txt"
                msg.write_text(f"fix: change a ({i})\n")
                r = subprocess.run(["sh", str(ENQUEUE), "parked", str(wt), "main", str(msg), "--files", "a.txt",
                                    "--ticket", "none", "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                                   env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                return Path(r.stdout.strip().splitlines()[-1])

            first = enqueue(0)
            parked = first.with_name(first.name + ".failed")
            first.rename(parked)
            second = enqueue(1)
            self.assertNotEqual(second.name, first.name)
            self.assertTrue(parked.exists())
            self.assertEqual(list(ctx.rglob("*.claim")), [], "no .claim marker should survive a finished enqueue")

    def test_job_names_still_glob_as_plain_sh_files(self):
        # the sequence suffix must not change what signq.py's own `*.sh` glob picks up
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            msg = tmp / "msg.txt"
            msg.write_text("fix: change a\n")
            env = _env(tmp, ctx)
            r = subprocess.run(["sh", str(ENQUEUE), "glob-check", str(wt), "main", str(msg), "--files", "a.txt",
                                "--ticket", "none", "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                               env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sq = _load_in(ctx)
            jobs = sq.load_jobs()
            self.assertEqual([j.topic for j in jobs], ["glob-check"])


class PrLookupDisplay(unittest.TestCase):
    """enqueue.sh's printed "queued ..." line must tell three outcomes apart: a PR that gh actually found, a
    clean "none yet", and a lookup that failed outright (gh errored) — never folding the last two together."""

    def _enqueue_without_pr(self, tmp: Path, mode: str) -> str:
        ctx = tmp / "ws" / ".context"
        ctx.mkdir(parents=True)
        origin = tmp / "placeholder-owner" / "placeholder-repo.git"
        origin.parent.mkdir(parents=True)
        git_env = _env(tmp)
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, env=git_env)
        wt = tmp / "ws" / "repo"
        wt.mkdir(parents=True)
        git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
        subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True, env=git_env)
        subprocess.run([*git, "remote", "add", "origin", str(origin)], check=True, env=git_env)
        (wt / "a.txt").write_text("a\n")
        subprocess.run([*git, "add", "a.txt"], check=True, env=git_env)
        subprocess.run([*git, "commit", "-q", "-m", "chore: seed"], check=True, env=git_env)
        (wt / "a.txt").write_text("b\n")
        msg = tmp / "msg.txt"
        msg.write_text("fix: change a\n")
        fakebin = _fake_gh_bin(tmp)
        env = _env(tmp, ctx)
        env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
        env["FAKE_GH_MODE"] = mode
        r = subprocess.run(["sh", str(ENQUEUE), "pr-disp", str(wt), "main", str(msg), "--files", "a.txt",
                            "--ticket", "none", "--epic", "none", "--summary", "s", "--by", "t"],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stderr

    def test_pr_found_shows_its_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = self._enqueue_without_pr(Path(tmp), "found")
        self.assertIn("#7", err)
        self.assertNotIn("lookup failed", err)

    def test_no_open_pr_reads_as_none_yet_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = self._enqueue_without_pr(Path(tmp), "none")
        self.assertIn("no PR yet", err)
        self.assertNotIn("lookup failed", err)

    def test_a_failed_lookup_is_never_shown_as_no_pr_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = self._enqueue_without_pr(Path(tmp), "failed")
        self.assertIn("PR lookup failed", err)
        self.assertNotIn("no PR yet", err)


def _fake_git_bin(tmp: Path) -> Path:
    """A `git` that answers `ls-remote` from $FAKE_LS_REMOTE (fail = exit 128 with an error on stderr, noisy =
    a warning on stderr plus $FAKE_TIP as the branch tip, exit 0) and hands every other call to the real git."""
    fakebin = tmp / "fakebin-git"
    fakebin.mkdir()
    git = fakebin / "git"
    git.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do\n"
        "  if [ \"$a\" = ls-remote ]; then\n"
        "    case \"$FAKE_LS_REMOTE\" in\n"
        "      fail) echo \"fatal: unable to access 'https://example.invalid/': Could not resolve host\" >&2; exit 128;;\n"
        "      noisy) echo 'warning: redirecting to https://example.invalid/new.git/' >&2\n"
        "             printf '%s\\trefs/heads/main\\n' \"$FAKE_TIP\"; exit 0;;\n"
        "    esac\n"
        "  fi\n"
        "done\n"
        f"exec {shutil.which('git')} \"$@\"\n"
    )
    git.chmod(0o755)
    return fakebin


def _clean_repo_one_ahead(root: Path):
    """A clean worktree with two commits and no remote-tracking ref; returns (worktree, sha of the first commit)."""
    wt = root / "repo"
    wt.mkdir()
    git_env = _env(root)
    git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True, env=git_env)
    for content in ("a\n", "b\n"):
        (wt / "a.txt").write_text(content)
        subprocess.run([*git, "add", "a.txt"], check=True, env=git_env)
        subprocess.run([*git, "commit", "-q", "-m", "chore: seed"], check=True, env=git_env)
    first = subprocess.run([*git, "rev-parse", "HEAD~1"], check=True, env=git_env, capture_output=True, text=True).stdout.strip()
    return wt, first


class EnqueueStops(unittest.TestCase):
    """enqueue.sh stops with a message that names what failed: the style tool, the remote lookup or the
    metadata call — and none of them leaves a job, a claim marker or a temp file behind."""

    def _run(self, tmp: Path, wt: Path, msg_text: str, *extra: str, env_extra=None, files=True):
        ctx = tmp / "ws" / ".context"
        ctx.mkdir(parents=True, exist_ok=True)
        msg = tmp / "msg.txt"
        msg.write_text(msg_text)
        env = _env(tmp, ctx)
        env.update(env_extra or {})
        args = ["sh", str(ENQUEUE), "stops", str(wt), "main", str(msg)]
        if files:
            args += ["--files", "a.txt"]
        args += ["--ticket", "none", "--epic", "none", "--summary", "s", "--by", "t", *extra]
        r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
        return r, ctx

    def assert_nothing_left(self, tmp: Path, ctx: Path):
        self.assertEqual(list(ctx.rglob("*.sh")), [], "a stopped enqueue must not queue a job")
        self.assertEqual(list(ctx.rglob("*.claim")) + list(ctx.rglob("*.tmp.*")), [])
        self.assertEqual(list(tmp.glob("tmp.*")), [], "the stderr capture file must be removed")

    def test_a_style_tool_that_cannot_run_is_not_reported_as_a_style_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ws").mkdir()
            wt = _seed_repo(tmp / "ws")
            (wt / ".claude").mkdir()
            (wt / ".claude" / "commit-style").write_text("shouty\n")  # not a style the tool knows
            r, ctx = self._run(tmp, wt, "fix: change a\n", "--pr", "1")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("commit_style.py check failed to run", r.stderr)
            self.assertNotIn("does not follow the repo's commit style", r.stderr)
            self.assert_nothing_left(tmp, ctx)

    def test_a_style_violation_still_says_fix_the_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ws").mkdir()
            wt = _seed_repo(tmp / "ws")
            r, ctx = self._run(tmp, wt, "Fixed things\n", "--pr", "1")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("does not follow the repo's commit style", r.stderr)
            self.assertNotIn("failed to run", r.stderr)
            self.assert_nothing_left(tmp, ctx)

    def test_a_failed_remote_lookup_stops_with_gits_own_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ws").mkdir()
            wt, _first = _clean_repo_one_ahead(tmp / "ws")
            fakebin = _fake_git_bin(tmp)
            env_extra = {"PATH": str(fakebin) + os.pathsep + os.environ["PATH"], "FAKE_LS_REMOTE": "fail"}
            r, ctx = self._run(tmp, wt, "fix: change a\n", "--pr", "1", env_extra=env_extra, files=False)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("git ls-remote origin main failed", r.stderr)
            self.assertIn("Could not resolve host", r.stderr)
            self.assertNotIn("nothing to commit", r.stderr)
            self.assert_nothing_left(tmp, ctx)

    def test_a_warning_on_a_successful_remote_lookup_does_not_refuse_a_push_only_retry(self):
        # git and ssh write to stderr on success too (a redirect warning, a new known-hosts line)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ws").mkdir()
            wt, first = _clean_repo_one_ahead(tmp / "ws")
            fakebin = _fake_git_bin(tmp)
            env_extra = {"PATH": str(fakebin) + os.pathsep + os.environ["PATH"], "FAKE_LS_REMOTE": "noisy",
                         "FAKE_TIP": first}
            r, ctx = self._run(tmp, wt, "fix: change a\n", "--pr", "1", env_extra=env_extra, files=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("HEAD is 1 commit(s) ahead", r.stderr)
            self.assertEqual(len(list(ctx.rglob("*.sh"))), 1)
            self.assertEqual(list(tmp.glob("tmp.*")), [], "the stderr capture file must be removed")

    def test_a_rejected_metadata_flag_stops_the_enqueue(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ws").mkdir()
            wt = _seed_repo(tmp / "ws")
            r, ctx = self._run(tmp, wt, "fix: change a\n", "--pr", "abc")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("--pr expects a bare PR number", r.stderr)
            self.assertIn("signq.py meta failed", r.stderr)
            self.assert_nothing_left(tmp, ctx)

    def test_a_staged_deletion_is_matched_as_a_literal_path(self):
        # `b.txt` must not pass because a staged deletion named `bXtxt` matches it as a pattern
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ws").mkdir()
            wt = _seed_repo(tmp / "ws")
            git_env = _env(tmp)
            git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
            (wt / "bXtxt").write_text("x\n")
            subprocess.run([*git, "add", "bXtxt"], check=True, env=git_env)
            subprocess.run([*git, "commit", "-q", "-m", "chore: add"], check=True, env=git_env)
            subprocess.run([*git, "rm", "-q", "bXtxt"], check=True, env=git_env)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            msg = tmp / "msg.txt"
            msg.write_text("fix: change a\n")

            def enqueue(path):
                return subprocess.run(["sh", str(ENQUEUE), "del", str(wt), "main", str(msg), "--files", path, "--ticket",
                                       "none", "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                                      env=_env(tmp, ctx), capture_output=True, text=True, timeout=60)

            wrong = enqueue("b.txt")
            self.assertEqual(wrong.returncode, 2, wrong.stdout + wrong.stderr)
            self.assertIn("b.txt is neither in the worktree nor tracked", wrong.stderr)
            right = enqueue("bXtxt")
            self.assertEqual(right.returncode, 0, right.stdout + right.stderr)
            self.assertIn("bXtxt is an already-staged deletion", right.stderr)


class DrainSurvives(unittest.TestCase):
    """One odd job never costs the rest of the drain: a job file that vanishes is skipped, a drain that raised
    gives the lock back, and the "remote moved" hint needs git's own rejection line."""

    def _queue(self, tmp: Path):
        ctx = tmp / "ws" / ".context"
        ctx.mkdir(parents=True)
        sq = _load_in(ctx)
        sq.Q.mkdir(parents=True, exist_ok=True)
        return sq

    def _drain(self, sq, rc: int = 0) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = sq.main(["run"])
        self.assertEqual(got, rc, buf.getvalue())
        return buf.getvalue()

    def test_a_job_file_that_disappears_mid_drain_is_skipped_and_the_summary_still_prints(self):
        with tempfile.TemporaryDirectory() as tmp:
            sq = self._queue(Path(tmp))
            second = sq.Q / "20260101T000000Z-001-b.sh"
            second.write_text('# META {"topic": "b"}\necho "pushed feedfacefeedface G second"\n')
            first = sq.Q / "20260101T000000Z-000-a.sh"
            first.write_text(f'# META {{"topic": "a"}}\nrm -f "{second}"\necho "pushed deadbeefdeadbeef G first"\n')
            out = self._drain(sq)
            self.assertIn("job file disappeared mid-drain", out)
            self.assertIn("summary", out)
            self.assertIn("1 pushed", out)
            self.assertNotIn("feedface", out)
            self.assertFalse(first.exists())

    def test_the_lock_is_free_again_after_a_drain_that_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            sq = self._queue(Path(tmp))
            with mock.patch.object(sq, "_drain", side_effect=RuntimeError("boom")):
                try:
                    sq.cmd_run([])
                    self.fail("the drain should have raised")
                except RuntimeError:
                    # the traceback still holds cmd_run's frame here, so only an explicit release frees the lock
                    probe = open(sq.LOCK, "a+")
                    try:
                        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        self.fail("the lock is still held after the drain raised")
                    finally:
                        probe.close()

    def test_the_remote_moved_hint_needs_gits_own_rejection_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            sq = self._queue(Path(tmp))
            job = sq.Q / "20260101T000000Z-000-a.sh"
            job.write_text('# META {"topic": "a"}\necho " ! [rejected]        main -> main (some reason)"\nexit 1\n')
            self.assertIn("remote moved", self._drain(sq, rc=1))
        with tempfile.TemporaryDirectory() as tmp:
            sq = self._queue(Path(tmp))
            job = sq.Q / "20260101T000000Z-000-a.sh"
            job.write_text('# META {"topic": "a"}\necho "fix: rejected payments are retried"\nexit 1\n')
            out = self._drain(sq, rc=1)
            self.assertIn("FAILED", out)
            self.assertNotIn("remote moved", out)

    def test_a_topic_that_starts_with_a_number_survives_a_job_name_without_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            sq = self._queue(Path(tmp))
            (sq.Q / "20260101T000000Z-46-fix.sh").write_text("echo legacy\n")
            (sq.Q / "20260101T000001Z-000-46-fix.sh").write_text("echo current\n")
            (sq.Q / "20260101T000002Z-plain.sh").write_text("echo legacy\n")
            self.assertEqual([j.topic for j in sq.load_jobs()], ["46-fix", "46-fix", "plain"])


@unittest.skipUnless(shutil.which("make"), "make not installed")
class WorkspaceMkSignGate(unittest.TestCase):
    """The host-side `make sign*` targets carry the same not-applicable gate as the session side: a machine
    where `systems.signed_commits` is false must never shell out to signq.py at all."""

    def _ws(self, tmp: Path, signed: bool):
        ws = tmp / "ws"
        (ws / ".context").mkdir(parents=True)
        (ws / "baton").symlink_to(KIT, target_is_directory=True)
        (ws / "Makefile").write_text("include baton/workspace.mk\n", encoding="utf-8")
        ctx = ws / ".context"
        env = {**os.environ, "CONTEXT_ROOT": str(ctx)}
        kbpy = str(BIN / "kb.py")
        subprocess.run([sys.executable, kbpy, "init", "--blank"], env=env, check=True, capture_output=True, text=True)
        subprocess.run([sys.executable, kbpy, "config-set", "systems.signed_commits", "true" if signed else "false"],
                       env=env, check=True, capture_output=True, text=True)
        return ws, env

    def test_sign_list_is_not_applicable_when_signed_commits_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, env = self._ws(Path(tmp), signed=False)
            r = subprocess.run([shutil.which("make"), "sign_list"], cwd=ws, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("sign-queue: not applicable here — signed_commits is false", r.stdout)

    def test_sign_list_runs_signq_when_signed_commits_is_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, env = self._ws(Path(tmp), signed=True)
            r = subprocess.run([shutil.which("make"), "sign_list"], cwd=ws, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("not applicable here", r.stdout + r.stderr)
        self.assertIn("queue empty", r.stdout)  # the real signq.py `list` ran, on an empty queue

    def test_an_unreadable_store_stops_with_the_error_never_as_not_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, env = self._ws(Path(tmp), signed=True)
            (ws / ".context" / "reference" / "env" / "config.json").write_text("{broken", encoding="utf-8")
            r = subprocess.run([shutil.which("make"), "sign_list"], cwd=ws, env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("sign-queue: systems.signed_commits could not be read", r.stderr)
        self.assertIn("invalid JSON", r.stderr)
        self.assertNotIn("not applicable here", r.stdout + r.stderr)

    def test_no_env_store_at_all_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, env = self._ws(Path(tmp), signed=True)
            shutil.rmtree(ws / ".context" / "reference" / "env")
            r = subprocess.run([shutil.which("make"), "sign_list"], cwd=ws, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("sign-queue: not applicable here — signed_commits is false", r.stdout)

    def test_sign_show_without_job_still_gates_first(self):
        # the usage check (`JOB=<index|topic>`) must not run before the signed_commits gate
        with tempfile.TemporaryDirectory() as tmp:
            ws, env = self._ws(Path(tmp), signed=False)
            r = subprocess.run([shutil.which("make"), "sign_show"], cwd=ws, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not applicable here", r.stdout)
        self.assertNotIn("usage:", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
