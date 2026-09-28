"""`kit_profile.py get --nonempty` — a guard that stops at an unset key AND at one configured but left
blank (`""` / `[]` / `{}`), the gap `ticket-close`'s GitHub adapter hit: a plain `get` exits 1 only for
an absent key, so an empty close-reason string passed the "configured?" check and closed the issue with
`--reason ""`. Stdlib unittest, subprocess against a throw-away store (never the workspace's).
Run: make -C .claude/context-db test."""
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
import kit_profile  # noqa: E402


class IsEmptyPredicate(unittest.TestCase):
    """The pure function behind the flag: falsy containers/strings are "unset", `0`/`False` are not."""

    def test_none_and_blank_containers_are_empty(self):
        for v in (None, "", [], {}):
            self.assertTrue(kit_profile.is_empty(v), v)

    def test_zero_and_false_are_not_empty(self):
        # a close reason or a transition id could legitimately be 0/false-shaped; only "unset" is empty
        for v in (0, 0.0, False, "0", [0], {"a": 0}):
            self.assertFalse(kit_profile.is_empty(v), v)


class GetNonemptyCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("WORKSPACE_")}
        self.env["CONTEXT_ROOT"] = str(Path(self.tmp.name) / ".context")
        self.run_("kb.py", "init", "--blank")

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / script), *args], env=self.env, capture_output=True, text=True, cwd=BIN)

    def config_set(self, key: str, value: str) -> None:
        p = self.run_("kb.py", "config-set", key, value)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_nonempty_value_passes_through(self):
        # the case a plain `get` already handled — --nonempty must not regress it
        self.config_set("tracker.close_reasons.wont_do", '"not planned"')
        p = self.run_("kit_profile.py", "get", "--nonempty", "tracker.close_reasons.wont_do")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "not planned")

    def test_blank_string_close_reason_is_rejected(self):
        # the exact bug: a close reason present in the store but left "" must not read as "configured"
        self.config_set("tracker.close_reasons.wont_do", '""')
        empty = self.run_("kit_profile.py", "get", "--nonempty", "tracker.close_reasons.wont_do")
        self.assertEqual(empty.returncode, 1, empty.stderr)
        self.assertEqual(empty.stdout, "")
        # and a plain `get` (no flag) still returns it — the flag adds a check, it does not remove the value
        plain = self.run_("kit_profile.py", "get", "tracker.close_reasons.wont_do")
        self.assertEqual((plain.returncode, plain.stdout.strip()), (0, ""))

    def test_empty_list_and_object_are_rejected(self):
        self.config_set("domains", "[]")
        p = self.run_("kit_profile.py", "get", "--nonempty", "domains")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.config_set("slack.channels", "{}")
        p = self.run_("kit_profile.py", "get", "--nonempty", "slack.channels")
        self.assertEqual(p.returncode, 1, p.stderr)

    def test_unset_key_is_still_exit_1(self):
        p = self.run_("kit_profile.py", "get", "--nonempty", "tracker.close_reasons.no_such_reason")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertEqual(p.stdout, "")

    def test_missing_key_argument_after_the_flag_is_a_usage_error(self):
        p = self.run_("kit_profile.py", "get", "--nonempty")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertTrue(p.stderr.strip())


if __name__ == "__main__":
    unittest.main()
