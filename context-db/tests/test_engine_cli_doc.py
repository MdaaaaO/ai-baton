"""gen_engine_cli.py (#82): the generator runs on a bare clone and the committed docs/engine-cli.md equals its output, so
a Makefile-help or `--help` change that forgets `make engine-cli-doc` fails CI. Stdlib unittest. Run: make -C
.claude/context-db test."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))
import gen_engine_cli as g  # noqa: E402
KIT = HERE.parents[1]
DOC = KIT / "docs" / "engine-cli.md"


def run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, CONTEXT_ROOT="/nonexistent/.context")  # the bare-clone situation: no store anywhere
    return subprocess.run([sys.executable, str(BIN / "gen_engine_cli.py"), *args], capture_output=True, text=True,
                          encoding="utf-8", env=env, cwd=KIT)


class EngineCliDoc(unittest.TestCase):
    def test_generator_runs_and_covers_make_help_and_every_tool(self):
        p = run("--stdout")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("## `make -C $BATON/context-db help`", p.stdout)
        self.assertIn("make -C .claude/context-db engine-cli-doc", p.stdout)  # the Makefile header names the target
        for tool in ("kb.py", "kit_verify.py", "review_gate.py", "session.py", "gen_index.py"):
            self.assertIn(f"## `{tool}`", p.stdout)
        self.assertIn("usage: kb.py", p.stdout)

    def test_committed_doc_equals_generator_output(self):
        p = run("--stdout")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(DOC.is_file(), "docs/engine-cli.md is missing — run `make -C .claude/context-db engine-cli-doc`")
        # whitespace-insensitive: argparse wraps `--help` differently per Python version, the words must match
        self.assertEqual(g.norm(DOC.read_text(encoding="utf-8")), g.norm(p.stdout),
                         "docs/engine-cli.md drifted from the generator — run `make -C .claude/context-db engine-cli-doc`")

    def test_check_flag_reports_drift(self):
        with tempfile.TemporaryDirectory() as td:
            stale = Path(td) / "engine-cli.md"
            stale.write_text(DOC.read_text(encoding="utf-8") + "\nstale line\n", encoding="utf-8")
            p = run("--check", "--out", str(stale))
            self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
            self.assertIn("out of date", p.stderr)
            fresh = Path(td) / "fresh.md"
            self.assertEqual(run("--out", str(fresh)).returncode, 0)
            self.assertEqual(run("--check", "--out", str(fresh)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
