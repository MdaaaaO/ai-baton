"""release_manifest.py: `commit <sha>` plus one sha256 line per git-tracked file, stable order, so a
release tag is a checkable trust point. Run: make -C .claude/context-db test."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import release_manifest as rm  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import hermetic_env  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=hermetic_env(repo))


def head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                           capture_output=True, text=True, env=hermetic_env(repo)).stdout.strip()


class Repo:
    """A throw-away git repo with a handful of tracked files and one untracked file."""

    def __init__(self, tmp: Path):
        self.path = tmp
        git(self.path, "init", "-q")
        (self.path / "a.txt").write_text("alpha\n", encoding="utf-8")
        (self.path / "sub").mkdir()
        (self.path / "sub" / "b.txt").write_text("bravo\n", encoding="utf-8")
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", "init")
        (self.path / "untracked.txt").write_text("not tracked\n", encoding="utf-8")


class Build(unittest.TestCase):
    def test_commit_line_matches_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            text = rm.build(repo.path)
            self.assertEqual(text.splitlines()[0], f"commit {head(repo.path)}")

    def test_every_tracked_file_is_listed_with_its_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            commit, files = rm.parse(rm.build(repo.path))
            self.assertEqual(commit, head(repo.path))
            self.assertEqual(set(files), {"a.txt", "sub/b.txt"})
            self.assertEqual(files["a.txt"], hashlib.sha256(b"alpha\n").hexdigest())
            self.assertEqual(files["sub/b.txt"], hashlib.sha256(b"bravo\n").hexdigest())
            # the untracked file never appears
            self.assertNotIn("untracked.txt", files)

    def test_stable_order_regardless_of_add_order(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            r1, r2 = Path(tmp1), Path(tmp2)
            for root, names in ((r1, ("z.txt", "a.txt", "m.txt")), (r2, ("a.txt", "m.txt", "z.txt"))):
                git(root, "init", "-q")
                for n in names:
                    (root / n).write_text(f"{n}\n", encoding="utf-8")
                    git(root, "add", n)
                git(root, "commit", "-q", "-m", "init")
            paths1 = [line.split("  ", 1)[1] for line in rm.build(r1).splitlines()[1:]]
            paths2 = [line.split("  ", 1)[1] for line in rm.build(r2).splitlines()[1:]]
            self.assertEqual(paths1, paths2)
            self.assertEqual(paths1, sorted(paths1))

    def test_build_writes_to_out_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            out = repo.path / "manifest.txt"
            self.assertEqual(rm.main(["build", "--root", str(repo.path), "--out", str(out)]), 0)
            self.assertEqual(out.read_text(encoding="utf-8"), rm.build(repo.path))


class Verify(unittest.TestCase):
    def test_matching_manifest_has_no_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            self.assertEqual(rm.verify(rm.build(repo.path), repo.path), [])

    def test_catches_a_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            text = rm.build(repo.path)
            (repo.path / "a.txt").write_text("tampered\n", encoding="utf-8")
            problems = rm.verify(text, repo.path)
            self.assertEqual(problems, ["hash mismatch: a.txt"])

    def test_catches_a_file_removed_after_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            text = rm.build(repo.path)
            git(repo.path, "rm", "-q", "a.txt")
            git(repo.path, "commit", "-q", "-m", "drop a.txt")
            problems = rm.verify(text, repo.path)
            self.assertIn("missing: a.txt (in the manifest, not in the tree)", problems)
            self.assertTrue(any(p.startswith("commit mismatch:") for p in problems))

    def test_catches_a_file_added_after_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            text = rm.build(repo.path)
            (repo.path / "new.txt").write_text("new\n", encoding="utf-8")
            git(repo.path, "add", "new.txt")
            git(repo.path, "commit", "-q", "-m", "add new.txt")
            problems = rm.verify(text, repo.path)
            self.assertIn("untracked by the manifest: new.txt", problems)

    def test_rejects_a_manifest_with_no_commit_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            with self.assertRaises(ValueError):
                rm.verify("not a manifest\n", repo.path)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repo(Path(tmp))
            manifest = repo.path / "manifest.txt"
            manifest.write_text(rm.build(repo.path), encoding="utf-8")
            self.assertEqual(rm.main(["verify", str(manifest), "--root", str(repo.path)]), 0)
            (repo.path / "a.txt").write_text("tampered\n", encoding="utf-8")
            self.assertEqual(rm.main(["verify", str(manifest), "--root", str(repo.path)]), 1)


class Tree:
    """A plain directory (no git) standing in for a plugin cache — the fixture `verify_no_git` is for."""

    def __init__(self, tmp: Path):
        self.path = tmp
        (self.path / "a.txt").write_text("alpha\n", encoding="utf-8")
        (self.path / "sub").mkdir()
        (self.path / "sub" / "b.txt").write_text("bravo\n", encoding="utf-8")

    def manifest(self, commit: str = "deadbeef") -> str:
        lines = [f"commit {commit}"]
        for rel in ("a.txt", "sub/b.txt"):
            lines.append(f"{rm.file_sha256(self.path / rel)}  {rel}")
        return "\n".join(lines) + "\n"


class VerifyNoGit(unittest.TestCase):
    def test_matching_tree_has_no_problems_and_a_commit_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(Path(tmp))
            problems, notes = rm.verify_no_git(tree.manifest("cafef00d"), tree.path)
            self.assertEqual(problems, [])
            self.assertIn("commit not checked (no git checkout here): manifest says cafef00d", notes)

    def test_catches_a_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(Path(tmp))
            text = tree.manifest()
            (tree.path / "a.txt").write_text("tampered\n", encoding="utf-8")
            problems, _ = rm.verify_no_git(text, tree.path)
            self.assertEqual(problems, ["hash mismatch: a.txt"])

    def test_catches_a_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(Path(tmp))
            text = tree.manifest()
            (tree.path / "a.txt").unlink()
            problems, _ = rm.verify_no_git(text, tree.path)
            self.assertEqual(problems, ["missing: a.txt (in the manifest, not in the tree)"])

    def test_an_unlisted_file_is_a_note_not_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(Path(tmp))
            text = tree.manifest()
            (tree.path / "extra.txt").write_text("extra\n", encoding="utf-8")
            problems, notes = rm.verify_no_git(text, tree.path)
            self.assertEqual(problems, [])
            self.assertIn("untracked by the manifest: extra.txt", notes)

    def test_pycache_and_pyc_are_skipped_outright(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(Path(tmp))
            text = tree.manifest()
            (tree.path / "__pycache__").mkdir()
            (tree.path / "__pycache__" / "a.cpython-312.pyc").write_text("x", encoding="utf-8")
            (tree.path / "mod.pyc").write_text("x", encoding="utf-8")
            problems, notes = rm.verify_no_git(text, tree.path)
            self.assertEqual(problems, [])
            self.assertEqual([n for n in notes if "pyc" in n or "__pycache__" in n], [])

    def test_cli_no_git_flag_reports_problems_as_notes_go_to_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(Path(tmp))
            manifest = tree.path / "manifest.txt"
            manifest.write_text(tree.manifest(), encoding="utf-8")
            self.assertEqual(rm.main(["verify", str(manifest), "--root", str(tree.path), "--no-git"]), 0)
            (tree.path / "a.txt").write_text("tampered\n", encoding="utf-8")
            self.assertEqual(rm.main(["verify", str(manifest), "--root", str(tree.path), "--no-git"]), 1)


if __name__ == "__main__":
    unittest.main()
