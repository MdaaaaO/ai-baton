"""sign-queue enqueue.sh --supersede: folding another round of fixes into an unsigned job instead of
piling up a second job for the same topic. The flag finds the newest PENDING job matching `topic` (and,
when `--by` is also passed, that `by` too), deletes it and enqueues the new one in its place — but refuses
outright, leaving everything as it was, when that job already has a drain log or is parked as `.failed`:
it was attempted, not merely queued, and folding into it would lose that history. Stdlib unittest, real git
(no signing key needed — these tests never run the generated job, only enqueue.sh's own bookkeeping).
Run: make -C .claude/context-db test."""
from __future__ import annotations
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
ENQUEUE = KIT / "skills" / "sign-queue" / "enqueue.sh"
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402


def _env(tmp, ctx) -> dict:
    env = {k: v for k, v in hermetic_env(tmp).items() if not k.startswith("SIGN_QUEUE_")}
    env["CONTEXT_ROOT"] = str(ctx)
    return env


def _seed_repo(root: Path) -> Path:
    wt = root / "repo"
    wt.mkdir()
    env = _env(root, root)  # CONTEXT_ROOT is unused by these plain git calls; reusing _env keeps one hermetic source
    git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True, env=env)
    (wt / "seed.txt").write_text("seed\n")
    subprocess.run([*git, "add", "seed.txt"], check=True, env=env)
    subprocess.run([*git, "commit", "-q", "-m", "chore: seed"], check=True, env=env)
    return wt


def _enqueue(tmp: Path, ctx: Path, wt: Path, topic: str, fname: str, subject: str, by: str,
            extra: list[str] | None = None) -> subprocess.CompletedProcess:
    (wt / fname).write_text(fname + "\n")
    msg = tmp / f"{fname}.msg.txt"
    msg.write_text(subject + "\n")
    # --summary omitted: it derives from the message file's first line, so the job's own META carries
    # each round's real subject — the check below reads that back to prove which round is still queued.
    args = ["sh", str(ENQUEUE), topic, str(wt), "main", str(msg), "--files", fname,
            "--ticket", "none", "--epic", "none", "--pr", "none", "--by", by]
    if extra:
        args += extra
    return subprocess.run(args, env=_env(tmp, ctx), capture_output=True, text=True, timeout=60)


class SupersedeReplacesThePendingJob(unittest.TestCase):
    def test_the_newest_pending_job_of_the_same_topic_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            r1 = _enqueue(tmp, ctx, wt, "fold", "a.txt", "fix: round one", "t")
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
            q = ctx / "state" / "sign-queue"
            first = list(q.glob("*-fold.sh"))
            self.assertEqual(len(first), 1, r1.stdout + r1.stderr)

            time.sleep(1.05)  # job names are a UTC-second timestamp + topic: force a distinct one (#46, unrelated)
            r2 = _enqueue(tmp, ctx, wt, "fold", "b.txt", "fix: round two", "t", extra=["--supersede"])
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("--supersede dropped", r2.stderr)
            self.assertIn(first[0].name, r2.stderr)

            remaining = list(q.glob("*-fold.sh"))
            self.assertEqual(len(remaining), 1, "the old job must be gone, only the new one left")
            self.assertNotEqual(remaining[0], first[0])
            self.assertIn("fix: round two", remaining[0].read_text())
            self.assertNotIn("fix: round one", remaining[0].read_text())

    def test_a_different_by_is_left_untouched(self):
        # --supersede with --by only folds into a job enqueued under that SAME --by; a job from
        # another session with the same topic is not a fold target.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            r1 = _enqueue(tmp, ctx, wt, "fold-by", "a.txt", "fix: session one", "session-a")
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
            time.sleep(1.05)  # job names are a UTC-second timestamp + topic: force a distinct one (#46, unrelated)
            r2 = _enqueue(tmp, ctx, wt, "fold-by", "b.txt", "fix: session two", "session-b",
                         extra=["--supersede"])
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertNotIn("--supersede dropped", r2.stderr)
            q = ctx / "state" / "sign-queue"
            remaining = sorted(p.name for p in q.glob("*-fold-by.sh"))
            self.assertEqual(len(remaining), 2, "a different --by must never be folded away")


class SupersedeRefusesAnAttemptedJob(unittest.TestCase):
    def test_a_job_with_a_drain_log_is_refused_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            r1 = _enqueue(tmp, ctx, wt, "attempted", "a.txt", "fix: first try", "t")
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
            q = ctx / "state" / "sign-queue"
            job = next(q.glob("*-attempted.sh"))
            (q / "logs").mkdir(parents=True, exist_ok=True)
            (q / "logs" / f"{job.name}.log").write_text("a drain was attempted here\n")

            r2 = _enqueue(tmp, ctx, wt, "attempted", "b.txt", "fix: second try", "t",
                         extra=["--supersede"])
            self.assertEqual(r2.returncode, 2, r2.stdout + r2.stderr)
            self.assertIn("refuses", r2.stderr)
            self.assertIn(job.name, r2.stderr)
            self.assertTrue(job.exists(), "a job with a drain log must never be deleted by --supersede")
            self.assertEqual(list(q.glob("*-attempted.sh")), [job], "no second job must have been queued either")

    def test_a_parked_failure_is_refused_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            r1 = _enqueue(tmp, ctx, wt, "parked", "a.txt", "fix: first try", "t")
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
            q = ctx / "state" / "sign-queue"
            job = next(q.glob("*-parked.sh"))
            # a `.failed` sibling of the exact candidate name — the drain-parked shape enqueue.sh checks for
            (q / f"{job.name}.failed").write_text(job.read_text())

            r2 = _enqueue(tmp, ctx, wt, "parked", "b.txt", "fix: second try", "t", extra=["--supersede"])
            self.assertEqual(r2.returncode, 2, r2.stdout + r2.stderr)
            self.assertIn("refuses", r2.stderr)
            self.assertTrue(job.exists(), "a job with a parked failure must never be deleted by --supersede")
            self.assertEqual(list(q.glob("*-parked.sh")), [job], "no second job must have been queued either")


class SupersedeDeletionIsDeferredPastValidation(unittest.TestCase):
    def test_a_later_refusal_leaves_the_candidate_job_untouched(self):
        # --supersede used to `rm -f` the candidate job as soon as it was found, before the later checks
        # (--onto format, --force-with-lease, --files, signq.py meta) had a chance to `exit 2`. A refused
        # enqueue must touch nothing — including the job it would have folded into.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            wt = _seed_repo(tmp / "ws")
            r1 = _enqueue(tmp, ctx, wt, "fold-refused", "a.txt", "fix: round one", "t")
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
            q = ctx / "state" / "sign-queue"
            first = list(q.glob("*-fold-refused.sh"))
            self.assertEqual(len(first), 1, r1.stdout + r1.stderr)

            time.sleep(1.05)  # job names are a UTC-second timestamp + topic: force a distinct one (#46, unrelated)
            # a --files path that is neither in the worktree nor tracked is refused (exit 2) well after the
            # supersede candidate is found, so this proves the deletion itself waits for that check to pass.
            (wt / "c.txt").write_text("c\n")
            msg = tmp / "c.txt.msg.txt"
            msg.write_text("fix: round two\n")
            r2 = subprocess.run(["sh", str(ENQUEUE), "fold-refused", str(wt), "main", str(msg),
                                 "--files", "no-such-file.txt", "--ticket", "none", "--epic", "none",
                                 "--pr", "none", "--by", "t", "--supersede"],
                                env=_env(tmp, ctx), capture_output=True, text=True, timeout=60)
            self.assertEqual(r2.returncode, 2, r2.stdout + r2.stderr)
            self.assertIn("neither in the worktree nor tracked", r2.stderr)
            self.assertNotIn("--supersede dropped", r2.stderr, "the fold must not run when the enqueue is refused")
            self.assertTrue(first[0].exists(), "a refused enqueue must leave the candidate job in place")
            remaining = list(q.glob("*-fold-refused.sh"))
            self.assertEqual(remaining, first, "no job may be added or removed by a refused --supersede")


if __name__ == "__main__":
    unittest.main()
