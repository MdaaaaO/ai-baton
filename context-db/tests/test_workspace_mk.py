"""workspace.mk names the kit by where it was included from, never a `.claude/` literal, and quotes its paths: a root
Makefile that includes it through a kit directory under another name (a plugin root, a checkout) drives that kit's
signq.py, sync.sh and engine, in a workspace whose path contains a space. Dry runs only (`make -n`): nothing is
executed but the recursive make of the ctx_* aliases, which `-n` keeps a dry run. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make")


@unittest.skipUnless(MAKE, "make not installed")
class WorkspaceMkKitRoot(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="workspace-mk-test."))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.ws = tmp / "my ws"
        (self.ws / ".context").mkdir(parents=True)
        (self.ws / "baton").symlink_to(KIT, target_is_directory=True)  # the kit, under a name other than .claude
        (self.ws / "Makefile").write_text("include baton/workspace.mk\n", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("MAKE") and k not in ("MFLAGS", "KIT", "CONTEXT", "CONTEXT_ROOT")}

    def dry(self, *args) -> str:
        r = subprocess.run([MAKE, "-n", *args], cwd=self.ws, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_sign_targets_use_the_included_kit(self):
        out = self.dry("sign_list")
        self.assertIn('python3 "baton/skills/sign-queue/signq.py" list', out)
        self.assertNotIn(".claude/", out)

    def test_claude_sync_uses_the_included_kit(self):
        out = self.dry("claude_sync")
        for line in ('sh "baton/sync.sh"', 'tail -n 3 "baton/sync.log"', 'cat "baton/.sync-status"'):
            self.assertIn(line, out)
        self.assertNotIn(".claude/", out)

    def test_ctx_alias_runs_the_included_engine_on_the_workspace_store(self):
        out = self.dry("ctx_verify")
        # $(CURDIR) is make's own getcwd(3) — resolves symlinks (and macOS's /var -> /private/var), unlike self.ws
        self.assertIn(f"CONTEXT_ROOT=\"{self.ws.resolve() / '.context'}\"", out)  # the workspace's store, space and all
        self.assertIn("verify.py", out)

    def test_kit_override(self):
        self.assertIn('sh "elsewhere/sync.sh"', self.dry("claude_sync", "KIT=elsewhere"))


if __name__ == "__main__":
    unittest.main()
