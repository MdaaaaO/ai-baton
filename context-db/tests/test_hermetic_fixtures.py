"""Regression coverage for the git/HOME isolation every fixture that spawns git needs: a developer's own
commit.gpgsign / gpg.format / system git config must never make a context-db test fixture fail, because a
fixture never git-commits with the calling process's real environment. A hostile ~/.gitconfig (commit.gpgsign
= true, gpg.format = ssh, a signing key that cannot exist) simulates exactly the failure mode reported against
main: any `git commit` that inherits it fails at once. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from tests.test_review_gate import Repo  # noqa: E402
from tests.test_sign_queue_home import _seed_repo  # noqa: E402


def _hostile_home(tmp: Path) -> Path:
    """A HOME whose global git config forces commit signing through a signer that cannot exist. A fixture that
    builds its subprocess env from a bare dict(os.environ) — instead of overriding HOME itself — inherits this
    and fails its first `git commit`; one that isolates itself never notices it is there."""
    home = tmp / "hostile-home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[commit]\n\tgpgsign = true\n"
        "[gpg]\n\tformat = ssh\n"
        "[user]\n\tname = Hostile\n\temail = hostile@example.invalid\n"
        "\tsigningkey = " + str(tmp / "no-such-signing-key") + "\n"
    )
    return home


class ReviewGateRepoIgnoresTheHostHome(unittest.TestCase):
    """test_review_gate.py's Repo() commits fixture history on every construction; it must never inherit the
    calling process's real git config."""

    def test_repo_construction_survives_a_hostile_ambient_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hostile = _hostile_home(tmp)
            with mock.patch.dict(os.environ, {"HOME": str(hostile)}):
                r = Repo(str(tmp))  # sh() raises AssertionError if a `git commit` here fails
            self.assertTrue((r.root / ".git").is_dir())


class SignQueueSeedIgnoresTheHostHome(unittest.TestCase):
    """test_sign_queue_home.py's _seed_repo() seeds a throw-away repo with a real commit; it must never inherit
    the calling process's real git config either."""

    def test_seed_repo_survives_a_hostile_ambient_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hostile = _hostile_home(tmp)
            root = tmp / "ws"
            root.mkdir()
            with mock.patch.dict(os.environ, {"HOME": str(hostile)}):
                wt = _seed_repo(root)  # raises CalledProcessError (check=True) if a `git commit` here fails
            self.assertTrue((wt / ".git").is_dir())


class SetupShScenariosIgnoreTheHostHome(unittest.TestCase):
    """setup_sh_scenarios.sh seeds several git-tracked fixtures by hand (a git-tracked plugin dir, a seeded
    `.claude/` for the stale-seed and install-mode scenarios); every one of them must ignore the shell's own
    ambient HOME, not only the HOME= it prefixes onto setup.sh itself."""

    def test_every_scenario_survives_a_hostile_ambient_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hostile = _hostile_home(tmp)
            env = dict(os.environ, HOME=str(hostile))
            r = subprocess.run(["sh", str(HERE / "setup_sh_scenarios.sh")], env=env, capture_output=True,
                               text=True, timeout=600)
        self.assertEqual(r.returncode, 0, "\n" + r.stdout + "\n" + r.stderr)
        self.assertIn("setup.sh scenarios: all passed", r.stdout)
        self.assertNotIn("FAIL", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
