"""sign-queue enqueue.sh generated job — the per-commit signature check after --force-with-lease/--rebase/
--onto must fail closed when `git log $OLDTIP..HEAD` itself errors (the shape of an unresolvable OLDTIP,
e.g. a --force-with-lease sha the local clone never fetched the object for). The check used to run inside
`for sc in $(git ... log ...); do`, and a POSIX shell's `set -e` does not catch a command substitution
failing inside a `for ... in $(...)` word list (only a bare simple command, such as a variable assignment,
trips it) — so a failing `git log` there silently emptied the loop, `unsigned` stayed "", and the job went
on to attempt the push as though every commit had been checked. Fixed by capturing the command substitution
on its own line first (`revs=$(...)`), so `set -e` catches the failure and the job stops before ever
calling `git push`. This drives `enqueue.sh` and the generated job end to end with a real ssh signing key,
so a regression shows up as the push step actually being reached, not just as a changed code path.
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
from tests import extend_git_config, hermetic_env  # noqa: E402

BOGUS_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"  # well-formed hex, no such object anywhere


def _env(tmp: Path, origin: Path, allowed: Path, key: Path) -> dict:
    env = {k: v for k, v in hermetic_env(tmp).items() if not k.startswith("SIGN_QUEUE_")}
    cfg = {
        "user.name": "t", "user.email": "t@example.invalid", "gpg.format": "ssh",
        "user.signingkey": str(key), "gpg.ssh.allowedSignersFile": str(allowed),
        "remote.origin.url": str(origin),
    }
    return extend_git_config(env, cfg)


@unittest.skipUnless(shutil.which("ssh-keygen") and shutil.which("git"), "ssh-keygen and git needed")
class ForceWithLeaseCheckFailsClosed(unittest.TestCase):
    """An already-pushed branch, a --force-with-lease sha that git log cannot resolve as a range: the
    generated job must stop at the failing check, never reach `git push`."""

    def _build_pushed_repo(self, tmp: Path):
        origin, wt = tmp / "origin.git", tmp / "wt"
        key = tmp / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        allowed = tmp / "allowed_signers"
        allowed.write_text("t@example.invalid " + (tmp / "key.pub").read_text())
        env = _env(tmp, origin, allowed, key)
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], env=env, check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], env=env, check=True)
        git = ["git", "-C", str(wt)]
        subprocess.run([*git, "remote", "add", "origin", str(origin)], env=env, check=True)
        (wt / "a.txt").write_text("a\n")
        subprocess.run([*git, "add", "a.txt"], env=env, check=True)
        subprocess.run([*git, "commit", "-q", "-S", "-m", "chore: seed"], env=env, check=True)
        subprocess.run([*git, "push", "-q", "-u", "origin", "main"], env=env, check=True)
        return wt, origin, env

    def test_unresolvable_oldtip_stops_the_job_before_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wt, origin, env = self._build_pushed_repo(tmp)
            remote_before = subprocess.run(["git", "-C", str(origin), "rev-parse", "main"], env=env,
                                            capture_output=True, text=True, check=True).stdout.strip()
            (wt / "b.txt").write_text("b\n")
            msg = tmp / "msg.txt"
            msg.write_text("fix: add b\n")
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            enqueue_env = dict(env, CONTEXT_ROOT=str(ctx))
            subprocess.run(["git", "-C", str(wt), "add", "b.txt"], env=env, check=True)
            r = subprocess.run(["sh", str(ENQUEUE), "leasebug", str(wt), "main", str(msg),
                                "--force-with-lease", BOGUS_SHA, "--files", "b.txt", "--ticket", "none",
                                "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                               env=enqueue_env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            jobs = list((ctx / "state" / "sign-queue").glob("*-leasebug.sh"))
            self.assertEqual(len(jobs), 1, r.stdout + r.stderr)

            run = subprocess.run(["sh", str(jobs[0])], env=env, capture_output=True, text=True, timeout=60)
            out = run.stdout + run.stderr
            self.assertNotEqual(run.returncode, 0, "an unresolvable OLDTIP must fail the job, not succeed")
            # the fail-closed proof: the check's own git-log failure must be the reason the job stopped —
            # `git push` must never have been reached (its distinct rejection text is otherwise unmistakable).
            self.assertNotIn("rejected", out, f"push was attempted despite the check failing:\n{out}")
            self.assertNotIn("stale info", out, f"push was attempted despite the check failing:\n{out}")
            self.assertNotIn("UNSIGNED", out, "this must fail at the git-log step itself, not the check's "
                                               "own (working) unsigned-commit branch")
            remote_after = subprocess.run(["git", "-C", str(origin), "rev-parse", "main"], env=env,
                                           capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(remote_before, remote_after, "nothing must ever reach the remote")


if __name__ == "__main__":
    unittest.main()
