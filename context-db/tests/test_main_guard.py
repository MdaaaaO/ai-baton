""".github/scripts/main-guard.sh — the push-to-main backstop's decision logic: retries an empty
commit-to-PR lookup before flagging a direct push, never retries a forced push, and never reads a failed
`gh api` call as "no match". Driven through the environment exactly as the workflow step calls it, with a
stub `gh` on PATH (no network, no token) that answers a scripted sequence of lookup results.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SCRIPT = KIT / ".github" / "scripts" / "main-guard.sh"

# Each call to `gh api .../commits/<sha>/pulls` consumes the next line of $MAIN_GUARD_TEST_SEQ (a scripted
# match count, or FAIL for a failing call); `gh issue list/comment/create` are logged to
# $MAIN_GUARD_TEST_LOG so a test can tell whether the commit was flagged without parsing issue bodies.
GH_STUB = """#!/bin/sh
case "$1" in
  api)
    case "$2" in
      */commits/*/pulls)
        n=$(( $(cat "$MAIN_GUARD_TEST_COUNT" 2>/dev/null || echo 0) + 1 ))
        echo "$n" > "$MAIN_GUARD_TEST_COUNT"
        line="$(sed -n "${n}p" "$MAIN_GUARD_TEST_SEQ")"
        if [ "$line" = "FAIL" ]; then
          echo '{"message":"stub failure"}' >&2
          exit 1
        fi
        echo "${line:-0}"
        ;;
      *) echo 0 ;;
    esac
    ;;
  issue)
    case "$2" in
      list) printf '' ;;
      comment) echo "comment $3" >> "$MAIN_GUARD_TEST_LOG"; exit 0 ;;
      create) echo "create" >> "$MAIN_GUARD_TEST_LOG"; exit 0 ;;
      *) exit 2 ;;
    esac
    ;;
  *) exit 2 ;;
esac
"""


class MainGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        stub = Path(self.tmp.name)
        (stub / "gh").write_text(GH_STUB)
        (stub / "gh").chmod(0o755)
        self.seq_file = stub / "seq"
        self.count_file = stub / "count"
        self.log_file = stub / "log"
        self.env = {
            **os.environ,
            "PATH": f"{stub}{os.pathsep}{os.environ.get('PATH', '')}",
            "GH_TOKEN": "stub",
            "REPO": "example/widgets",
            "SHA": "a1b2c3d4e5f60718293a4b5c6d7e8f901234abcd",
            "ACTOR": "someone",
            "FORCED": "false",
            "MAIN_GUARD_ATTEMPTS": "3",
            "MAIN_GUARD_PAUSE_SECONDS": "0",
            "MAIN_GUARD_TEST_SEQ": str(self.seq_file),
            "MAIN_GUARD_TEST_COUNT": str(self.count_file),
            "MAIN_GUARD_TEST_LOG": str(self.log_file),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_guard(self, responses: list[str], forced: str = "false", attempts: str = "3") -> subprocess.CompletedProcess:
        self.seq_file.write_text("\n".join(responses) + "\n")
        env = {**self.env, "FORCED": forced, "MAIN_GUARD_ATTEMPTS": attempts}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)

    def flagged(self) -> bool:
        return self.log_file.exists() and self.log_file.read_text().strip() != ""

    def test_empty_then_match_passes_without_flagging(self):
        p = self.run_guard(["0", "0", "1"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(self.flagged(), p.stdout)
        self.assertEqual(self.count_file.read_text().strip(), "3")

    def test_every_attempt_empty_flags_a_direct_push(self):
        p = self.run_guard(["0", "0", "0"])
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.flagged())
        self.assertIn("create", self.log_file.read_text())
        self.assertIn("direct push", p.stdout)
        # Retried exactly MAIN_GUARD_ATTEMPTS times, never past it.
        self.assertEqual(self.count_file.read_text().strip(), "3")

    def test_failing_gh_api_call_fails_the_step_without_flagging(self):
        p = self.run_guard(["FAIL"])
        self.assertEqual(p.returncode, 1)
        self.assertFalse(self.flagged(), "a swallowed gh api error must never read as a flagged direct push")
        self.assertIn("gh api", p.stderr)
        self.assertIn("failed", p.stderr)

    def test_forced_push_is_flagged_even_with_a_match(self):
        p = self.run_guard(["1"], forced="true")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.flagged())
        self.assertIn("force-push", p.stdout)
        # A forced push is flagged regardless of the match — no retry needed, exactly one lookup.
        self.assertEqual(self.count_file.read_text().strip(), "1")

    def test_attempts_and_pause_are_overridable_from_the_environment(self):
        # A match on the very last allowed attempt still passes; attempts=2 means no third call happens.
        p = self.run_guard(["0", "1"], attempts="2")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.count_file.read_text().strip(), "2")


if __name__ == "__main__":
    unittest.main()
