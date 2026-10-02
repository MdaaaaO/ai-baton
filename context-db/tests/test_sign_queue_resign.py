"""sign-queue no-op re-stack (fix, not the numbered issue — described in words): a stacked branch's
`--onto` re-stack whose upstream tip has not moved since the branch was last rebased onto it (wherever
that ran — no signing key, so no `-S`) used to be a no-op — git left every existing commit exactly as it
was, including one made earlier without `-S`, while the job's own new commit (staged fresh, `commit -S
-F`) was signed. The job reported a signed HEAD and pushed, hiding the unsigned commit beneath it. Fixed
two ways: `enqueue.sh` always
force-rebases (`-S -f`) so the range is replayed (and re-signed) every time, and the generated job verifies
every commit about to be pushed — not just HEAD — refusing the push (parked, `UNSIGNED <sha>`) if any of
them is unsigned. This drives `enqueue.sh` and the generated job script end to end with a real ssh signing
key, so a regression shows up as an unsigned commit actually reaching the remote, not just as a changed
code path. Needs ssh-keygen + a real git. Run: make -C .claude/context-db test."""
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
    """hermetic_env(tmp) plus the ssh-signing config every git call in this file needs, layered on through
    GIT_CONFIG_COUNT/KEY/VALUE (the override that reaches a call regardless of NOSYSTEM/GLOBAL, same as
    test_sign_queue_home.py's HostileValues fixture)."""
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
class NoOpRestackReSigns(unittest.TestCase):
    """local unsigned commit + unchanged base + --onto: the job must re-sign (or refuse), never push a
    commit whose %G? is N."""

    def _build_stack(self, tmp: Path):
        """origin (bare) with a signed `main`, and a local `stack` branch off main's tip that carries one
        commit made WITHOUT -S — the pre-existing unsigned commit a queued re-stack must not leave behind.
        Returns (wt, origin, base_sha, env, presig_sha, presig_sig)."""
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
        base = subprocess.run([*git, "rev-parse", "HEAD"], env=env, capture_output=True, text=True,
                              check=True).stdout.strip()
        subprocess.run([*git, "checkout", "-q", "-b", "stack"], env=env, check=True)
        (wt / "b.txt").write_text("b\n")
        subprocess.run([*git, "add", "b.txt"], env=env, check=True)
        # the trap this reproduces: a commit made without a signing step — a plain `commit`, no -S, sitting
        # between the (unmoved) upstream base and the fix this test then stages and enqueues. Git only signs
        # on -S or commit.gpgsign=true, neither given here, even with signingkey configured.
        subprocess.run([*git, "commit", "-q", "-m", "docs: unsigned change"], env=env, check=True)
        presig_sha = subprocess.run([*git, "rev-parse", "HEAD"], env=env, capture_output=True, text=True,
                                     check=True).stdout.strip()
        presig_sig = subprocess.run([*git, "log", "-1", "--format=%G?"], env=env, capture_output=True,
                                     text=True, check=True).stdout.strip()
        return wt, origin, base, env, presig_sha, presig_sig

    def test_the_pre_rebase_commit_is_unsigned_before_the_job_runs(self):
        # sanity check on the fixture itself, not the fix
        with tempfile.TemporaryDirectory() as tmp:
            *_, presig_sig = self._build_stack(Path(tmp))[1:]
        self.assertEqual(presig_sig, "N")

    def test_onto_re_stack_over_an_unmoved_base_never_pushes_an_unsigned_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wt, origin, base, env, presig_sha, presig_sig = self._build_stack(tmp)
            self.assertEqual(presig_sig, "N")
            (wt / "c.txt").write_text("c\n")
            msg = tmp / "msg.txt"
            msg.write_text("fix: add c\n")
            ctx = tmp / "ws" / ".context"
            ctx.mkdir(parents=True)
            enqueue_env = dict(env, CONTEXT_ROOT=str(ctx))
            subprocess.run(["git", "-C", str(wt), "add", "c.txt"], env=env, check=True)
            r = subprocess.run(["sh", str(ENQUEUE), "onto-resign", str(wt), "stack", str(msg), "--onto",
                                f"main:{base}", "--new-branch", "--files", "c.txt", "--ticket", "none",
                                "--epic", "none", "--pr", "1", "--summary", "s", "--by", "t"],
                               env=enqueue_env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            jobs = list((ctx / "state" / "sign-queue").glob("*-onto-resign.sh"))
            self.assertEqual(len(jobs), 1, r.stdout + r.stderr)
            run = subprocess.run(["sh", str(jobs[0])], env=env, capture_output=True, text=True, timeout=60)
            # the pushed commits (there may be more than the local HEAD now the earlier commit was re-signed
            # under a new sha) must all be signed — the actual proof the fix is after, not this exit code.
            # A refused push never creates the remote branch at all: origin/stack then doesn't resolve,
            # which is exactly "nothing landed" for this check (log stays empty, not an error).
            log_run = subprocess.run(["git", "-C", str(wt), "log", "--format=%H %G? %s", "origin/stack"],
                                     env=env, capture_output=True, text=True)
            log = log_run.stdout if log_run.returncode == 0 else ""
            unsigned_rows = [ln for ln in log.splitlines() if ln.split()[1] != "G"] if log.strip() else []
            self.assertEqual(unsigned_rows, [],
                             f"an unsigned commit reached the remote: {unsigned_rows}\njob output:\n"
                             f"{run.stdout}{run.stderr}")
            self.assertNotIn(presig_sha, log, "the earlier unsigned commit must be re-created (re-signed), "
                                               "not pushed as-is")
            if run.returncode != 0:
                # the fix's other legal outcome: refuse the push outright rather than land an unsigned row
                self.assertIn("UNSIGNED", run.stdout + run.stderr)
                self.assertEqual(log.strip(), "", "a refused job must not have pushed anything either")


if __name__ == "__main__":
    unittest.main()
