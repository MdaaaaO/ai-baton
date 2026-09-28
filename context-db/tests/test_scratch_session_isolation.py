"""kit_profile.py scratch() keys the per-session scratch dir on CLAUDE_CODE_SESSION_ID under $XDG_RUNTIME_DIR
(else $TMPDIR). A real Claude Code session exports both into every command it runs, including `make ci`/
`make test`; a test fixture that registers a session (e.g. test_gen_sessions.py's `stale-no-prompt` fixture,
through session.py's name stamp) builds its own subprocess env by copying os.environ and setting only a fake
CLAUDE_CODE_SESSION_ID — the way test_gen_sessions.py's `env_for` does — so without the test package's own
isolation (tests/__init__.py, imported before any test module runs) that write lands in the REAL session's
scratch dir, overwriting its `session-name` file (or, for a hook fixture, appending to its `hooks.log`).

This reproduces that shape end to end in an isolated child interpreter, standing in for "a real session's
ambient $XDG_RUNTIME_DIR/$CLAUDE_CODE_SESSION_ID, inherited by `make test`'s Python process": the child never
touches this session's actual runtime dir, but importing `tests` inside it must still neutralize its fabricated
ambient env before the inner registering fixture runs — exactly as it does for the real one. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
CONTEXT_DB = BIN.parent  # "tests" (this package) and "bin" both live here


class RegisteringFixtureNeverWritesTheRealRuntimeDir(unittest.TestCase):
    def test_a_fixtures_fake_session_id_never_reaches_the_ambient_runtime_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            real_runtime = tmp / "real-run"  # stands in for the actual session's $XDG_RUNTIME_DIR
            real_runtime.mkdir(mode=0o700)
            real_session = "s-real-parent"       # the session actually running `make test`
            fixture_session = "s-fixture-fake"   # what a registering test fixture sets for itself
            root = tmp / ".context"

            # The ambient env a real Claude Code session exports into every command it runs — what `make
            # test`'s Python process would inherit if run from inside one.
            ambient = {**os.environ, "XDG_RUNTIME_DIR": str(real_runtime), "CLAUDE_CODE_SESSION_ID": real_session}
            ambient.pop("TMPDIR", None)

            # Runs entirely inside that ambient env: importing `tests` first (as `python3 -m unittest discover`
            # does for every module) must neutralize it before the inner registering fixture — built the way
            # test_gen_sessions.py's `env_for` builds one, by copying os.environ and setting only a fake
            # CLAUDE_CODE_SESSION_ID — spawns session.py register. Both checked spots (leaked/written) are read
            # inside this same short-lived process, before its own exit prunes the temp runtime dir it made.
            script = textwrap.dedent(f"""
                import json, os, subprocess, sys
                from pathlib import Path
                sys.path.insert(0, {str(CONTEXT_DB)!r})
                import tests  # noqa: F401 — runs the package's isolation before any fixture executes
                env = {{**os.environ, "CONTEXT_ROOT": {str(root)!r}, "CLAUDE_CODE_SESSION_ID": {fixture_session!r}}}
                r = subprocess.run([sys.executable, {str(BIN / "session.py")!r}, "register", "--name", "iso-check",
                                    "--no-stats"], env=env, cwd={str(BIN)!r}, capture_output=True, text=True)
                sys.stderr.write(r.stderr)
                test_xdg = os.environ.get("XDG_RUNTIME_DIR", "")
                leaked = Path({str(real_runtime)!r}) / "ai-baton-kit" / {fixture_session!r} / "session-name"
                written = Path(test_xdg) / "ai-baton-kit" / {fixture_session!r} / "session-name" if test_xdg else None
                print(json.dumps({{"rc": r.returncode, "test_xdg": test_xdg, "leaked_exists": leaked.exists(),
                                   "written_text": written.read_text(encoding="utf-8") if written and written.is_file() else None}}))
                """)
            outer = subprocess.run([sys.executable, "-c", script], env=ambient, capture_output=True, text=True)
            self.assertEqual(outer.returncode, 0, outer.stderr)
            result = json.loads(outer.stdout.strip().splitlines()[-1])
            self.assertEqual(result["rc"], 0, outer.stderr)
            self.assertTrue(result["test_xdg"], "the suite must always run with XDG_RUNTIME_DIR pointed at its own dir")

            # never in the ambient/real runtime dir the fabricated session actually carried
            self.assertFalse(result["leaked_exists"],
                              "a registering fixture must never write into the real runtime dir "
                              f"({real_runtime}/ai-baton-kit/{fixture_session}/session-name)")

            # only ever under the suite's own isolated one, which must differ from the ambient dir
            self.assertNotEqual(result["test_xdg"], str(real_runtime))
            self.assertIsNotNone(result["written_text"], "expected the registering fixture to write session-name "
                                                          "under the suite's own runtime dir")
            self.assertEqual(result["written_text"].strip(), "iso-check")


    def test_a_sandbox_sessions_job_dir_and_kit_scratch_are_dropped_too(self):
        # scratch() consults CLAUDE_JOB_DIR and KIT_SCRATCH before XDG_RUNTIME_DIR: a sandbox session exports them
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            job, kit_scratch = tmp / "job", tmp / "kit-scratch"
            (job / "tmp").mkdir(parents=True)  # scratch() is `<job>/tmp` in a sandbox
            kit_scratch.mkdir()
            root = tmp / ".context"
            ambient = {**os.environ, "CLAUDE_JOB_DIR": str(job), "KIT_SCRATCH": str(kit_scratch),
                       "CLAUDE_CODE_SESSION_ID": "s-real-parent"}
            script = textwrap.dedent(f"""
                import os, subprocess, sys
                sys.path.insert(0, {str(CONTEXT_DB)!r})
                import tests  # noqa: F401
                env = {{**os.environ, "CONTEXT_ROOT": {str(root)!r}, "CLAUDE_CODE_SESSION_ID": "s-fixture-fake"}}
                r = subprocess.run([sys.executable, {str(BIN / "session.py")!r}, "register", "--name", "iso-job",
                                    "--no-stats"], env=env, cwd={str(BIN)!r}, capture_output=True, text=True)
                sys.stderr.write(r.stderr)
                sys.exit(r.returncode)
                """)
            outer = subprocess.run([sys.executable, "-c", script], env=ambient, capture_output=True, text=True)
            self.assertEqual(outer.returncode, 0, outer.stderr)
            self.assertEqual([p for p in job.rglob("session-name")], [], "wrote into the real job dir")
            self.assertEqual([p for p in kit_scratch.rglob("session-name")], [], "wrote into the real KIT_SCRATCH")


if __name__ == "__main__":
    unittest.main()
