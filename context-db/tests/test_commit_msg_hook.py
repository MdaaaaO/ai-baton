"""hooks/commit-msg picks its checker: the committed tree's own copy first, the hook-relative copy as the fallback."""
from __future__ import annotations
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import hermetic_env

HOOK_SRC = Path(__file__).resolve().parents[2] / "hooks" / "commit-msg"


def stub(name: str) -> str:
    """A checker that only records who ran: its name goes to $HOOK_TRACE, exit 0."""
    return f'import os, sys\nopen(os.environ["HOOK_TRACE"], "a").write("{name}\\n")\nsys.exit(0)\n'


class HookChecker(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.trace = self.tmp / "trace"
        self.msg = self.tmp / "msg"
        self.msg.write_text("feat(kit): add a thing\n")
        self.env = {**hermetic_env(self.tmp), "HOOK_TRACE": str(self.trace)}
        self.env.pop("KIT_SKIP_COMMIT_STYLE", None)

    def put(self, root: Path, rel: str, text: str) -> Path:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return f

    def hook_in(self, root: Path) -> Path:
        h = self.put(root, "hooks/commit-msg", HOOK_SRC.read_text())
        h.chmod(0o755)
        return h

    def git(self, *a: str, cwd: Path) -> None:
        subprocess.run(["git", *a], cwd=cwd, env=hermetic_env(self.tmp), check=True, capture_output=True)

    def repo(self, name: str) -> Path:
        r = self.tmp / name
        r.mkdir()
        self.git("init", "-q", "-b", "main", cwd=r)
        return r

    def run_hook(self, hook: Path, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(hook), str(self.msg)], cwd=cwd, env=self.env, capture_output=True, text=True)

    def ran(self) -> list[str]:
        return self.trace.read_text().split() if self.trace.exists() else []

    def test_tree_checker_wins_over_the_hook_relative_one(self):
        kit = self.repo("kit")
        self.put(kit, "context-db/bin/commit_style.py", stub("tree"))
        hook_home = self.tmp / "plugin"
        self.put(hook_home, "context-db/bin/commit_style.py", stub("hook-relative"))
        r = self.run_hook(self.hook_in(hook_home), kit)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ran(), ["tree"])

    def test_hook_relative_checker_is_the_fallback(self):
        other = self.repo("other")  # a repo that opted in with core.hooksPath: no checker of its own
        hook_home = self.tmp / "plugin"
        self.put(hook_home, "context-db/bin/commit_style.py", stub("hook-relative"))
        r = self.run_hook(self.hook_in(hook_home), other)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ran(), ["hook-relative"])

    def test_worktree_is_judged_by_its_own_checker(self):
        main = self.repo("main-checkout")
        self.put(main, "context-db/bin/commit_style.py", stub("old"))
        hook = self.hook_in(main)  # core.hooksPath points at the main checkout's hooks/
        self.git("add", "-A", cwd=main)
        self.git("commit", "-q", "-m", "init", cwd=main)
        wt = self.tmp / "wt"
        self.git("worktree", "add", "-q", "-b", "topic", str(wt), cwd=main)
        (wt / "context-db/bin/commit_style.py").write_text(stub("new"))  # the branch changes the checker
        r = self.run_hook(hook, wt)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ran(), ["new"])

    def test_no_checker_anywhere_passes(self):
        other = self.repo("other")
        hook_home = self.tmp / "bare"
        r = self.run_hook(self.hook_in(hook_home), other)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ran(), [])

    def test_a_failing_checker_refuses_the_commit(self):
        kit = self.repo("kit")
        self.put(kit, "context-db/bin/commit_style.py", "import sys\nsys.exit(1)\n")
        r = self.run_hook(self.hook_in(self.tmp / "plugin"), kit)
        self.assertEqual(r.returncode, 1)
        self.assertIn("commit refused", r.stderr)


if __name__ == "__main__":
    unittest.main()
