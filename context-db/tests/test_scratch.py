"""#138: `kit_profile.py scratch` lands in a per-user 0700 root and refuses a symlinked or foreign one.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402


class ScratchRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_JOB_DIR", "KIT_SCRATCH", "XDG_RUNTIME_DIR")}
        env.update(TMPDIR=str(self.t), CLAUDE_CODE_SESSION_ID="s-1")
        self.env = env

    def tearDown(self):
        self.tmp.cleanup()

    def scratch(self, *a):
        with mock.patch.dict(os.environ, self.env, clear=True):
            return kit_profile.scratch(*a)

    def test_shared_temp_root_is_per_uid_and_private(self):
        p = self.scratch("x")
        root = self.t / f"ai-baton-kit-{os.getuid()}"
        self.assertEqual(p, root / "s-1" / "x")
        for d in (root, root / "s-1", p):
            self.assertEqual(stat.S_IMODE(os.lstat(d).st_mode), 0o700, d)

    def test_a_loose_root_is_tightened(self):
        root = self.t / f"ai-baton-kit-{os.getuid()}"
        root.mkdir(mode=0o777)
        os.chmod(root, 0o777)
        self.scratch()
        self.assertEqual(stat.S_IMODE(os.lstat(root).st_mode), 0o700)

    def test_a_symlinked_root_is_refused(self):
        target = self.t / "elsewhere"
        target.mkdir()
        (self.t / f"ai-baton-kit-{os.getuid()}").symlink_to(target)
        with self.assertRaises(kit_profile.ScratchError):
            self.scratch()
        self.assertEqual(list(target.iterdir()), [])
        r = subprocess.run([sys.executable, str(BIN / "kit_profile.py"), "scratch"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("symlink", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_xdg_runtime_dir_wins(self):
        xdg = self.t / "run"
        xdg.mkdir(mode=0o700)
        self.env["XDG_RUNTIME_DIR"] = str(xdg)
        self.assertEqual(self.scratch(), xdg / "ai-baton-kit" / "s-1")

    def test_explicit_override_is_not_checked(self):
        self.env["KIT_SCRATCH"] = str(self.t / "mine")
        self.assertEqual(self.scratch(), self.t / "mine")



class SweepPrReview(unittest.TestCase):
    """`scratch --sweep-pr-review` removes the session's own pr-review/ subtree and nothing else."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_JOB_DIR", "KIT_SCRATCH", "XDG_RUNTIME_DIR")}
        env.update(TMPDIR=str(self.t), CLAUDE_CODE_SESSION_ID="s-1")
        self.env = env
        self.mine = self.t / f"ai-baton-kit-{os.getuid()}" / "s-1"
        self.other = self.t / f"ai-baton-kit-{os.getuid()}" / "s-2"
        for d in (self.mine, self.other):
            (d / "pr-review" / "acme_widgets" / "7").mkdir(parents=True)
            (d / "pr-review" / "acme_widgets" / "7" / "diff.patch").write_bytes(b"x" * 2048)
            (d / "next.md").write_text("prompt\n")
            (d / "session-name").write_text("a-session\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self):
        return subprocess.run([sys.executable, str(BIN / "kit_profile.py"), "scratch", "--sweep-pr-review"],
                              env=self.env, capture_output=True, text=True)

    def test_removes_only_the_sessions_own_pr_review_subtree(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2048 bytes", r.stdout)
        self.assertFalse((self.mine / "pr-review").exists())
        self.assertEqual(sorted(p.name for p in self.mine.iterdir()), ["next.md", "session-name"])
        self.assertTrue((self.other / "pr-review" / "acme_widgets" / "7" / "diff.patch").is_file())  # another session's

    def test_nothing_to_remove_is_ok_and_idempotent(self):
        self.run_cli()
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no pr-review scratch", r.stdout)

    def test_a_symlinked_pr_review_is_refused_and_its_target_kept(self):
        victim = self.t / "victim"
        victim.mkdir()
        (victim / "keep.txt").write_text("k")
        import shutil
        shutil.rmtree(self.mine / "pr-review")
        (self.mine / "pr-review").symlink_to(victim)
        r = self.run_cli()
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("symlink", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertTrue((victim / "keep.txt").is_file())
        self.assertTrue((self.mine / "pr-review").is_symlink())

    def test_the_shared_uid_fallback_key_is_refused(self):
        env = {k: v for k, v in self.env.items() if k != "CLAUDE_CODE_SESSION_ID"}
        uid_dir = self.t / f"ai-baton-kit-{os.getuid()}" / f"uid-{os.getuid()}"
        uid_dir.mkdir(parents=True)
        (uid_dir / "pr-review").mkdir()
        (uid_dir / "pr-review" / "x").write_text("x")
        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(kit_profile, "_owning_pid", return_value=None), \
             mock.patch("sys.stderr", err):
            rc = kit_profile.main(["kit_profile.py", "scratch", "--sweep-pr-review"])
        self.assertEqual(rc, 2)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn("shared", err.getvalue())
        self.assertTrue((uid_dir / "pr-review" / "x").is_file())

    def test_session_scratch_root_is_none_under_an_override(self):
        with mock.patch.dict(os.environ, dict(self.env, KIT_SCRATCH=str(self.t / "own")), clear=True):
            self.assertIsNone(kit_profile.session_scratch_root())
        with mock.patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(kit_profile.session_scratch_root(), self.t / f"ai-baton-kit-{os.getuid()}")


if __name__ == "__main__":
    unittest.main()
