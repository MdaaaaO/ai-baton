"""hooks/hooks.json's SessionStart command: idempotent against $CLAUDE_ENV_FILE across repeated
runs — a hook fires on every session start and the file is not recreated between them, so a plain
append accumulates one export block per start over weeks — and it logs a failing session-env or
workspace-rules call instead of discarding it: a write that fails must say so on stdout, not vanish
behind `2>/dev/null`. Fact-shaped literals are assembled at run time. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]

CMD = json.loads((KIT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["command"]

LOGIN = "octo-" + "tester"


def run(cmd: str, **env: str) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKSPACE_"))}
    return subprocess.run(["sh", "-c", cmd], env={**base, **env}, capture_output=True, text=True)


class SessionStartIsIdempotent(unittest.TestCase):
    def test_running_twice_on_the_same_env_file_appends_the_block_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            envfile = Path(tmp) / "env"
            kwargs = {"CLAUDE_PLUGIN_ROOT": str(KIT), "CLAUDE_ENV_FILE": str(envfile),
                      "CONTEXT_ROOT": "/nonexistent/.context", "KIT_SCRATCH": str(Path(tmp) / "scratch"),
                      "CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN": LOGIN}
            r1 = run(CMD, **kwargs)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            first = envfile.read_text(encoding="utf-8")
            self.assertEqual(first.count("export WORKSPACE_GITHUB_LOGIN"), 1)
            # a second SessionStart on the very same file (nothing recreates it between sessions)
            r2 = run(CMD, **kwargs)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            second = envfile.read_text(encoding="utf-8")
            self.assertEqual(first, second)  # no duplicate block — the guard made this run a no-op
            self.assertEqual(second.count("export WORKSPACE_GITHUB_LOGIN"), 1)

    def test_no_env_file_is_still_a_quiet_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run(CMD, CLAUDE_PLUGIN_ROOT=str(KIT), CONTEXT_ROOT="/nonexistent/.context",
                    KIT_SCRATCH=str(Path(tmp) / "scratch"))
            self.assertEqual((r.returncode, r.stdout), (0, ""))


class SessionStartLogsFailures(unittest.TestCase):
    def stub_kit(self, tmp: Path, exit_session_env: int) -> Path:
        """A fake `$CLAUDE_PLUGIN_ROOT` whose kit_profile.py stands in for the real one: `scratch`
        prints a real dir under $KIT_SCRATCH, `session-env` prints to stderr and exits as told,
        `workspace-rules` is a no-op — enough surface for the hook command, none of the real engine."""
        bin_dir = tmp / "kit" / "context-db" / "bin"
        bin_dir.mkdir(parents=True)
        stub = bin_dir / "kit_profile.py"
        stub.write_text(
            "import os, sys, pathlib\n"
            "cmd = sys.argv[1] if len(sys.argv) > 1 else ''\n"
            "if cmd == 'scratch':\n"
            "    d = pathlib.Path(os.environ['KIT_SCRATCH']); d.mkdir(parents=True, exist_ok=True); print(d)\n"
            "elif cmd == 'session-env':\n"
            f"    print('boom-from-session-env', file=sys.stderr); sys.exit({exit_session_env})\n"
            "elif cmd == 'workspace-rules':\n"
            "    pass\n",
            encoding="utf-8",
        )
        return bin_dir.parent.parent

    def test_a_session_env_failure_prints_one_line_and_is_logged_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_kit = self.stub_kit(tmp_path, exit_session_env=1)
            envfile = tmp_path / "env"
            scratch = tmp_path / "scratch"
            r = run(CMD, CLAUDE_PLUGIN_ROOT=str(fake_kit), CLAUDE_ENV_FILE=str(envfile), KIT_SCRATCH=str(scratch))
            self.assertEqual(r.returncode, 0)
            self.assertIn("ai-baton: session-env failed", r.stdout)  # visible in the session, not discarded
            log = scratch / "hooks.log"
            self.assertTrue(log.is_file())
            self.assertIn("boom-from-session-env", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
