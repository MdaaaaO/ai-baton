"""The engine CLIs' exit-code contract: 0 ok · 1 the thing asked about is absent · 2 a usage or I/O error — one stderr
line, never a traceback. Every case runs against a throw-away store (CONTEXT_ROOT), never the workspace's.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"


class ExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("WORKSPACE_")}
        self.env["CONTEXT_ROOT"] = str(Path(self.tmp.name) / ".context")
        self.run_("kb.py", "init", "--blank")

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / script), *args], env=self.env, capture_output=True, text=True, cwd=BIN)

    def assertExit(self, rc: int, script: str, *args: str, silent: bool = False) -> None:
        p = self.run_(script, *args)
        self.assertEqual(p.returncode, rc, f"{script} {' '.join(args)}: {p.stderr}")
        self.assertNotIn("Traceback", p.stderr)
        if rc and not silent:
            self.assertTrue(p.stderr.strip(), f"{script} {' '.join(args)}: exit {rc} without a message")

    def test_kit_profile_missing_arguments_are_usage_errors(self):
        for cmd in ("template", "identity-source", "get", "mode-hint"):
            self.assertExit(2, "kit_profile.py", cmd)
        self.assertExit(2, "kit_profile.py", "no-such-command")
        self.assertExit(1, "kit_profile.py", "get", "tracker.no_such_key", silent=True)  # callers test the status (pr-watch)
        self.assertExit(0, "kit_profile.py", "name")

    def test_kb_usage_absent_and_ok(self):
        self.assertExit(2, "kb.py")                                   # argparse: no subcommand
        self.assertExit(2, "kb.py", "get", "nodot", "x")             # malformed key (was exit 1)
        self.assertExit(2, "kb.py", "discover")                      # usage (was exit 1)
        self.assertExit(2, "kb.py", "set", "github.person", "x", "v", "--from", "nonsense")
        self.assertExit(1, "kb.py", "get", "github.person", "nobody")  # absent row
        self.assertExit(1, "kb.py", "discover", "teletext.page", "100")  # no manifest covers it
        self.assertExit(0, "kb.py", "discover", "--all")

    def test_kb_io_error_is_exit_2(self):
        cfg = next(Path(self.env["CONTEXT_ROOT"]).rglob("config.json"))
        cfg.write_text("{not json", encoding="utf-8")
        self.assertExit(2, "kb.py", "config", "systems")

    def test_discover_all_survives_a_malformed_manifest(self):
        store = next(Path(self.env["CONTEXT_ROOT"]).rglob("config.json")).parent
        d = store / "_discovery"
        d.mkdir(exist_ok=True)
        (d / "zzz.json").write_text(json.dumps({"facts": [{"key": "a.b.c", "target": "row", "tool": "user", "verify": "v"}]}),
                                    encoding="utf-8")
        self.assertExit(0, "kb.py", "discover", "--all")

    def test_deprecated_get_alias_reads_systems_and_warns_once(self):
        # one flag per capability: `slack.enabled` / `github.signed_commits` are retired — `kit_profile.py get` still answers them
        # for one release, from the `systems.*` flag they now mean, with a one-line stderr warning; stdout carries
        # only the value, same shape and exit code as a normal `get`.
        self.run_("kb.py", "config-set", "systems.slack", "true")
        aliased = self.run_("kit_profile.py", "get", "slack.enabled")
        direct = self.run_("kit_profile.py", "get", "systems.slack")
        self.assertEqual(aliased.returncode, 0, aliased.stderr)
        self.assertEqual(aliased.stdout, direct.stdout)
        self.assertEqual(aliased.stdout.strip(), "true")
        self.assertEqual(aliased.stderr.strip(), "kit_profile: slack.enabled is deprecated — read systems.slack")
        self.run_("kb.py", "config-set", "systems.signed_commits", "true")
        aliased2 = self.run_("kit_profile.py", "get", "github.signed_commits")
        self.assertEqual(aliased2.returncode, 0, aliased2.stderr)
        self.assertEqual(aliased2.stdout.strip(), "true")
        self.assertIn("github.signed_commits is deprecated — read systems.signed_commits", aliased2.stderr)


if __name__ == "__main__":
    unittest.main()
