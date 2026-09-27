"""kit_verify.py's env-store checks and --stale, on throw-away stores. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import kb  # noqa: E402
import kit_profile  # noqa: E402
import kit_verify  # noqa: E402


class EnvStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / "reference" / "env"
        self._saved = (kb.ENV, kit_profile.ENV_DIR)
        kb.ENV = self.env
        kit_profile.ENV_DIR = self.env
        kb.init_blank()

    def tearDown(self):
        kb.ENV, kit_profile.ENV_DIR = self._saved
        self.tmp.cleanup()

    def check(self) -> list[str]:
        errors: list[str] = []
        kit_verify.check_env_store(errors)
        return errors

    def edit(self, **top):
        cfg = kb.load_config()
        cfg.update(top)
        kb.save_config(cfg)

    def test_blank_store_passes(self):
        self.assertEqual(self.check(), [])

    def test_missing_store_and_invalid_json(self):
        (self.env / "config.json").unlink()
        self.assertTrue(any("no configuration: env store" in e for e in self.check()))
        (self.env / "config.json").write_text("{not json", encoding="utf-8")
        self.assertTrue(any("invalid JSON" in e for e in self.check()))

    def test_environment_name_rules(self):
        self.edit(environment="")
        self.assertTrue(any("'environment' must name this environment" in e for e in self.check()))
        self.edit(environment="Acme Corp")
        self.assertTrue(any("is not a lowercase slug" in e for e in self.check()))
        self.edit(environment="acme-2")
        self.assertEqual(self.check(), [])

    def test_missing_keys_and_systems(self):
        cfg = kb.load_config()
        del cfg["labels"]
        del cfg["systems"]["slack"]
        cfg["systems"]["notion"] = "yes"
        cfg["systems"]["snowflake"] = True
        kb.save_config(cfg)
        errors = self.check()
        self.assertTrue(any("missing top-level key 'labels'" in e for e in errors), errors)
        self.assertTrue(any("systems.slack missing" in e for e in errors), errors)
        self.assertTrue(any("systems.notion must be true/false" in e for e in errors), errors)
        self.assertTrue(any("renamed system flag(s) systems.snowflake" in e for e in errors), errors)

    def test_optional_keys_may_be_absent_but_not_wrong(self):
        cfg = kb.load_config()
        del cfg["commits"]
        kb.save_config(cfg)
        self.assertEqual(self.check(), [])
        self.edit(commits={"default": "haiku", "repos": {"a/b": "free", "c/d": "odd"}})
        errors = self.check()
        self.assertTrue(any("commits.default must be one of" in e for e in errors), errors)
        self.assertTrue(any("commits.repos[c/d] = 'odd'" in e for e in errors), errors)
        self.edit(commits=[])
        self.assertTrue(any("commits must be an object" in e for e in self.check()))

    def test_tracker_key_regex(self):
        self.edit(tracker={"kind": "jira", "key_regex": "", "close_reasons": {}})
        self.assertTrue(any("tracker.key_regex missing" in e for e in self.check()))
        self.edit(tracker={"kind": "jira", "key_regex": r"\b(KEY)-(\d+)\b", "close_reasons": {}})
        self.assertTrue(any("exactly one capture group" in e for e in self.check()))
        self.edit(tracker={"kind": "jira", "key_regex": r"\b(KEY-\d+", "close_reasons": {}})
        self.assertTrue(any("does not compile" in e for e in self.check()))
        self.edit(tracker={"kind": "jira", "key_regex": r"\b(KEY-\d+)\b", "close_reasons": {}})
        self.assertEqual(self.check(), [])

    def test_tracker_kind_enum(self):
        self.edit(tracker={"kind": "linear", "key_regex": "", "close_reasons": {}})
        errors = self.check()
        self.assertTrue(any("tracker.kind 'linear' is not one of" in e for e in errors), errors)
        for kind in kit_profile.TRACKER_KINDS:
            self.edit(tracker={"kind": kind, "key_regex": r"(#\d+)" if kind != "none" else "", "close_reasons": {}})
            self.assertEqual([e for e in self.check() if "tracker.kind" in e], [])

    def test_null_or_wrong_typed_sections_are_problems_not_tracebacks(self):
        # `tracker: null` raised AttributeError inside the verifier
        self.edit(tracker=None)
        errors = self.check()
        self.assertTrue(all("Traceback" not in e for e in errors))
        self.assertEqual([e for e in errors if "tracker" in e], [])  # null tracker = no tracker, nothing to check
        self.edit(tracker="jira")
        self.assertTrue(any("tracker must be an object" in e for e in self.check()))
        self.edit(systems=["slack"])
        errors = self.check()
        self.assertTrue(any("systems must be an object" in e for e in errors))
        self.assertFalse(any("missing (true/false)" in e for e in errors), errors)  # one problem, one line
        self.edit(systems=None)
        errors = [e for e in self.check() if "systems" in e]
        self.assertEqual(len(errors), 1, errors)  # null = nothing set: the one `missing … migrate adds them` line
        self.assertIn("missing (true/false)", errors[0])

    def test_github_close_reasons_are_checked(self):
        self.edit(tracker={"kind": "github", "key_regex": r"#(\d+)", "close_reasons": {"done": "completed"}})
        errors = self.check()
        self.assertTrue(any("tracker.close_reasons.wont_do missing" in e for e in errors), errors)


class StaleFlag(unittest.TestCase):
    def run_verify(self, *args: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".context"
            env = {**os.environ, "CONTEXT_ROOT": str(root)}
            subprocess.run([sys.executable, str(BIN / "kb.py"), "init", "--blank"], env=env, check=True, capture_output=True)
            return subprocess.run([sys.executable, str(BIN / "kit_verify.py"), *args], env=env, cwd=BIN, capture_output=True, text=True)

    def test_stale_lists_units_reviewed_longer_ago_than_n_days(self):
        r = self.run_verify("--stale", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("~ stale:", r.stderr)
        r = self.run_verify("--stale", "1")  # every unit reviewed before yesterday — with no --stale nothing is listed
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.count("~ stale:"), int(r.stdout.rsplit(", ", 1)[-1].split()[0]) if "stale" in r.stdout else 0)

    def test_stale_without_a_value_is_a_usage_error_not_a_traceback(self):
        r = self.run_verify("--stale")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
