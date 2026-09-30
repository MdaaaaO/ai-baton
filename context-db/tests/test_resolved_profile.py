"""#370: `kit_profile.py session-env --update <file>` prints a short "resolved profile" block to stdout — the
SessionStart context, never `$CLAUDE_ENV_FILE` — so a session never spends a turn re-reading a stable env value
(tracker kind/key_regex/url_template, `github.review_bot`, the true `systems.*` flags, the footer line). The block
is byte-budgeted (`RESOLVED_PROFILE_BUDGET`), never raises with a missing store or key (placeholders instead), and
never prints a value shaped like a secret. Every probe below runs on a `mktemp` CONTEXT_ROOT — never the live
workspace. Fact-shaped literals are assembled at run time. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402


def run(script: str, *args: str, root: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in ("WORKSPACE_TZ", "CLAUDE_CODE_SESSION_ID")}
    e.update({"CONTEXT_ROOT": str(root), **(env or {})})
    return subprocess.run([sys.executable, str(BIN / script), *args], env=e, cwd=BIN, capture_output=True, text=True)


def write_config(root: Path, cfg: dict) -> None:
    env_dir = root / "reference" / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


BLANK_SYSTEMS = {n: False for n in ("jira", "slack", "notion", "datalake", "airflow", "dbt",
                                    "aws_sso", "incident_io", "lattice", "signed_commits")}


class HookOutput(unittest.TestCase):
    """The block reaches the SessionStart context (stdout of `session-env --update`), never the env file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        self.scratch = Path(self.tmp.name) / "scratch"
        self.env = {"KIT_SCRATCH": str(self.scratch)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_made_up_values_appear_in_stdout_not_the_env_file(self):
        systems = dict(BLANK_SYSTEMS, signed_commits=True, dbt=True)
        write_config(self.root, {
            "environment": "junk-env",
            "tracker": {"kind": "github", "key_regex": "", "url_template": "https://example.invalid/{repo}/{key}"},
            "github": {"org": "junk-org", "review_bot": "junk-bot[bot]"},
            "systems": systems,
        })
        envfile = Path(self.tmp.name) / "envfile"
        r = run("kit_profile.py", "session-env", "--update", str(envfile), root=self.root, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(kit_profile.RESOLVED_PROFILE_HEADER, r.stdout)
        self.assertIn("kind=github", r.stdout)
        self.assertIn("url_template=https://example.invalid/{repo}/{key}", r.stdout)
        self.assertIn("github.review_bot=junk-bot[bot]", r.stdout)
        self.assertIn("systems on: dbt, signed_commits", r.stdout)  # sorted
        self.assertIn("footer: none yet", r.stdout)
        # none of it lands in the env file — that channel is exports only
        out = envfile.read_text(encoding="utf-8")
        self.assertNotIn(kit_profile.RESOLVED_PROFILE_HEADER, out)
        self.assertNotIn("junk-bot[bot]", out)

    def test_footer_line_follows_session_register(self):
        write_config(self.root, {"environment": "junk-env", "systems": dict(BLANK_SYSTEMS)})
        envfile = Path(self.tmp.name) / "envfile"
        r = run("kit_profile.py", "session-env", "--update", str(envfile), root=self.root, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("footer: none yet — `session-register` records the name", r.stdout)
        (self.root / "sessions").mkdir(parents=True, exist_ok=True)
        r = run("session.py", "register", "--name", "kit-resolved-profile-test", "--no-stats", root=self.root, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run("kit_profile.py", "session-env", "--update", str(envfile), root=self.root, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("footer: session `kit-resolved-profile-test`", r.stdout)

    def test_missing_store_gives_placeholders_and_rc_zero(self):
        missing_root = self.root  # never created
        envfile = Path(self.tmp.name) / "envfile"
        r = run("kit_profile.py", "session-env", "--update", str(envfile), root=missing_root, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("kind=<unset> key_regex=<unset> url_template=<unset>", r.stdout)
        self.assertIn("github.review_bot=<unset>", r.stdout)
        self.assertIn("systems on: <none>", r.stdout)
        self.assertIn("footer: none yet", r.stdout)

    def test_a_missing_key_alone_is_a_placeholder_not_a_crash(self):
        write_config(self.root, {"environment": "junk-env"})  # no tracker/github/systems sections at all
        envfile = Path(self.tmp.name) / "envfile"
        r = run("kit_profile.py", "session-env", "--update", str(envfile), root=self.root, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("kind=<unset>", r.stdout)
        self.assertIn("systems on: <none>", r.stdout)


class SecretSafety(unittest.TestCase):
    """A secret-shaped config value must never reach the block — `_profile_value` redacts it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        self.scratch = Path(self.tmp.name) / "scratch"
        self.env = {"KIT_SCRATCH": str(self.scratch)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_fake_token_shaped_review_bot_is_redacted_everywhere(self):
        fake_token = "ghp_" + "x" * 24  # token-SHAPED, not a real credential
        write_config(self.root, {
            "environment": "junk-env",
            "tracker": {"kind": "github"},
            "github": {"review_bot": fake_token},
            "systems": dict(BLANK_SYSTEMS),
        })
        envfile = Path(self.tmp.name) / "envfile"
        r = run("kit_profile.py", "session-env", "--update", str(envfile), root=self.root, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(fake_token, r.stdout)
        self.assertNotIn(fake_token, r.stderr)
        self.assertNotIn(fake_token, envfile.read_text(encoding="utf-8"))
        self.assertIn("github.review_bot=<redacted>", r.stdout)

    def test_profile_value_redacts_every_secret_shape_directly(self):
        fake_values = ["ghp_" + "a" * 24, "AKIA" + "B" * 16, "xoxb-" + "1" * 12, "sk-ant-" + "c" * 24]
        for v in fake_values:
            with mock.patch.object(kit_profile, "get", lambda key, v=v: v if key == "tracker.kind" else None):
                self.assertEqual(kit_profile._profile_value("tracker.kind"), "<redacted>", v)


class BlockShape(unittest.TestCase):
    """Direct coverage of `resolved_profile_block`'s own contract: never over budget, truncated with a marker
    rather than silently cut."""

    def test_stays_under_budget_with_every_system_on(self):
        cfg_systems = {n: True for n in ("jira", "slack", "notion", "datalake", "airflow", "dbt",
                                          "aws_sso", "incident_io", "lattice", "signed_commits")}
        with mock.patch.object(kit_profile, "get", lambda key, default=None: {
            "systems": cfg_systems,
            "tracker.kind": "github",
            "tracker.key_regex": "",
            "tracker.url_template": "https://example.invalid/{repo}/issues/{key}",
            "github.review_bot": "some-bot[bot]",
        }.get(key, default)):
            with mock.patch.object(kit_profile, "session_name", lambda: ""):
                block = kit_profile.resolved_profile_block()
        self.assertLessEqual(len(block.encode("utf-8")), kit_profile.RESOLVED_PROFILE_BUDGET)
        self.assertIn("systems on:", block)

    def test_an_oversized_block_is_cut_to_budget_with_a_marker(self):
        huge = ["x" * 2000]
        with mock.patch.object(kit_profile, "resolved_profile_lines", lambda: huge):
            block = kit_profile.resolved_profile_block()
        self.assertLessEqual(len(block.encode("utf-8")), kit_profile.RESOLVED_PROFILE_BUDGET)
        self.assertTrue(block.rstrip("\n").endswith(kit_profile.RESOLVED_PROFILE_MARKER.strip()))

    def test_default_empty_config_never_raises(self):
        with mock.patch.object(kit_profile, "get", lambda key, default=None: default):
            with mock.patch.object(kit_profile, "session_name", lambda: ""):
                block = kit_profile.resolved_profile_block()
        self.assertIn("<unset>", block)
        self.assertIn("<none>", block)


if __name__ == "__main__":
    unittest.main()
