"""#74: the pr-review / pr-scan scripts find the workspace's `.context/` through `kit_profile.py context`, never from
their own location — on a plugin install the kit sits in Claude Code's plugin cache, whose parent is no workspace.
A copy of the kit laid out like the plugin cache (no git, `.claude-plugin/plugin.json`) runs each script from a
temp workspace; the path it fails to read (no config.json is seeded) must be the workspace's, not the cache's.
Without a workspace the scripts stop with one line naming PR_REVIEW_HOME. Stdlib unittest, no network (every
script stops at its config before any `gh` call). Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SCRIPTS = {  # script (relative to the kit) → argv after it
    "skills/pr-scan/pr-scan.sh": [],
    "skills/pr-review/scripts/fetch-context.sh": ["o/r", "1"],
    "skills/pr-review/scripts/reply-threads.sh": ["preview"],
    "skills/pr-review/scripts/submit-review.sh": ["preview"],
    "skills/pr-review/scripts/trivial-check.py": ["o/r", "1"],
}


def _env() -> dict:
    drop = ("CONTEXT_ROOT", "CLAUDE_PROJECT_DIR", "PR_REVIEW_HOME", "PR_SCAN_OUT")
    return {k: v for k, v in os.environ.items() if k not in drop}


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class PluginLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kit-prhome-test."))
        cls.kit = cls.tmp / "cache" / "ai-baton" / "0.0.0"  # the plugin cache: no git, no workspace beside it
        shutil.copytree(KIT / "skills", cls.kit / "skills")
        shutil.copytree(KIT / "context-db" / "bin", cls.kit / "context-db" / "bin",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(KIT / ".claude-plugin", cls.kit / ".claude-plugin")
        cls.ws = cls.tmp / "home" / "ws"
        (cls.ws / ".context" / "reference" / "env").mkdir(parents=True)
        (cls.ws / ".context" / "reference" / "env" / "config.json").write_text("{}\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def run_script(self, rel, cwd, **env):
        path = self.kit / rel
        cmd = [sys.executable, str(path)] if rel.endswith(".py") else ["bash", str(path)]
        return subprocess.run([*cmd, *SCRIPTS[rel]], cwd=cwd, env={**_env(), "HOME": str(self.tmp / "home"), **env},
                              capture_output=True, text=True, timeout=60)

    def test_state_is_the_workspaces(self):
        want = str(self.ws / ".context" / "state" / "pr-review")
        for rel in SCRIPTS:
            with self.subTest(script=rel):
                r = self.run_script(rel, self.ws, PR_SCAN_OUT=str(self.tmp / "scan-out"))
                self.assertNotEqual(r.returncode, 0)  # no config.json seeded
                self.assertIn(want, r.stderr)
                self.assertNotIn(str(self.tmp / "cache" / ".context"), r.stderr)

    def test_no_workspace_stops_naming_the_override(self):
        nowhere = self.tmp / "home" / "elsewhere"
        nowhere.mkdir(exist_ok=True)
        for rel in SCRIPTS:
            if rel.endswith(".py"):
                continue  # trivial-check runs under pr-scan / submit-review, which refuse first
            with self.subTest(script=rel):
                r = self.run_script(rel, nowhere)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("no workspace .context/ found", r.stderr)
                self.assertIn("PR_REVIEW_HOME", r.stderr)

    def test_override_still_wins(self):
        home = self.tmp / "override"
        r = self.run_script("skills/pr-review/scripts/reply-threads.sh", self.tmp / "home", PR_REVIEW_HOME=str(home))
        self.assertIn(str(home / "config.json"), r.stderr)


if __name__ == "__main__":
    unittest.main()
