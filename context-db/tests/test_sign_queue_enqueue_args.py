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
