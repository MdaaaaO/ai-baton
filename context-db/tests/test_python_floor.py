"""kit_profile.py declares the kit's Python floor and enforces it (docs/contributing.md, issue: the 3.9 floor):
`zoneinfo` (stdlib, imported by kit_profile itself), `Path.is_relative_to` and `str.removeprefix` are 3.9+-only, so
a machine below the floor should stop with one clear line, not a stack trace from whichever module happens to hit
the missing feature first. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402

KIT_PROFILE_PY = str(BIN / "kit_profile.py")


class PythonFloorError(unittest.TestCase):
    """The pure function: a fact-shaped version tuple in, `None` or the one-line message out."""

    def test_below_the_floor_returns_the_message(self):
        self.assertEqual(kit_profile.python_floor_error((3, 8, 10)), "ai-baton needs Python 3.9+ (found 3.8.10)")
        self.assertEqual(kit_profile.python_floor_error((2, 7, 18)), "ai-baton needs Python 3.9+ (found 2.7.18)")

    def test_at_or_above_the_floor_returns_none(self):
        self.assertIsNone(kit_profile.python_floor_error((3, 9, 0)))
        self.assertIsNone(kit_profile.python_floor_error((3, 12, 1)))

    def test_this_interpreter_meets_the_floor(self):
        # The suite itself only runs on 3.9+ (docs/contributing.md § Testing), so the default (no argument) must
        # agree with the interpreter actually running this test.
        self.assertIsNone(kit_profile.python_floor_error())


class ModuleLevelGuard(unittest.TestCase):
    """The check runs at import time, before the zoneinfo import that would otherwise be the first thing to
    fail — so it must sit above every other import in the file, not merely exist somewhere in it."""

    @staticmethod
    def run_under(version_info: tuple) -> subprocess.CompletedProcess:
        # A real interpreter that low is not assumed to exist on the test machine: fake sys.version_info on the
        # one that is running, then execute kit_profile.py as __main__ — the module-level guard reads the same
        # attribute the real check would.
        code = f"import sys, runpy; sys.version_info={version_info!r}; runpy.run_path({KIT_PROFILE_PY!r}, run_name='__main__')"
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    def test_below_the_floor_exits_nonzero_with_one_line_on_stderr(self):
        p = self.run_under((3, 8, 0, "final", 0))
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout, "")
        self.assertEqual(p.stderr.strip(), "ai-baton needs Python 3.9+ (found 3.8.0)")

    def test_at_the_floor_runs_past_the_guard(self):
        p = self.run_under((3, 9, 0, "final", 0))
        self.assertNotIn("ai-baton needs Python", p.stderr)


if __name__ == "__main__":
    unittest.main()
