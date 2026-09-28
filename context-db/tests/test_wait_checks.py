"""skills/gh-cli/wait-checks.sh against a stub `gh` (#155) — no network, no real `sleep`. The exit-code table
the script's own header promises: 0 every check passed, 1 at least one did not, 2 bad arguments or a failed
query (reason on stderr, never read as "still pending"), 124 the deadline hit before every check settled. Also
covers the two-poll confirmation (a check-suite with no runs yet clears between polls) and the numeric-PR head
resolution path. Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SCRIPT = KIT / "skills" / "gh-cli" / "wait-checks.sh"
sys.path.insert(0, str(HERE.parent))
from tests.fakes.gh_stub import write_gh_stub  # noqa: E402

REPO = "o/r"
FULL = "a" * 40


@unittest.skipUnless(shutil.which("bash"), "bash needed")
class WaitChecksExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-waitchecks-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.log = self.tmp / "gh.log"

    def run_wait(self, routes: dict, ref: str = FULL, timeout: str = "5", interval: str = "1",
                 default_exit: int = 9) -> subprocess.CompletedProcess:
        write_gh_stub(self.bin, routes, self.log, default_exit=default_exit)
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}
        return subprocess.run(["bash", str(SCRIPT), REPO, ref, timeout, interval],
                               env=env, capture_output=True, text=True, timeout=60)

    def test_all_checks_passed_exits_zero(self):
        r = self.run_wait({
            f"commits/{FULL}/check-runs": "build\tcompleted\tsuccess\n",
            f"commits/{FULL}/check-suites": "",
            f"commits/{FULL}/statuses": "",
        })
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("build\tcompleted\tsuccess", r.stdout)

    def test_a_failed_check_exits_one(self):
        r = self.run_wait({
            f"commits/{FULL}/check-runs": "build\tcompleted\tfailure\n",
            f"commits/{FULL}/check-suites": "",
            f"commits/{FULL}/statuses": "",
        })
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_bad_arguments_exit_two_with_usage(self):
        write_gh_stub(self.bin, {}, self.log)
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}
        r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("wait-checks.sh <owner/repo>", r.stderr)

    def test_a_failed_query_exits_two_never_read_as_still_pending(self):
        r = self.run_wait({
            f"commits/{FULL}/check-runs": (1, "", "gh: simulated failure"),
        })
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("check-runs query failed", r.stderr)

    def test_unfinished_checks_hit_the_deadline_and_exit_124(self):
        r = self.run_wait({
            f"commits/{FULL}/check-runs": "build\tin_progress\t-\n",
            f"commits/{FULL}/check-suites": "",
            f"commits/{FULL}/statuses": "",
        }, timeout="2", interval="1")
        self.assertEqual(r.returncode, 124, r.stdout + r.stderr)
        self.assertIn("deadline hit", r.stderr)

    def test_open_check_suite_with_no_runs_yet_delays_until_the_second_poll(self):
        # A `needs:` job registers late: its suite is open (latest_check_runs_count > 0, not completed) on the
        # first poll, then clears — the finished set must be confirmed unchanged on a further poll before this
        # exits, or a job that never got the chance to run would be missed (the case this script's own header
        # comment documents: "a suite still queued with 0 runs is ignored, so the second poll is what catches it").
        self.bin.mkdir(parents=True, exist_ok=True)
        gh = self.bin / "gh"
        gh.write_text(
            "#!/bin/bash\n"
            f'echo "$*" >> {self.log}\n'
            'case "$*" in\n'
            f'  *"commits/{FULL}/check-runs"*) printf "build\\tcompleted\\tsuccess\\n" ;;\n'
            f'  *"commits/{FULL}/check-suites"*)\n'
            f'    n=$(grep -c "check-suites" {self.log})\n'
            '    [ "$n" -le 1 ] && echo "review-app" || true ;;\n'
            f'  *"commits/{FULL}/statuses"*) : ;;\n'
            '  *) echo "unhandled: $*" >&2; exit 9 ;;\n'
            'esac\n',
            encoding="utf-8",
        )
        gh.chmod(0o755)
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}
        r = subprocess.run(["bash", str(SCRIPT), REPO, FULL, "30", "1"], env=env,
                            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # at least 3 polls: open suite (not finished) -> suite clears (finished once) -> confirmed unchanged
        self.assertGreaterEqual(self.log.read_text().count("check-suites"), 3)

    def test_numeric_pr_resolves_to_its_head_sha_first(self):
        r = self.run_wait({
            "pulls/42": FULL,  # `gh api repos/o/r/pulls/42 --jq .head.sha`
            f"commits/{FULL}/check-runs": "build\tcompleted\tsuccess\n",
            f"commits/{FULL}/check-suites": "",
            f"commits/{FULL}/statuses": "",
        }, ref="42")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"commits/{FULL}/check-runs", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
