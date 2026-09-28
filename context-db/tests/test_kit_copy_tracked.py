"""Regression coverage for the fixture kit copy every scenario test builds (setup_sh_scenarios.sh, via
hermetic.sh's kit_copy_tracked): it must come from git's own tracked-file list, never a find/cp over the whole
checkout, so an untracked file sitting in a developer's checkout — a node_modules/, a scratch file, a stray
.context — never leaks into the fixture. Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HERMETIC_SH = HERE / "hermetic.sh"
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402


def _git(args, cwd: Path, home: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, env=hermetic_env(home), check=True, capture_output=True, text=True)


def _seed_repo(tmp: Path) -> Path:
    """A throw-away git checkout standing in for a developer's kit clone: one committed (tracked) file."""
    repo = tmp / "src-repo"
    repo.mkdir()
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(["init", "-q"], repo, tmp)
    _git(["add", "tracked.txt"], repo, tmp)
    _git(["commit", "-q", "-m", "seed"], repo, tmp)
    return repo


def _copy_tracked(src: Path, dest: Path, home: Path) -> subprocess.CompletedProcess:
    """Runs hermetic.sh's kit_copy_tracked exactly as setup_sh_scenarios.sh does: source the file, call the
    function. A separate hermetic HOME/TMPDIR (`home`) keeps this run isolated the same way that script is."""
    script = f'. "{HERMETIC_SH}"\nhermetic_git_env "$1"\nkit_copy_tracked "$2" "$3"\n'
    return subprocess.run(["sh", "-c", script, "_", str(home), str(src), str(dest)],
                          env=hermetic_env(home), capture_output=True, text=True, timeout=60)


class KitCopyTrackedExcludesUntrackedFiles(unittest.TestCase):
    def test_an_untracked_file_never_leaks_into_the_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _seed_repo(tmp)
            (repo / "untracked.txt").write_text("must never appear in the fixture\n", encoding="utf-8")  # never added
            dest = tmp / "dest"
            r = _copy_tracked(repo, dest, tmp / "home")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((dest / "tracked.txt").is_file())
            self.assertFalse((dest / "untracked.txt").exists())

    def test_tracked_files_in_subdirectories_are_still_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _seed_repo(tmp)
            (repo / "sub").mkdir()
            (repo / "sub" / "nested.txt").write_text("nested\n", encoding="utf-8")
            _git(["add", "sub/nested.txt"], repo, tmp)
            _git(["commit", "-q", "-m", "nested"], repo, tmp)
            dest = tmp / "dest"
            r = _copy_tracked(repo, dest, tmp / "home")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((dest / "sub" / "nested.txt").read_text(encoding="utf-8"), "nested\n")


if __name__ == "__main__":
    unittest.main()
