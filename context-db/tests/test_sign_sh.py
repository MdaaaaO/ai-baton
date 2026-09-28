"""skills/sign-queue/sign.sh (#155) — the compatibility shim `exec python3 "$(dirname "$0")/signq.py" "$@"`:
argv, stdout and the exit code must pass through unchanged for every subcommand a user might still type by
its old name, and `--dry-run` (the highest-risk path — it is what runs `git push` on the owner's machine)
must never touch the queued job when run through the shim: the job script stays put, exactly as calling
signq.py directly would. Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SCRIPT = KIT / "skills" / "sign-queue" / "sign.sh"


@unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), "bash and python3 needed")
class SignShWrapper(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-signsh-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.queue = self.tmp / "queue"
        self.queue.mkdir()
        self.job = self.queue / "demo.sh"
        self.job.write_text(
            '#!/bin/sh\n# META {"topic": "demo", "repo": "o/r", "branch": "b"}\necho "would push"\n',
            encoding="utf-8")
        self.job.chmod(0o755)

    def run_sign(self, *args: str) -> subprocess.CompletedProcess:
        # CONTEXT_ROOT: signq.py imports kit_profile, which otherwise resolves the workspace's live env store
        # from cwd/the kit's own location; SIGN_QUEUE_DIR alone already redirects the queue itself, this just
        # keeps kit_profile's own module-level path resolution off the real store too.
        env = {"SIGN_QUEUE_DIR": str(self.queue), "CONTEXT_ROOT": str(self.tmp / "ctx"), "PATH": os.environ["PATH"]}
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)

    def test_list_forwards_to_signq_list(self):
        r = self.run_sign("--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 pending", r.stdout)
        self.assertIn(" b ", r.stdout)  # the BRANCH column, from this job's own META

    def test_dry_run_never_executes_the_queued_job(self):
        r = self.run_sign("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run", r.stdout)
        self.assertNotIn("would push", r.stdout, "the job script itself must never run under --dry-run")
        self.assertTrue(self.job.exists(), "a dry run must never consume (delete/rename) the job")

    def test_an_underlying_error_s_exit_code_and_message_pass_through(self):
        r = self.run_sign("show", "no-such-job")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no unique job matches", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
