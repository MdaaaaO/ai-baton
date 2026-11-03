"""sign-queue enqueue.sh generated job — the per-commit `%G?` check after --force-with-lease/--rebase/--onto
covers only the commits the push ADDS (`$OLDTIP..HEAD --not --remotes=origin`), never a commit some origin/*
ref already reaches (#656). The check used to walk the plain `$OLDTIP..HEAD` range, so a branch rebased onto a
base branch that GitHub's own web-flow merge button had advanced crossed that unsigned merge commit (its GPG
signature reads N on a host without gpg) and the job parked UNSIGNED for a commit it never made and could
never sign. This drives `enqueue.sh` and the generated job end to end with a real ssh signing key against a
local bare origin: the fix shows up as the push actually landing, and the fail-closed half still holds — a
commit the session itself made unsigned, not yet on origin, parks the job exactly as before.
Needs ssh-keygen + a real git. Run: make -C .claude/context-db test."""
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
class UnsignedCheckSkipsCommitsAlreadyOnOrigin(unittest.TestCase):
    """A pushed topic branch rebased onto a main that an UNSIGNED merge commit advanced on origin: the
    --force-with-lease job must push (the merge commit is origin's, not this job's), while an unsigned commit
    the worktree itself added still parks the job."""

    def _git(self, wt: Path, env: dict, *args: str, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(wt), *args], env=env, check=True, capture_output=True,
                              text=True, **kw)

    def _rev(self, repo: Path, env: dict, ref: str) -> str:
        return self._git(repo, env, "rev-parse", ref).stdout.strip()

    def _build_rebased_topic(self, tmp: Path):
        """main: seed (signed) → M (UNSIGNED merge commit, pushed to origin); topic: seed → a (signed, pushed as
        OLD), then rebased locally onto origin/main so HEAD is seed → M → a'. Returns (wt, origin, env, OLD)."""
        origin, wt = tmp / "origin.git", tmp / "wt"
        key = tmp / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        allowed = tmp / "allowed_signers"
        allowed.write_text("t@example.invalid " + (tmp / "key.pub").read_text())
        env = _env(tmp, origin, allowed, key)
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], env=env, check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], env=env, check=True)
        self._git(wt, env, "remote", "add", "origin", str(origin))
        (wt / "a.txt").write_text("a\n")
        self._git(wt, env, "add", "a.txt")
        self._git(wt, env, "commit", "-q", "-S", "-m", "chore: seed")
        self._git(wt, env, "push", "-q", "-u", "origin", "main")
        # the topic branch, signed and already pushed
        self._git(wt, env, "checkout", "-q", "-b", "topic")
        (wt / "t.txt").write_text("t\n")
        self._git(wt, env, "add", "t.txt")
        self._git(wt, env, "commit", "-q", "-S", "-m", "feat: a")
        self._git(wt, env, "push", "-q", "-u", "origin", "topic")
        old = self._rev(wt, env, "HEAD")
        # main moves on origin by a commit nobody here signed — the shape of GitHub's web-flow merge commit
        self._git(wt, env, "checkout", "-q", "main")
        self._git(wt, env, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty",
                  "-m", "Merge pull request #1 from someone/else")
        self.assertEqual(self._git(wt, env, "log", "-1", "--format=%G?").stdout.strip(), "N")
        self._git(wt, env, "push", "-q", "origin", "main")
        self._git(wt, env, "fetch", "-q", "origin")
        # the session rebases topic onto the moved main: a is replayed (and re-signed), M is now beneath it
        self._git(wt, env, "checkout", "-q", "topic")
        self._git(wt, env, "rebase", "-q", "-S", "origin/main")
        return wt, origin, env, old

    def _enqueue(self, tmp: Path, wt: Path, env: dict, old: str, topic: str) -> Path:
        (wt / "b.txt").write_text("b\n")
        msg = tmp / "msg.txt"
        msg.write_text("fix: add b\n")
        ctx = tmp / "ws" / ".context"
        ctx.mkdir(parents=True)
        self._git(wt, env, "add", "b.txt")
        r = subprocess.run(["sh", str(ENQUEUE), topic, str(wt), "topic", str(msg),
                            "--force-with-lease", old, "--files", "b.txt", "--ticket", "none",
                            "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                           env=dict(env, CONTEXT_ROOT=str(ctx)), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        jobs = list((ctx / "state" / "sign-queue").glob(f"*-{topic}.sh"))
        self.assertEqual(len(jobs), 1, r.stdout + r.stderr)
        return jobs[0]

    def test_a_merge_commit_already_on_origin_does_not_park_the_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wt, origin, env, old = self._build_rebased_topic(tmp)
            job = self._enqueue(tmp, wt, env, old, "crossmerge")
            # the shape under test: the plain range crosses origin's unsigned merge commit, the range the job
            # checks (everything not yet on some origin/* ref) holds only the re-signed a'
            crossed = self._git(wt, env, "log", "--format=%G? %s", f"{old}..HEAD").stdout
            self.assertIn("N Merge pull request", crossed, crossed)
            added = self._git(wt, env, "log", "--format=%G? %s", f"{old}..HEAD", "--not", "--remotes=origin").stdout
            self.assertEqual(added.splitlines(), ["G feat: a"], added)
            run = subprocess.run(["sh", str(job)], env=env, capture_output=True, text=True, timeout=60)
            out = run.stdout + run.stderr
            self.assertNotIn("UNSIGNED", out, f"origin's own merge commit parked the job:\n{out}")
            self.assertEqual(run.returncode, 0, out)
            self.assertIn("pushed ", out)
            self.assertEqual(self._rev(origin, env, "topic"), self._rev(wt, env, "HEAD"),
                             "the rebased branch plus the new commit must have landed on origin")

    def test_an_unsigned_commit_of_our_own_still_parks_the_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wt, origin, env, old = self._build_rebased_topic(tmp)
            # the session's own unsigned commit, not on origin: exactly what the check exists to refuse
            self._git(wt, env, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty",
                      "-m", "chore: made without a key")
            ours = self._rev(wt, env, "HEAD")
            job = self._enqueue(tmp, wt, env, old, "ownunsigned")
            run = subprocess.run(["sh", str(job)], env=env, capture_output=True, text=True, timeout=60)
            out = run.stdout + run.stderr
            self.assertNotEqual(run.returncode, 0, out)
            self.assertIn(f"UNSIGNED {ours}", out, out)
            self.assertNotIn("pushed ", out)
            self.assertEqual(self._rev(origin, env, "topic"), old, "nothing must have reached origin")


if __name__ == "__main__":
    unittest.main()
