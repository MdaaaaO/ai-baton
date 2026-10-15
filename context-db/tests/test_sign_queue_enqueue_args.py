"""sign-queue enqueue.sh argument handling: --files may be given once, as the legacy space-separated
string, or repeated (one path per flag) — a repeated flag never splits, so it is the only way to stage two
paths where either one contains a space; a lone --files value is kept whole when it already names a real
path and split on whitespace otherwise, so the legacy single-string shape still works for existing callers.
--by (or SIGN_QUEUE_BY) is required outright: a call with neither exits 2 with a one-line usage message. The
queue directory's writability is checked once, up front, with the real OS error, instead of being inferred
from a claim loop exhausting its tries. Stdlib unittest, real git (no signing key needed — these tests never
run the generated job, only enqueue.sh's own argument handling and the job script it writes).
Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
ENQUEUE = KIT / "skills" / "sign-queue" / "enqueue.sh"
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402


def _env(tmp, ctx=None) -> dict:
    """hermetic_env(tmp) with any ambient SIGN_QUEUE_* stripped, so a developer's own sign-queue env (or
    queue dir) can never leak into a fixture; ctx, when given, becomes CONTEXT_ROOT."""
    env = {k: v for k, v in hermetic_env(tmp).items() if not k.startswith("SIGN_QUEUE_")}
    if ctx is not None:
        env["CONTEXT_ROOT"] = str(ctx)
    return env


def _seed_repo(root: Path) -> Path:
    """One committed file, so the worktree has a HEAD and `git status --short` is clean until a test adds
    or modifies something — the fixture every test below builds its own files on top of."""
    wt = root / "repo"
    wt.mkdir()
    git_env = _env(root)
    git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True, env=git_env)
    (wt / "seed.txt").write_text("seed\n")
    subprocess.run([*git, "add", "seed.txt"], check=True, env=git_env)
    subprocess.run([*git, "commit", "-q", "-m", "chore: seed"], check=True, env=git_env)
    return wt


def _track(root: Path, wt: Path, *names: str) -> None:
    """Commit `names` and then change each one, so a pattern that reached git as a pathspec would have
    tracked, modified files to match."""
    git_env = _env(root)
    git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
    for n in names:
        (wt / n).write_text("v1\n")
    subprocess.run([*git, "add", "--", *names], check=True, env=git_env)
    subprocess.run([*git, "commit", "-q", "-m", "chore: more files"], check=True, env=git_env)
    for n in names:
        (wt / n).write_text("v2\n")


def _run_staging(root: Path, wt: Path, job: Path) -> list[str]:
    """Run only the job's staging lines (never its commit or push) and return what ended up staged."""
    keep = [ln for ln in job.read_text().splitlines()
            if ln.startswith(("WT=", "GIT_LITERAL_PATHSPECS=", 'git -C "$WT" add --'))]
    subprocess.run(["sh", "-c", "set -eu\n" + "\n".join(keep)], check=True, env=_env(root), timeout=60)
    r = subprocess.run(["git", "-C", str(wt), "diff", "--cached", "--name-only"], check=True, env=_env(root),
                       capture_output=True, text=True, timeout=60)
    return sorted(r.stdout.splitlines())


def _enqueue(tmp: Path, wt: Path, ctx: Path, topic: str, *extra: str, env_extra=None):
    msg = tmp / f"{topic}-msg.txt"
    msg.write_text("fix: change things\n")
    env = _env(tmp, ctx)
    env.update(env_extra or {})
    args = ["sh", str(ENQUEUE), topic, str(wt), "main", str(msg), "--ticket", "none", "--epic", "none",
            "--pr", "1", "--summary", "s", *extra]
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)


class RepeatedFiles(unittest.TestCase):
    """--files given more than once: each occurrence is staged as exactly the path given, spaces and all —
    never split on whitespace."""

    def test_two_files_flags_are_both_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            (wt / "a.txt").write_text("a\n")
            (wt / "b.txt").write_text("b\n")
            r = _enqueue(tmp, wt, ctx, "two-flags", "--files", "a.txt", "--files", "b.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            job = Path(r.stdout.strip().splitlines()[-1])
            self.assertEqual(self._staged(job), ["a.txt", "b.txt"])

    def test_a_space_in_one_of_two_flags_is_never_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            (wt / "a b.txt").write_text("ab\n")
            (wt / "c.txt").write_text("c\n")
            r = _enqueue(tmp, wt, ctx, "space-flag", "--files", "a b.txt", "--files", "c.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            job = Path(r.stdout.strip().splitlines()[-1])
            # never split into "a" and "b.txt"
            self.assertEqual(self._staged(job), ["a b.txt", "c.txt"])

    def test_a_glob_character_in_a_repeated_flag_is_never_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            _track(tmp / "ws", wt, "a1.txt", "a2.txt")  # tracked and modified: what a pathspec would match
            (wt / "c.txt").write_text("c\n")
            msg = tmp / "glob-msg.txt"
            msg.write_text("fix: change things\n")
            # run from inside the worktree: a pattern expanded by the shell would match a1.txt there too
            r = subprocess.run(["sh", str(ENQUEUE), "glob", str(wt), "main", str(msg), "--ticket", "none",
                                "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t",
                                "--files", "a*.txt", "--files", "c.txt"],
                               env=_env(tmp, ctx), cwd=wt, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("a*.txt is neither in the worktree nor tracked", r.stderr)

    def test_a_file_named_like_a_pattern_stages_only_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            _track(tmp / "ws", wt, "f1.txt")  # `f[1].txt` read as a pattern matches this one
            (wt / "f[1].txt").write_text("literal\n")
            (wt / "c.txt").write_text("c\n")
            r = _enqueue(tmp, wt, ctx, "literal", "--files", "f[1].txt", "--files", "c.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            job = Path(r.stdout.strip().splitlines()[-1])
            self.assertEqual(_run_staging(tmp / "ws", wt, job), ["c.txt", "f[1].txt"])

    def test_pathspec_magic_is_a_file_name_not_a_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            _track(tmp / "ws", wt, "a1.txt")
            (wt / "c.txt").write_text("c\n")
            r = _enqueue(tmp, wt, ctx, "magic", "--files", ":(glob)a*.txt", "--files", "c.txt", "--by", "t")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn(":(glob)a*.txt is neither in the worktree nor tracked", r.stderr)

    def test_a_backslash_in_a_path_reaches_the_job_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            name = "a\\tb.txt"  # a backslash and a t, not a TAB
            (wt / name).write_text("x\n")
            (wt / "c.txt").write_text("c\n")
            r = _enqueue(tmp, wt, ctx, "backslash", "--files", name, "--files", "c.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            job = Path(r.stdout.strip().splitlines()[-1])
            self.assertEqual(self._staged(job), [name, "c.txt"])

    def test_a_value_flag_given_last_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            (wt / "c.txt").write_text("c\n")
            for flag in ("--files", "--by"):
                with self.subTest(flag=flag):
                    r = _enqueue(tmp, wt, ctx, "dangling", "--by", "t", flag)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn(f"{flag} needs a value", r.stderr)

    def test_an_empty_files_value_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            (wt / "a.txt").write_text("a\n")
            r = _enqueue(tmp, wt, ctx, "empty-files", "--files", "", "--by", "t")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("--files needs a path", r.stderr)

    @staticmethod
    def _add_line(job: Path) -> str:
        lines = [ln for ln in job.read_text().splitlines() if ln.startswith('git -C "$WT" add --')]
        assert len(lines) == 1, lines
        return lines[0]

    @classmethod
    def _staged(cls, job: Path) -> list[str]:
        """The exact list of paths the job stages, parsed with shlex (not a naive whitespace split) so a
        quoted token containing a space is one list entry, not two."""
        return shlex.split(cls._add_line(job).split("--", 1)[-1])


class LegacySingleFilesString(unittest.TestCase):
    """Exactly one --files value is the legacy space-separated shape: kept whole when it already names a
    real path (so a lone path with a space still works), split on whitespace otherwise (so two existing
    paths joined by a space — the pre-existing calling convention — still both get staged)."""

    def test_a_path_with_a_space_is_staged_as_one_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            (wt / "a b.txt").write_text("ab\n")
            r = _enqueue(tmp, wt, ctx, "one-path-space", "--files", "a b.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            job = Path(r.stdout.strip().splitlines()[-1])
            self.assertEqual(RepeatedFiles._staged(job), ["a b.txt"])

    def test_two_existing_paths_joined_by_a_space_still_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            (wt / "a.txt").write_text("a\n")
            (wt / "b.txt").write_text("b\n")
            r = _enqueue(tmp, wt, ctx, "legacy-split", "--files", "a.txt b.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            job = Path(r.stdout.strip().splitlines()[-1])
            self.assertEqual(RepeatedFiles._staged(job), ["a.txt", "b.txt"])


class ByIsRequired(unittest.TestCase):
    """A call with neither --by nor SIGN_QUEUE_BY exits 2 with a one-line usage message — no fallback to any
    workspace identity."""

    def test_missing_by_and_no_env_var_exits_2_with_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            env = _env(tmp, ctx)
            self.assertNotIn("SIGN_QUEUE_BY", env)
            r = subprocess.run(["sh", str(ENQUEUE), "t", "/nonexistent/wt", "branch", "/nonexistent/msg"],
                               env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1, r.stderr)
            self.assertIn("usage:", lines[0])
            self.assertIn("--by", lines[0])

    def test_sign_queue_by_env_var_satisfies_the_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            (wt / "a.txt").write_text("a\n")
            r = _enqueue(tmp, wt, ctx, "env-by", "--files", "a.txt", env_extra={"SIGN_QUEUE_BY": "t"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


def _with_origin(root: Path, push: bool = True) -> tuple[Path, Path]:
    """_seed_repo plus a bare `origin` under root; `push` publishes main there first. Returns (wt, origin)."""
    wt = _seed_repo(root)
    origin = root / "origin.git"
    git_env = _env(root)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, env=git_env)
    subprocess.run(["git", "-C", str(wt), "remote", "add", "origin", str(origin)], check=True, env=git_env)
    if push:
        subprocess.run(["git", "-C", str(wt), "push", "-q", "origin", "main"], check=True, env=git_env)
    return wt, origin


def _move_origin(root: Path, origin: Path) -> None:
    """Someone else pushes to origin/main (a GitHub "Update branch" click): the new tip is not in the worktree."""
    git_env = _env(root)
    other = root / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, env=git_env)
    (other / "theirs.txt").write_text("theirs\n")
    subprocess.run(["git", "-C", str(other), "add", "theirs.txt"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(other), "commit", "-q", "-m", "chore: theirs"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True, env=git_env)


def _job_lines(r) -> list[str]:
    return Path(r.stdout.strip().splitlines()[-1]).read_text().splitlines()


class RemoteDecidesFlags(unittest.TestCase):
    """enqueue.sh reads the remote itself (#50): an absent `origin/<branch>` means --new-branch, a remote tip
    that is not in HEAD's history means --rebase, and a worktree in the middle of a rebase is refused. The
    explicit flags still force their behaviour on."""

    def _setup(self, tmp: Path, push: bool = True):
        ctx = tmp / "ws" / ".context"
        ctx.mkdir(parents=True)
        wt, origin = _with_origin(tmp / "ws", push=push)
        (wt / "a.txt").write_text("a\n")
        return ctx, wt, origin

    def test_a_branch_missing_on_the_remote_gets_new_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt, _ = self._setup(tmp, push=False)
            r = _enqueue(tmp, wt, ctx, "auto-new", "--files", "a.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = _job_lines(r)
            self.assertIn('git -C "$WT" push -u origin "$BR"', lines)
            self.assertNotIn('git -C "$WT" rebase -S -f FETCH_HEAD', lines)

    def test_a_remote_tip_outside_head_gets_rebase(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt, origin = self._setup(tmp)
            _move_origin(tmp / "ws", origin)
            r = _enqueue(tmp, wt, ctx, "auto-rebase", "--files", "a.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = _job_lines(r)
            self.assertIn('git -C "$WT" rebase -S -f FETCH_HEAD', lines)
            self.assertIn('git -C "$WT" push origin "$BR"', lines)

    def test_a_remote_tip_already_in_head_needs_neither_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt, _ = self._setup(tmp)
            r = _enqueue(tmp, wt, ctx, "plain", "--files", "a.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = _job_lines(r)
            self.assertIn('git -C "$WT" push origin "$BR"', lines)
            self.assertNotIn('git -C "$WT" rebase -S -f FETCH_HEAD', lines)

    def test_explicit_rebase_still_forces_the_rebase(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt, _ = self._setup(tmp)
            r = _enqueue(tmp, wt, ctx, "forced", "--files", "a.txt", "--by", "t", "--rebase")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('git -C "$WT" rebase -S -f FETCH_HEAD', _job_lines(r))

    def test_force_with_lease_is_never_turned_into_a_rebase(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt, origin = self._setup(tmp)
            _move_origin(tmp / "ws", origin)
            tip = subprocess.run(["git", "--git-dir", str(origin), "rev-parse", "main"], check=True,
                                 env=_env(tmp), capture_output=True, text=True).stdout.strip()
            r = _enqueue(tmp, wt, ctx, "lease", "--files", "a.txt", "--by", "t", "--force-with-lease", tip)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn('git -C "$WT" rebase -S -f FETCH_HEAD', _job_lines(r))

    def test_a_rebase_in_progress_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt, _ = self._setup(tmp)
            git_dir = subprocess.run(["git", "-C", str(wt), "rev-parse", "--absolute-git-dir"], check=True,
                                     env=_env(tmp), capture_output=True, text=True).stdout.strip()
            for d in ("rebase-merge", "rebase-apply"):
                with self.subTest(state=d):
                    (Path(git_dir) / d).mkdir()
                    try:
                        r = _enqueue(tmp, wt, ctx, "mid-rebase", "--files", "a.txt", "--by", "t")
                    finally:
                        (Path(git_dir) / d).rmdir()
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn("rebase is in progress", r.stderr)
                    self.assertEqual(list(ctx.rglob("*.sh")), [], "a refused enqueue must not queue a job")


def _git(root: Path, wt: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t", *args], check=True,
                          env=_env(root), capture_output=True, text=True).stdout.strip()


# an SSH remote this process has no identity for: what ls-remote meets in the session the queue exists for
_NO_SSH_IDENTITY = {"GIT_SSH_COMMAND": "sh -c 'echo Permission denied \\(publickey\\) >&2; exit 255'"}
_PLAIN_REBASE = 'git -C "$WT" rebase -S -f FETCH_HEAD'


class RemoteDecisionGuards(unittest.TestCase):
    """The guards behind RemoteDecidesFlags (#50 review): a remote that cannot be reached from here never
    refuses a dirty enqueue, a history rewrite is never auto-rebased back over itself, --onto and
    --force-with-lease are exempt, a dirty worktree with no origin asks nothing, and only the exact
    `refs/heads/<branch>` counts as the branch."""

    def _ssh_origin(self, tmp: Path):
        ctx = tmp / "ws" / ".context"
        ctx.mkdir(parents=True)
        wt = _seed_repo(tmp / "ws")
        subprocess.run(["git", "-C", str(wt), "remote", "add", "origin", "git@github.com:owner/repo.git"],
                       check=True, env=_env(tmp))
        return ctx, wt

    def test_an_unreachable_remote_notes_and_queues_a_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt = self._ssh_origin(tmp)
            (wt / "a.txt").write_text("a\n")
            r = _enqueue(tmp, wt, ctx, "ssh", "--files", "a.txt", "--by", "t", env_extra=_NO_SSH_IDENTITY)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("note: remote not reachable here: --new-branch/--rebase not decided; pass them yourself if needed (a moved remote parks the job)",
                          r.stderr)
            self.assertEqual(r.stderr.count("remote not reachable here"), 1)
            lines = _job_lines(r)
            self.assertIn('git -C "$WT" push origin "$BR"', lines)
            self.assertNotIn('git -C "$WT" push -u origin "$BR"', lines)
            self.assertNotIn(_PLAIN_REBASE, lines)

    def test_an_unreachable_remote_still_honours_explicit_flags(self):
        for flag, expect in (("--rebase", _PLAIN_REBASE), ("--new-branch", 'git -C "$WT" push -u origin "$BR"')):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                ctx, wt = self._ssh_origin(tmp)
                (wt / "a.txt").write_text("a\n")
                r = _enqueue(tmp, wt, ctx, "ssh-flag", "--files", "a.txt", "--by", "t", flag,
                             env_extra=_NO_SSH_IDENTITY)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(expect, _job_lines(r))

    def test_an_unreachable_remote_still_stops_a_clean_push_only_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt = self._ssh_origin(tmp)
            r = _enqueue(tmp, wt, ctx, "ssh-clean", "--by", "t", env_extra=_NO_SSH_IDENTITY)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("ls-remote origin main failed", r.stderr)

    def _amended_after_push(self, tmp: Path):
        ctx = tmp / "ws" / ".context"
        ctx.mkdir(parents=True)
        wt, origin = _with_origin(tmp / "ws")
        (wt / "drop.txt").write_text("x\n")
        (wt / "keep.txt").write_text("k\n")
        _git(tmp, wt, "add", "drop.txt", "keep.txt")
        _git(tmp, wt, "commit", "-q", "-m", "feat: b")
        _git(tmp, wt, "push", "-q", "origin", "main")
        _git(tmp, wt, "rm", "-q", "drop.txt")
        _git(tmp, wt, "commit", "-q", "--amend", "-m", "feat: b without drop")
        (wt / "a.txt").write_text("a\n")
        return ctx, wt, origin

    def test_an_amend_after_a_push_is_refused_not_auto_rebased(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt, origin = self._amended_after_push(tmp)
            r = _enqueue(tmp, wt, ctx, "amend", "--files", "a.txt", "--by", "t")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("rewrote away", r.stderr)
            self.assertIn("--force-with-lease", r.stderr)
            self.assertNotIn("-> --rebase", r.stderr)
            self.assertEqual(list(ctx.rglob("*.sh")), [], "a refused enqueue must not queue a job")

    def test_an_amend_under_someone_elses_newer_push_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt, origin = self._amended_after_push(tmp)
            _move_origin(tmp / "ws", origin)
            _git(tmp, wt, "fetch", "-q", "origin")
            r = _enqueue(tmp, wt, ctx, "amend-moved", "--files", "a.txt", "--by", "t")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("rewrote away", r.stderr)

    def test_an_amend_with_a_lease_is_queued_without_a_rebase(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx, wt, origin = self._amended_after_push(tmp)
            tip = subprocess.run(["git", "--git-dir", str(origin), "rev-parse", "main"], check=True,
                                 env=_env(tmp), capture_output=True, text=True).stdout.strip()
            r = _enqueue(tmp, wt, ctx, "amend-lease", "--files", "a.txt", "--by", "t", "--force-with-lease", tip)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn(_PLAIN_REBASE, _job_lines(r))

    def test_an_unpushed_amend_still_rebases_onto_a_moved_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt, origin = _with_origin(tmp / "ws")
            (wt / "b.txt").write_text("b\n")
            _git(tmp, wt, "add", "b.txt")
            _git(tmp, wt, "commit", "-q", "-m", "feat: b")
            _git(tmp, wt, "commit", "-q", "--amend", "-m", "feat: b, reworded")  # never pushed: no rewrite of the remote
            _move_origin(tmp / "ws", origin)
            _git(tmp, wt, "fetch", "-q", "origin")
            (wt / "a.txt").write_text("a\n")
            r = _enqueue(tmp, wt, ctx, "unpushed-amend", "--files", "a.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(_PLAIN_REBASE, _job_lines(r))

    def test_onto_is_never_turned_into_a_plain_rebase(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt, origin = _with_origin(tmp / "ws")
            _move_origin(tmp / "ws", origin)
            head = _git(tmp, wt, "rev-parse", "HEAD")
            (wt / "a.txt").write_text("a\n")
            r = _enqueue(tmp, wt, ctx, "onto", "--files", "a.txt", "--by", "t", "--onto", "main:" + head)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = _job_lines(r)
            self.assertNotIn(_PLAIN_REBASE, lines)
            self.assertIn('git -C "$WT" rebase -S -f --onto FETCH_HEAD "$OLD_BASE"', lines)

    def test_a_dirty_worktree_with_no_origin_asks_nothing_and_decides_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            (wt / "a.txt").write_text("a\n")
            r = _enqueue(tmp, wt, ctx, "no-origin", "--files", "a.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("ls-remote", r.stderr)
            self.assertNotIn("remote not reachable", r.stderr)
            lines = _job_lines(r)
            self.assertIn('git -C "$WT" push origin "$BR"', lines)
            self.assertNotIn(_PLAIN_REBASE, lines)

    def test_a_lease_on_a_branch_missing_on_the_remote_adds_no_new_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt, _ = _with_origin(tmp / "ws", push=False)
            (wt / "a.txt").write_text("a\n")
            r = _enqueue(tmp, wt, ctx, "lease-missing", "--files", "a.txt", "--by", "t",
                         "--force-with-lease", _git(tmp, wt, "rev-parse", "HEAD"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = _job_lines(r)
            self.assertNotIn('git -C "$WT" push -u origin "$BR"', lines)
            self.assertIn('git -C "$WT" push --force-with-lease="refs/heads/$BR:$LEASE" origin "$BR"', lines)

    def test_only_the_exact_ref_counts_as_the_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt, origin = _with_origin(tmp / "ws", push=False)
            _git(tmp, wt, "push", "-q", "origin", "HEAD:refs/heads/x/main")  # matches the `main` pattern, is not main
            (wt / "a.txt").write_text("a\n")
            r = _enqueue(tmp, wt, ctx, "exact-ref", "--files", "a.txt", "--by", "t")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = _job_lines(r)
            self.assertIn('git -C "$WT" push -u origin "$BR"', lines)
            self.assertNotIn(_PLAIN_REBASE, lines)

    def test_explicit_new_branch_stays_on_against_an_existing_remote_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt, _ = _with_origin(tmp / "ws")
            (wt / "a.txt").write_text("a\n")
            r = _enqueue(tmp, wt, ctx, "forced-new", "--files", "a.txt", "--by", "t", "--new-branch")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('git -C "$WT" push -u origin "$BR"', _job_lines(r))


@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write through directory permissions")
class ReadOnlyQueueDir(unittest.TestCase):
    """A queue directory that cannot be written stops before the claim loop ever runs, naming the directory
    and the real error — never "could not claim a unique job name", which would blame the wrong thing."""

    def test_read_only_queue_dir_names_itself_and_the_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            (wt / "seed.txt").write_text("changed\n")
            q = tmp / "ro-queue"
            q.mkdir()
            q.chmod(0o555)
            try:
                r = _enqueue(tmp, wt, ctx, "ro-queue", "--files", "seed.txt", "--by", "t",
                             env_extra={"SIGN_QUEUE_DIR": str(q)})
            finally:
                q.chmod(0o755)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn(str(q), r.stderr)
            self.assertIn("not writable", r.stderr)
            self.assertIn("Permission denied", r.stderr)
            self.assertEqual(list(q.glob("*.sh")), [], "a stopped enqueue must not queue a job")


if __name__ == "__main__":
    unittest.main()
