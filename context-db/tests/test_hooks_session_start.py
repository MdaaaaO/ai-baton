"""hooks/hooks.json's SessionStart command: its `# ai-baton session-env begin/end` block in
$CLAUDE_ENV_FILE is replaced whole on every start (not skipped once written — a persisted env file
must still pick up a changed CLAUDE_PROJECT_DIR, BATON or plugin option) while every other line in
that file survives untouched, and a session-env failure leaves the file exactly as it was rather
than writing a stale or half-written block. Its `hooks.log` never follows a symlink and never falls
back to a $TMPDIR write when `kit_profile.py scratch` fails; nothing about any of that logging can
stop `workspace-rules` — the always-on session rules — from running. Fact-shaped literals are
assembled at run time. Stdlib unittest. Run: make -C .claude/context-db test."""
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

LOGIN_A = "octo-" + "tester"
LOGIN_B = "octo-" + "tester-two"

BEGIN = "# ai-baton session-env begin"
END = "# ai-baton session-env end"


def run(cmd: str, **env: str) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKSPACE_"))}
    return subprocess.run(["sh", "-c", cmd], env={**base, **env}, capture_output=True, text=True)


class SessionStartReplacesItsBlock(unittest.TestCase):
    def test_running_twice_with_the_same_value_leaves_one_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            envfile = Path(tmp) / "env"
            kwargs = {"CLAUDE_PLUGIN_ROOT": str(KIT), "CLAUDE_ENV_FILE": str(envfile),
                      "CONTEXT_ROOT": "/nonexistent/.context", "KIT_SCRATCH": str(Path(tmp) / "scratch"),
                      "CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN": LOGIN_A}
            self.assertEqual(run(CMD, **kwargs).returncode, 0)
            self.assertEqual(run(CMD, **kwargs).returncode, 0)
            out = envfile.read_text(encoding="utf-8")
            self.assertEqual(out.count(BEGIN), 1)
            self.assertEqual(out.count(END), 1)
            self.assertEqual(out.count(f"export WORKSPACE_GITHUB_LOGIN={LOGIN_A}"), 1)

    def test_a_changed_value_replaces_the_block_and_other_lines_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            envfile = Path(tmp) / "env"
            envfile.write_text("export SOMETHING_ELSE=kept\n", encoding="utf-8")
            scratch = str(Path(tmp) / "scratch")
            r1 = run(CMD, CLAUDE_PLUGIN_ROOT=str(KIT), CLAUDE_ENV_FILE=str(envfile), CONTEXT_ROOT="/nonexistent/.context",
                      KIT_SCRATCH=scratch, CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN=LOGIN_A)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            # a later SessionStart with a changed plugin option (e.g. CLAUDE_PROJECT_DIR or an
            # identity option changing between sessions) must not be skipped by a stale marker
            r2 = run(CMD, CLAUDE_PLUGIN_ROOT=str(KIT), CLAUDE_ENV_FILE=str(envfile), CONTEXT_ROOT="/nonexistent/.context",
                      KIT_SCRATCH=scratch, CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN=LOGIN_B)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            out = envfile.read_text(encoding="utf-8")
            self.assertEqual(out.count(BEGIN), 1)  # exactly one block, not one appended per run
            self.assertEqual(out.count(END), 1)
            self.assertNotIn(f"export WORKSPACE_GITHUB_LOGIN={LOGIN_A}\n", out)  # the old line is gone, not shadowed
            self.assertEqual(out.count(f"export WORKSPACE_GITHUB_LOGIN={LOGIN_B}"), 1)
            self.assertIn("export SOMETHING_ELSE=kept", out)  # a line outside the block is untouched

    def test_no_env_file_is_still_a_quiet_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run(CMD, CLAUDE_PLUGIN_ROOT=str(KIT), CONTEXT_ROOT="/nonexistent/.context",
                    KIT_SCRATCH=str(Path(tmp) / "scratch"))
            self.assertEqual((r.returncode, r.stdout), (0, ""))


class SessionStartLoggingIsSafe(unittest.TestCase):
    def stub_kit(self, tmp: Path, *, scratch_fails: bool = False, ws_marker: str = "") -> Path:
        """A fake `$CLAUDE_PLUGIN_ROOT` whose kit_profile.py stands in for the real one: `scratch`
        either prints a real dir under $KIT_SCRATCH or fails outright (`scratch_fails`), `session-env`
        prints to stderr and fails when $STUB_FAIL is set, else prints one export line, `workspace-rules`
        prints `ws_marker` (so a test can prove it ran) — enough surface for the hook command, none of
        the real engine."""
        bin_dir = tmp / "kit" / "context-db" / "bin"
        bin_dir.mkdir(parents=True)
        stub = bin_dir / "kit_profile.py"
        scratch_body = "sys.exit(1)\n" if scratch_fails else (
            "d = pathlib.Path(os.environ['KIT_SCRATCH']); d.mkdir(parents=True, exist_ok=True); print(d)\n"
        )
        stub.write_text(
            "import os, sys, pathlib\n"
            "cmd = sys.argv[1] if len(sys.argv) > 1 else ''\n"
            "if cmd == 'scratch':\n"
            f"    {scratch_body}"
            "elif cmd == 'session-env':\n"
            "    if os.environ.get('STUB_FAIL'):\n"
            "        print('boom-from-session-env', file=sys.stderr); sys.exit(1)\n"
            "    print('export X=1')\n"
            "elif cmd == 'workspace-rules':\n"
            f"    print({ws_marker!r})\n",
            encoding="utf-8",
        )
        return bin_dir.parent.parent

    def test_a_session_env_failure_prints_one_line_and_is_logged_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_kit = self.stub_kit(tmp_path)
            envfile = tmp_path / "env"
            scratch = tmp_path / "scratch"
            r = run(CMD, CLAUDE_PLUGIN_ROOT=str(fake_kit), CLAUDE_ENV_FILE=str(envfile),
                    KIT_SCRATCH=str(scratch), STUB_FAIL="1")
            self.assertEqual(r.returncode, 0)
            self.assertIn("ai-baton: session-env failed", r.stdout)  # visible in the session, not discarded
            log = scratch / "hooks.log"
            self.assertTrue(log.is_file())
            self.assertIn("boom-from-session-env", log.read_text(encoding="utf-8"))

    def test_a_failed_session_env_leaves_the_env_file_untouched_then_a_later_one_writes_one_block(self):
        """A session-env failure must not write a half-written or stale block — the env file is
        untouched — and the next SessionStart still retries; once it succeeds the file ends with
        exactly one block, not zero and not two."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_kit = self.stub_kit(tmp_path)
            envfile = tmp_path / "env"
            envfile.write_text("export SOMETHING_ELSE=kept\n", encoding="utf-8")
            before = envfile.read_text(encoding="utf-8")
            scratch = tmp_path / "scratch"
            r1 = run(CMD, CLAUDE_PLUGIN_ROOT=str(fake_kit), CLAUDE_ENV_FILE=str(envfile),
                     KIT_SCRATCH=str(scratch), STUB_FAIL="1")
            self.assertEqual(r1.returncode, 0)
            self.assertEqual(envfile.read_text(encoding="utf-8"), before)  # untouched by the failed run
            r2 = run(CMD, CLAUDE_PLUGIN_ROOT=str(fake_kit), CLAUDE_ENV_FILE=str(envfile), KIT_SCRATCH=str(scratch))
            self.assertEqual(r2.returncode, 0, r2.stderr)
            out = envfile.read_text(encoding="utf-8")
            self.assertEqual(out.count("export X=1"), 1)
            self.assertEqual(out.count(BEGIN), 1)
            self.assertEqual(out.count(END), 1)
            self.assertIn("export SOMETHING_ELSE=kept", out)

    def test_scratch_failing_still_runs_workspace_rules_and_writes_nothing_under_tmpdir(self):
        """`scratch` fails outright: the hook must still run `workspace-rules` and surface its
        output, and — unlike the fix's first draft — must never fall back to writing under
        $TMPDIR (a world-writable, shared directory)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_kit = self.stub_kit(tmp_path, scratch_fails=True, ws_marker="WORKSPACE-RULES-RAN")
            fake_tmpdir = tmp_path / "tmpdir"
            fake_tmpdir.mkdir()
            r = run(CMD, CLAUDE_PLUGIN_ROOT=str(fake_kit), TMPDIR=str(fake_tmpdir))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("WORKSPACE-RULES-RAN", r.stdout)
            self.assertEqual(list(fake_tmpdir.iterdir()), [])  # nothing written under $TMPDIR

    def test_a_symlinked_hooks_log_is_never_written_through(self):
        """`hooks.log` being a symlink (planted by another user on a shared host, or left over from
        something else) must not be followed — the hook logs to /dev/null instead of the symlink's
        target, and the target is never created or modified."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_kit = self.stub_kit(tmp_path)
            scratch = tmp_path / "scratch"
            scratch.mkdir()
            evil_target = tmp_path / "evil-target"
            (scratch / "hooks.log").symlink_to(evil_target)
            envfile = tmp_path / "env"
            r = run(CMD, CLAUDE_PLUGIN_ROOT=str(fake_kit), CLAUDE_ENV_FILE=str(envfile),
                    KIT_SCRATCH=str(scratch), STUB_FAIL="1")
            self.assertEqual(r.returncode, 0)
            self.assertFalse(evil_target.exists())  # the symlink was never followed for a write
            self.assertTrue((scratch / "hooks.log").is_symlink())  # and was not replaced either
            self.assertIn("no log available", r.stdout)  # no log path is named once it is a symlink


if __name__ == "__main__":
    unittest.main()
