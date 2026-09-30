"""The session-* targets' SESSION_ID default: `STATSFLAGS` reads SESSION_ID with `$(value …)` (so free text can
carry literal `$(...)` without the shell expanding it — see the Makefile's own `sq` comment), which means a
*lazily*-expanded default (`SESSION_ID ?= $(CLAUDE_CODE_SESSION_ID)`) leaks its own unexpanded reference text
instead of the session id: every `make session-touch`/`session-register`/`session-end`/`session-stats` call that
does not pass `SESSION_ID=` by hand ends up passing the literal string "$(CLAUDE_CODE_SESSION_ID)" as
`--session-id`, so the stats lookup never finds the transcript. Fixed with an eager default (`:=`) taken only
when SESSION_ID is otherwise undefined, so an explicit `SESSION_ID=` (command line or shell environment) still
wins. `make -n` prints even the `@`-silenced recipe lines without running them, so a dry run is enough to see
the flag. Stdlib unittest. Run: make -C $BATON/context-db test."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
MAKE_VARS = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES")


def clean_env(**extra: str) -> dict:
    """The ambient environment stripped of anything that would let this test's own session (or an
    outer `make test`) leak SESSION_ID/CONTEXT settings into the child `make -n` call."""
    env = {k: v for k, v in os.environ.items()
            if k not in ("CLAUDE_CODE_SESSION_ID", "SESSION_ID", "CONTEXT_ROOT", "CONTEXT", *MAKE_VARS)}
    return {**env, **extra}


def dry_run(*args: str, **env_extra: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = str(Path(tmp) / ".context")  # never created — session-touch's `-n` recipe never runs
        p = subprocess.run(["make", "-n", "-C", str(ENGINE), "session-touch", "NAME=x", f"CONTEXT={ctx}", *args],
                            env=clean_env(**env_extra), capture_output=True, text=True, timeout=60)
    stdout = p.stdout
    assert p.returncode == 0, p.stdout + p.stderr
    assert "session.py touch" in stdout, stdout + p.stderr
    return stdout


class SessionIdExpansion(unittest.TestCase):
    def test_an_environment_session_id_is_expanded_not_passed_literally(self):
        fake = "sess-" + uuid.uuid4().hex
        out = dry_run(CLAUDE_CODE_SESSION_ID=fake)
        self.assertIn(f"--session-id '{fake}'", out)
        self.assertNotIn("$(", out)

    def test_an_explicit_session_id_override_wins_over_the_environment(self):
        fake_env = "sess-env-" + uuid.uuid4().hex
        fake_explicit = "sess-explicit-" + uuid.uuid4().hex
        out = dry_run(f"SESSION_ID={fake_explicit}", CLAUDE_CODE_SESSION_ID=fake_env)
        self.assertIn(f"--session-id '{fake_explicit}'", out)
        self.assertNotIn(fake_env, out)
        self.assertNotIn("$(", out)

    def test_no_session_id_anywhere_yields_no_session_id_flag(self):
        out = dry_run()  # neither SESSION_ID= nor CLAUDE_CODE_SESSION_ID in the environment
        self.assertNotIn("--session-id", out)
        self.assertNotIn("$(", out)


if __name__ == "__main__":
    unittest.main()
