"""skills/pr-watch/bot-verdict.sh (#155) — the canonical bot-verdict lookup every merge-gate script reads
instead of its own regex: the emoji on the bot's LATEST review whose `commit_id` prefix-matches the given
head wins (green/yellow/red), no matching Assessment yet is "none", no bot configured (`github.review_bot`
empty, no `PR_WATCH_BOT_LOGIN` override) is exit 2 — never read as "none", a `gh api` failure is exit 1 — never
read as "no verdict yet". Also the 4th-arg fast path (an already-fetched reviews file skips the network call
entirely). Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SCRIPT = KIT / "skills" / "pr-watch" / "bot-verdict.sh"
sys.path.insert(0, str(HERE.parent))
from tests.fakes.gh_stub import write_gh_stub  # noqa: E402

REPO = "o/r"
PR = "7"
FULL = "a" * 40
BOT = "review-bot[bot]"


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "bash and jq needed")
class BotVerdict(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-botverdict-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.log = self.tmp / "gh.log"
        self.env_dir = self.tmp / "ws" / ".context" / "reference" / "env"
        self.env_dir.mkdir(parents=True)
        self.write_config(BOT)

    def write_config(self, review_bot: str) -> None:
        (self.env_dir / "config.json").write_text(
            json.dumps({"github": {"review_bot": review_bot, "sandbox_token_prefix": ""}}), encoding="utf-8")

    def run_verdict(self, reviews: list | None = None, *extra_args: str,
                     routes: dict | None = None) -> subprocess.CompletedProcess:
        r = routes if routes is not None else {f"pulls/{PR}/reviews": json.dumps(reviews or [])}
        write_gh_stub(self.bin, r, self.log)
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
               "CONTEXT_ROOT": str(self.tmp / "ws" / ".context")}
        return subprocess.run(["bash", str(SCRIPT), REPO, PR, FULL, *extra_args],
                               env=env, capture_output=True, text=True, timeout=30)

    @staticmethod
    def review(emoji: str, commit: str = FULL) -> dict:
        return {"user": {"login": BOT}, "commit_id": commit, "body": f"### Assessment: {emoji} looks fine"}

    def test_green_verdict_from_the_latest_matching_review(self):
        r = self.run_verdict([self.review("🔴"), self.review("🟢")])  # latest wins, not the first
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "green")

    def test_yellow_and_red_are_recognised_too(self):
        for emoji, want in (("🟡", "yellow"), ("🔴", "red")):
            r = self.run_verdict([self.review(emoji)])
            self.assertEqual(r.stdout.strip(), want, r.stderr)

    def test_no_matching_assessment_is_none_not_an_error(self):
        r = self.run_verdict([])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "none")

    def test_a_review_on_a_different_commit_does_not_match(self):
        r = self.run_verdict([self.review("🟢", commit="b" * 40)])
        self.assertEqual(r.stdout.strip(), "none")

    def test_no_bot_configured_exits_two_never_read_as_no_verdict_yet(self):
        self.write_config("")
        r = self.run_verdict([])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_gh_api_failure_exits_one_never_read_as_no_change(self):
        r = self.run_verdict(routes={f"pulls/{PR}/reviews": (1, "", "gh: simulated failure")})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("gh api", r.stderr)

    def test_pr_watch_bot_login_override_skips_the_config_lookup(self):
        self.write_config("")  # no store-configured bot at all
        write_gh_stub(self.bin, {f"pulls/{PR}/reviews": json.dumps([self.review("🟢")])}, self.log)
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "CONTEXT_ROOT": str(self.tmp / "ws" / ".context"),
               "PR_WATCH_BOT_LOGIN": BOT}
        r = subprocess.run(["bash", str(SCRIPT), REPO, PR, FULL], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "green", r.stderr)

    def test_reviews_file_argument_skips_the_network_call_entirely(self):
        reviews_file = self.tmp / "reviews.json"
        reviews_file.write_text(json.dumps([self.review("🟢")]), encoding="utf-8")
        self.bin.mkdir(parents=True, exist_ok=True)  # no `gh` on PATH at all: a call would fail loudly
        env = {"PATH": os.environ["PATH"], "CONTEXT_ROOT": str(self.tmp / "ws" / ".context")}
        r = subprocess.run(["bash", str(SCRIPT), REPO, PR, FULL, str(reviews_file)],
                            env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "green", r.stderr)


if __name__ == "__main__":
    unittest.main()
