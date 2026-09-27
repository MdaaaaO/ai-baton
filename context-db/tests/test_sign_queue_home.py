"""#7: the sign queue lives in the workspace (`<.context>/state/sign-queue/`), never under the kit, which is the plugin
cache on a plugin install; jobs a pre-#7 kit queued under the kit dir move over on first use, never overwriting.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SIGNQ = KIT / "skills" / "sign-queue" / "signq.py"
ENQUEUE = KIT / "skills" / "sign-queue" / "enqueue.sh"


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
            env = {k: v for k, v in os.environ.items() if not k.startswith("SIGN_QUEUE_")}
            with mock.patch.dict(os.environ, {**env, "CONTEXT_ROOT": str(ctx)}, clear=True):
                sq = load_signq()
            self.assertEqual((sq.Q, sq.CONTEXT, sq.ROOT), (ctx / "state" / "sign-queue", ctx, ctx.parent))
            self.assertEqual(sq.LEGACY_Q, KIT / "sign-queue")
            self.assertFalse(str(sq.Q).startswith(str(KIT)))  # never below the kit

    def test_enqueue_refuses_without_a_workspace(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("SIGN_QUEUE_")}
        r = subprocess.run(["sh", str(ENQUEUE), "t", "/nonexistent", "b", "/nonexistent/msg"],
                           env={**env, "CONTEXT_ROOT": "/nonexistent/ws/.context"}, capture_output=True, text=True)
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
            git = ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t"]
            subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True)
            (wt / "a.txt").write_text("a\n")
            subprocess.run([*git, "add", "a.txt"], check=True)
            subprocess.run([*git, "commit", "-q", "-m", "chore: seed"], check=True)
            (wt / "a.txt").write_text("b\n")
            msg = Path(tmp) / "msg.txt"
            msg.write_text("fix: change a\n")
            env = {k: v for k, v in os.environ.items() if not k.startswith("SIGN_QUEUE_")}
            r = subprocess.run(["sh", str(ENQUEUE), "topic-a", str(wt), "main", str(msg), "--files", "a.txt",
                                "--ticket", "none", "--epic", "none", "--pr", "none", "--summary", "s", "--by", "t"],
                               env={**env, "CONTEXT_ROOT": str(ctx)}, capture_output=True, text=True, timeout=60)
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
            env = {k: v for k, v in os.environ.items() if not k.startswith("SIGN_QUEUE_")}
            with mock.patch.dict(os.environ, {**env, "CONTEXT_ROOT": str(Path(tmp) / "ws" / ".context")}, clear=True):
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
