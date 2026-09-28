"""gen_engine_cli.py: the generator runs on a bare clone and the committed docs/engine-cli.md equals its output, so
a Makefile-help or `--help` change that forgets `make engine-cli-doc` fails CI. Stdlib unittest. Run: make -C
$BATON/context-db test."""
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


class Norm(unittest.TestCase):
    """argparse itself renamed a heading (`optional arguments:` before 3.10, `options:` from 3.10 on) — the same
    class of per-Python-version drift `norm()` already absorbs for whitespace, so a 3.9 CI leg comparing
    `--help` output against a doc committed under a newer Python does not fail on wording norm() already knows."""

    def test_pre_310_wording_matches_the_310_wording(self):
        self.assertEqual(g.norm("positional arguments:\n  x\n\noptional arguments:\n  -h, --help"),
                         g.norm("positional arguments:\n  x\n\noptions:\n  -h, --help"))

    def test_does_not_touch_the_phrase_without_the_heading_colon(self):
        # only the argparse heading (`optional arguments:`) is canonicalised — the same words in prose are not.
        self.assertEqual(g.norm("some optional arguments in prose, not the heading"),
                         "some optional arguments in prose, not the heading")


class EngineCliDoc(unittest.TestCase):
    def test_generator_runs_and_covers_make_help_and_every_tool(self):
        p = run("--stdout")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("## `make -C $BATON/context-db help`", p.stdout)
        self.assertIn("make -C $BATON/context-db engine-cli-doc", p.stdout)  # the Makefile header names the target
        for tool in ("kb.py", "kit_verify.py", "review_gate.py", "session.py", "gen_index.py"):
            self.assertIn(f"## `{tool}`", p.stdout)
        self.assertIn("usage: kb.py", p.stdout)

    def test_committed_doc_equals_generator_output(self):
        p = run("--stdout")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(DOC.is_file(), "docs/engine-cli.md is missing — run `make -C $BATON/context-db engine-cli-doc`")
        # whitespace-insensitive: argparse wraps `--help` differently per Python version, the words must match
        self.assertEqual(g.norm(DOC.read_text(encoding="utf-8")), g.norm(p.stdout),
                         "docs/engine-cli.md drifted from the generator — run `make -C $BATON/context-db engine-cli-doc`")

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
