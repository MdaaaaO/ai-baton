"""#138: `kit_profile.py scratch` lands in a per-user 0700 root and refuses a symlinked or foreign one.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
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


if __name__ == "__main__":
    unittest.main()
