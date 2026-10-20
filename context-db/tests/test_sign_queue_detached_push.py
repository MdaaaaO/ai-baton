"""sign-queue enqueue.sh generated job — a worktree checked out DETACHED at origin/<branch> (the shape the skill
asks for) must move the remote branch (#608). The job used to push a bare branch name, which git resolves to the
LOCAL refs/heads/<branch>: still at the old tip, so git said "Everything up-to-date", exited 0, and the drain
recorded the job as pushed while the signed commit stayed on the detached HEAD. The job now pushes
HEAD:refs/heads/<branch> and checks the remote tip before printing its "pushed" marker. Drives enqueue.sh and
the generated job end to end with a real ssh signing key. Needs ssh-keygen + a real git.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
ENQUEUE = KIT / "skills" / "sign-queue" / "enqueue.sh"
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402


def _env(tmp: Path, origin: Path, allowed: Path, key: Path) -> dict:
    env = {k: v for k, v in hermetic_env(tmp).items() if not k.startswith("SIGN_QUEUE_")}
    cfg = {
        "user.name": "t", "user.email": "t@example.invalid", "gpg.format": "ssh",
        "user.signingkey": str(key), "gpg.ssh.allowedSignersFile": str(allowed),
        "remote.origin.url": str(origin),
    }
    env.update(GIT_CONFIG_COUNT=str(len(cfg)),
               **{f"GIT_CONFIG_KEY_{i}": k for i, k in enumerate(cfg)},
               **{f"GIT_CONFIG_VALUE_{i}": v for i, v in enumerate(cfg.values())})
    return env


@unittest.skipUnless(shutil.which("ssh-keygen") and shutil.which("git"), "ssh-keygen and git needed")
class DetachedWorktreePushLands(unittest.TestCase):

    def _repo(self, tmp: Path):
        origin, wt = tmp / "origin.git", tmp / "wt"
        key = tmp / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        allowed = tmp / "allowed_signers"
        allowed.write_text("t@example.invalid " + (tmp / "key.pub").read_text())
        env = _env(tmp, origin, allowed, key)
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], env=env, check=True)
        subprocess.run(["git", "init", "-q", "-b", "feat", str(wt)], env=env, check=True)
        git = ["git", "-C", str(wt)]
        subprocess.run([*git, "remote", "add", "origin", str(origin)], env=env, check=True)
        (wt / "a.txt").write_text("a\n")
        subprocess.run([*git, "add", "a.txt"], env=env, check=True)
        subprocess.run([*git, "commit", "-q", "-S", "-m", "chore: seed"], env=env, check=True)
        subprocess.run([*git, "push", "-q", "-u", "origin", "feat"], env=env, check=True)
        # the sign-queue shape: detached at the remote tip; local refs/heads/feat stays behind once we commit
        subprocess.run([*git, "checkout", "-q", "--detach", "feat"], env=env, check=True)
        return wt, origin, env

    def _rev(self, repo: Path, ref: str, env: dict) -> str:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", ref], env=env,
                              capture_output=True, text=True, check=True).stdout.strip()

    def _enqueue_and_run(self, tmp: Path, wt: Path, env: dict, branch: str, topic: str):
        (wt / "b.txt").write_text("b\n")
        msg = tmp / "msg.txt"
        msg.write_text("fix: add b\n")
        ctx = tmp / "ws" / ".context"
        ctx.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["sh", str(ENQUEUE), topic, str(wt), branch, str(msg), "--files", "b.txt",
                            "--ticket", "none", "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                           env=dict(env, CONTEXT_ROOT=str(ctx)), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        jobs = list((ctx / "state" / "sign-queue").glob(f"*-{topic}.sh"))
        self.assertEqual(len(jobs), 1, r.stdout + r.stderr)
        run = subprocess.run(["sh", str(jobs[0])], env=env, capture_output=True, text=True, timeout=60)
        return r, run

    def test_existing_branch_moves_to_the_detached_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wt, origin, env = self._repo(tmp)
            before = self._rev(origin, "feat", env)
            enq, run = self._enqueue_and_run(tmp, wt, env, "feat", "detached")
            out = run.stdout + run.stderr
            self.assertEqual(run.returncode, 0, out)
            head = self._rev(wt, "HEAD", env)
            self.assertNotEqual(head, before, "the job must have made a commit")
            self.assertEqual(self._rev(origin, "feat", env), head, f"origin/feat did not move to HEAD:\n{out}")
            self.assertIn("pushed ", out)
            self.assertEqual(self._rev(wt, "refs/heads/feat", env), before, "the local branch is left alone")

    def test_new_branch_from_a_detached_head_lands_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wt, origin, env = self._repo(tmp)
            enq, run = self._enqueue_and_run(tmp, wt, env, "feat2", "detachednew")
            out = run.stdout + run.stderr
            self.assertIn("--new-branch", enq.stderr)
            self.assertEqual(run.returncode, 0, out)
            self.assertEqual(self._rev(origin, "feat2", env), self._rev(wt, "HEAD", env), out)

    def test_a_push_that_did_not_land_parks_instead_of_saying_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wt, origin, env = self._repo(tmp)
            # a remote that accepts the push and then rewinds the branch: exit 0, but the tip is not HEAD
            before = self._rev(origin, "feat", env)
            hook = origin / "hooks" / "post-receive"
            hook.write_text(f"#!/bin/sh\ngit update-ref refs/heads/feat {before}\n")
            hook.chmod(0o755)
            enq, run = self._enqueue_and_run(tmp, wt, env, "feat", "notlanded")
            out = run.stdout + run.stderr
            self.assertNotEqual(run.returncode, 0, out)
            self.assertIn("PUSH NOT LANDED", out)
            self.assertNotIn("pushed ", out.replace("PUSH NOT LANDED", ""))


if __name__ == "__main__":
    unittest.main()
