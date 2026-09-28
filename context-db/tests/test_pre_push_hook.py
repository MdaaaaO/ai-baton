"""hooks/pre-push (#155) — the kit repo's own `main` is PR-only, enforced client-side before a push ever
leaves the machine: any update whose remote ref is `refs/heads/main` (a plain push, a `push origin
HEAD:main`, or a delete) is refused; every other branch passes untouched. `KIT_ALLOW_MAIN_PUSH=1` is the
documented one-off override. Runs the hook directly against a stdin git would feed it (`<local ref> <local
sha> <remote ref> <remote sha>` per line) — no real remote needed. Stdlib unittest. Run: make -C
.claude/context-db test."""
from __future__ import annotations
import os
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
HOOK = KIT / "hooks" / "pre-push"
FULL = "a" * 40
ZERO = "0" * 40


@unittest.skipUnless(shutil.which("bash"), "bash needed")
class PrePushHook(unittest.TestCase):
    def run_hook(self, lines: list, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        stdin = "".join(f"{a} {b} {c} {d}\n" for a, b, c, d in lines)
        # the caller's env minus the hook's own override, so a developer with KIT_ALLOW_MAIN_PUSH exported
        # still sees the "refused" cases refused; env_extra goes on top
        env = {k: v for k, v in os.environ.items() if k != "KIT_ALLOW_MAIN_PUSH"}
        env.update(env_extra or {})
        return subprocess.run(["bash", str(HOOK), *args], input=stdin, env=env,
                               capture_output=True, text=True, timeout=15)

    def test_a_push_that_updates_main_is_refused(self):
        r = self.run_hook([("refs/heads/topic", FULL, "refs/heads/main", ZERO)], "origin", "https://example.invalid/o/r.git")
        self.assertEqual(r.returncode, 1)
        self.assertIn("origin/main of the kit repo is PR-only", r.stderr)
        self.assertIn("KIT_ALLOW_MAIN_PUSH=1", r.stderr)

    def test_a_push_to_another_branch_passes(self):
        r = self.run_hook([("refs/heads/topic", FULL, "refs/heads/topic", ZERO)], "origin", "https://example.invalid/o/r.git")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")

    def test_deleting_main_is_refused_too(self):
        # a delete push: git feeds the hook an all-zero LOCAL sha and "(delete)" as the local ref, but the
        # REMOTE ref is still refs/heads/main — the check must key off the remote side, not the local one
        r = self.run_hook([("(delete)", ZERO, "refs/heads/main", FULL)], "origin", "https://example.invalid/o/r.git")
        self.assertEqual(r.returncode, 1)

    def test_a_mixed_push_with_one_ref_targeting_main_is_still_refused(self):
        r = self.run_hook([
            ("refs/heads/topic", FULL, "refs/heads/topic", ZERO),
            ("refs/heads/other", FULL, "refs/heads/main", ZERO),
        ], "origin", "https://example.invalid/o/r.git")
        self.assertEqual(r.returncode, 1)

    def test_kit_allow_main_push_overrides(self):
        r = self.run_hook([("refs/heads/topic", FULL, "refs/heads/main", ZERO)], "origin",
                           "https://example.invalid/o/r.git", env_extra={"KIT_ALLOW_MAIN_PUSH": "1"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")

    def test_no_arguments_still_refuses_main_defaulting_the_remote_name(self):
        r = self.run_hook([("refs/heads/topic", FULL, "refs/heads/main", ZERO)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("origin/main", r.stderr)


if __name__ == "__main__":
    unittest.main()
