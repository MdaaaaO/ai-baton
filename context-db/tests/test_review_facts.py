"""`kit_profile.py review-facts` (#48) — the one reader of the pr-review family's GitHub facts. The login is the
identity (plugin option, else `WORKSPACE_GITHUB_LOGIN`); owner, bots, owner teams and the review bot come from the
env store's `github.*`; `.context/state/pr-review/config.json` holds tuning only. A needed fact left unset exits 1
with one stderr line per fact naming the key and where it is set. Stdlib unittest, subprocess against a throw-away
store (never the workspace's). Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"

sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402

LOGIN = "octo-" + "tester"
ORG = "acme-" + "widgets"


class ReviewFactsCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("WORKSPACE_", "CLAUDE_PLUGIN_OPTION_"))}
        self.env["CONTEXT_ROOT"] = str(Path(self.tmp.name) / ".context")
        p = self.run_("kb.py", "init", "--blank")
        self.assertEqual(p.returncode, 0, p.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / script), *args], env=self.env, capture_output=True,
                              text=True, cwd=BIN)

    def config_set(self, key: str, value: str) -> None:
        p = self.run_("kb.py", "config-set", key, value)
        self.assertEqual(p.returncode, 0, p.stderr)

    def facts(self, *args: str) -> subprocess.CompletedProcess:
        return self.run_("kit_profile.py", "review-facts", *args)

    def test_every_fact_resolves_from_identity_and_the_store(self):
        self.env["WORKSPACE_GITHUB_LOGIN"] = LOGIN
        self.config_set("github.org", ORG)
        self.config_set("github.bots", '["dependabot[bot]", "copilot"]')
        self.config_set("github.owner_teams", '["data-eng"]')
        self.config_set("github.review_bot", "copilot")
        p = self.facts("--need", "login,owner")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), {"login": LOGIN, "owner": ORG, "bots": ["dependabot[bot]", "copilot"],
                                                "owner_teams": ["data-eng"], "review_bot": "copilot"})

    def test_the_review_bot_joins_the_bot_list(self):
        self.env["WORKSPACE_GITHUB_LOGIN"] = LOGIN
        self.config_set("github.bots", '["dependabot[bot]"]')
        self.config_set("github.review_bot", "reviewer-bot")
        self.assertEqual(json.loads(self.facts().stdout)["bots"], ["dependabot[bot]", "reviewer-bot"])

    def test_the_plugin_option_wins_over_the_variable(self):
        self.env.update(WORKSPACE_GITHUB_LOGIN="stale", CLAUDE_PLUGIN_OPTION_GITHUB_LOGIN=LOGIN)
        self.assertEqual(json.loads(self.facts().stdout)["login"], LOGIN)

    def test_a_missing_login_names_the_variable(self):
        self.config_set("github.org", ORG)
        p = self.facts("--need", "login,owner")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout)["owner"], ORG)  # the rest still prints; callers stop on the exit code
        self.assertIn("login is not set", p.stderr)
        self.assertIn("WORKSPACE_GITHUB_LOGIN", p.stderr)
        self.assertNotIn("owner is not set", p.stderr)

    def test_a_missing_owner_names_the_store_key(self):
        self.env["WORKSPACE_GITHUB_LOGIN"] = LOGIN
        p = self.facts("--need", "login,owner")
        self.assertEqual(p.returncode, 1)
        self.assertIn("owner is not set", p.stderr)
        self.assertIn("github.org", p.stderr)

    def test_unneeded_facts_may_be_unset(self):
        p = self.facts()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["login"], "")

    def test_an_unknown_fact_is_a_usage_error(self):
        p = self.facts("--need", "login,bogus")
        self.assertEqual(p.returncode, 2)
        self.assertIn("bogus", p.stderr)


class MissingLines(unittest.TestCase):
    def test_one_line_per_empty_needed_fact(self):
        facts = {"login": "", "owner": ORG, "bots": [], "owner_teams": [], "review_bot": ""}
        self.assertEqual(kit_profile.review_facts_missing(facts, ["login", "owner", "bots"]),
                         [f"login is not set — {kit_profile.REVIEW_FACT_SOURCES['login']}",
                          f"bots is not set — {kit_profile.REVIEW_FACT_SOURCES['bots']}"])

    def test_every_fact_has_a_source(self):
        self.assertEqual(set(kit_profile.REVIEW_FACT_SOURCES),
                         set(kit_profile.review_facts({})))


if __name__ == "__main__":
    unittest.main()
