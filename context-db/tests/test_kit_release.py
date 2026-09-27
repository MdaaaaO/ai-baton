"""workspace.mk's kit_release / kit_release_dry against a temp workspace and a bare origin (#85): a clone workspace
cuts from its `.claude/` by default, a plugin-shaped workspace (no `.claude/`) from `KIT_CHECKOUT`, and without a
checkout the target stops with one line naming `KIT_CHECKOUT`. conventional-release is faked through `_CREL` (a
command-line variable beats the file's `:=`), so no network and no uv. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make")


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if "proxy" not in k.lower() and not k.startswith(("GIT_", "MAKE")) and k not in ("MFLAGS", "CONTEXT_ROOT")}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t")
    return env


@unittest.skipUnless(MAKE, "make not installed")
class KitRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-release-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = _env(self.tmp)
        self.origin = self.tmp / "origin.git"
        seed = self.tmp / "seed"
        self.git("init", "-q", "--bare", str(self.origin), cwd=self.tmp)
        self.git("-c", "init.defaultBranch=main", "init", "-q", str(seed), cwd=self.tmp)
        (seed / "context-db" / "bin").mkdir(parents=True)
        shutil.copy(KIT / "context-db" / "bin" / "kit_profile.py", seed / "context-db" / "bin")
        (seed / "VERSION").write_text("0.1.0\n")
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-qm", "init", cwd=seed)
        self.git("push", "-q", str(self.origin), "HEAD:main", cwd=seed)
        self.git("--git-dir", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/main", cwd=self.tmp)
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        # the fake release tool: prints its arguments and the directory it ran in (the throwaway worktree)
        self.crel = self.tmp / "crel.sh"
        self.crel.write_text('#!/bin/sh\necho "CREL $* in $(basename "$PWD")"\n')
        self.crel.chmod(0o755)

    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True)

    def clone(self, dest: Path) -> Path:
        self.git("clone", "-q", str(self.origin), str(dest), cwd=self.tmp)
        return dest

    def make(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([MAKE, "-s", "-f", str(KIT / "workspace.mk"), f"_CREL={self.crel}", *args],
                              cwd=self.ws, env=self.env, capture_output=True, text=True)

    def assert_dry_ran(self, r, checkout: Path):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CREL release --dry-run in kit_release-run", r.stdout)
        self.assertFalse((self.ws / ".worktrees" / "kit_release-run").exists())  # the throwaway worktree is gone
        wts = subprocess.run(["git", "-C", str(checkout), "worktree", "list"], env=self.env,
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(len(wts.strip().splitlines()), 1, wts)  # and pruned from the checkout

    def test_clone_defaults_to_dot_claude(self):
        checkout = self.clone(self.ws / ".claude")
        self.assert_dry_ran(self.make("kit_release_dry"), checkout)

    def test_plugin_workspace_uses_kit_checkout(self):
        checkout = self.clone(self.tmp / "kit-clone")
        self.assert_dry_ran(self.make("kit_release_dry", f"KIT_CHECKOUT={checkout}"), checkout)

    def test_no_checkout_names_kit_checkout(self):
        for extra in ((), (f"KIT_CHECKOUT={self.tmp / 'missing'}",)):
            with self.subTest(extra=extra):
                r = self.make("kit_release_dry", *extra)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("KIT_CHECKOUT=", r.stdout)
                self.assertNotIn("CREL", r.stdout)
                self.assertFalse((self.ws / ".worktrees").exists())


if __name__ == "__main__":
    unittest.main()
