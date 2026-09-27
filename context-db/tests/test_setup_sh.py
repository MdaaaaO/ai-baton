"""setup.sh — runs the bats-free scenario script tests/setup_sh_scenarios.sh (sh -e; memory migration, symlink
handling, POSIX grep, helper failures, leftover cleanup). Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "setup_sh_scenarios.sh"


class SetupSh(unittest.TestCase):
    def test_every_scenario_passes(self):
        r = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, "\n" + r.stdout + "\n" + r.stderr)
        self.assertIn("setup.sh scenarios: all passed", r.stdout)
        self.assertNotIn("FAIL", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
