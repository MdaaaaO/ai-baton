"""`kit_profile.py session-name` / `footer` cross-check the recorded scratch name against the store (#551): a name whose
`sessions/<name>.md` is missing or `ended` is a stale scratch file (a walkthrough or fixture wrote it), so the footer
falls back to the bare "" with one stderr line instead of putting a wrong name in a public PR body. An active row keeps
the name, and a store that cannot be read keeps the recorded name (a transient error must not blank the footer).
Subprocess against a throw-away store with its own KIT_SCRATCH — never the workspace's. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"


class FooterCrossCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        (self.root / "sessions").mkdir(parents=True)
        self.scratch = Path(self.tmp.name) / "scratch"
        self.scratch.mkdir()
        (self.scratch / "session-name").write_text("kit-recorded\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def kp(self, cmd: str, root: Path | None = None) -> subprocess.CompletedProcess:
        e = {k: v for k, v in os.environ.items() if k not in ("WORKSPACE_TZ", "CLAUDE_CODE_SESSION_ID")}
        e.update(CONTEXT_ROOT=str(root or self.root), KIT_SCRATCH=str(self.scratch))
        return subprocess.run([sys.executable, str(BIN / "kit_profile.py"), cmd], env=e, cwd=BIN,
                              capture_output=True, text=True)

    def row(self, status: str) -> None:
        (self.root / "sessions" / "kit-recorded.md").write_text(
            f"---\nsession: kit-recorded\nstatus: {status}\n---\n\nbody\n", encoding="utf-8")

    def test_an_active_row_keeps_the_name(self):
        self.row("active")
        r = self.kp("footer")
        self.assertEqual((r.stdout.strip(), r.stderr), ("session `kit-recorded`", ""))
        self.assertEqual(self.kp("session-name").stdout.strip(), "kit-recorded")

    def test_a_recorded_name_with_no_row_gives_the_bare_footer(self):
        r = self.kp("footer")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertIn(str(self.scratch / "session-name"), r.stderr)
        self.assertEqual(self.kp("session-name").returncode, 1)

    def test_an_ended_row_gives_the_bare_footer(self):
        self.row("ended")
        r = self.kp("footer")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))
        self.assertIn("stale scratch file", r.stderr)
        self.assertEqual(self.kp("session-name").returncode, 1)

    def test_an_unreadable_store_keeps_the_recorded_name(self):
        # no store directory at all (a stale mount, a moved tree): the check cannot say the row is missing
        r = self.kp("footer", root=Path(self.tmp.name) / "gone" / ".context")
        self.assertEqual(r.stdout.strip(), "session `kit-recorded`", r.stderr)
        if os.geteuid() != 0:  # a row file that cannot be read is the same: keep the name
            self.row("ended")
            (self.root / "sessions" / "kit-recorded.md").chmod(0)
            r = self.kp("footer")
            self.assertEqual(r.stdout.strip(), "session `kit-recorded`", r.stderr)


if __name__ == "__main__":
    unittest.main()
