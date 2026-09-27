"""Engine tests — stdlib unittest, no env store needed. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "bin"))

import kb  # noqa: E402

GH_REASONS = {"completed", "not planned"}   # everything `gh issue close --reason` accepts, minus `duplicate`


class CloseReasons(unittest.TestCase):
    def test_template_ships_gh_strings_under_the_skill_keys(self):
        tmpl = json.loads((KIT / "environment-template" / "config.json").read_text(encoding="utf-8"))
        reasons = tmpl["tracker"]["close_reasons"]
        self.assertEqual(set(reasons), set(kb.CLOSE_REASON_KEYS))
        self.assertTrue(set(reasons.values()) <= GH_REASONS, reasons)

    def test_manifest_derives_the_same_key_set(self):
        manifest = json.loads((KIT / "context-db" / "discovery" / "tracker-github.json").read_text(encoding="utf-8"))
        entry = next(e for e in manifest["facts"] if e["key"] == "tracker.close_reasons")
        derived = json.loads(entry["args"]["template"])
        self.assertEqual(set(derived), set(kb.CLOSE_REASON_KEYS))
        self.assertTrue(set(derived.values()) <= GH_REASONS, derived)

    def test_migrate_rewrites_legacy_keys_and_values(self):
        cfg = {"tracker": {"kind": "github", "close_reasons": {"done": "completed", "wont-do": "not_planned"}}}
        changes = kb.migrate_close_reasons(cfg)
        self.assertEqual(cfg["tracker"]["close_reasons"],
                         {"done": "completed", "wont_do": "not planned", "cancelled": "not planned"})
        self.assertEqual(len(changes), 3)          # key rename, value fix, cancelled added
        self.assertEqual(kb.migrate_close_reasons(cfg), [])  # idempotent
        self.assertEqual(kb.close_reason_problems(cfg), [])

    def test_problems_name_bad_values_and_missing_keys(self):
        cfg = {"tracker": {"kind": "github", "close_reasons": {"done": "completed", "wont_do": "not_planned"}}}
        problems = kb.close_reason_problems(cfg)
        self.assertTrue(any("cancelled missing" in p for p in problems), problems)
        self.assertTrue(any("'not_planned'" in p for p in problems), problems)

    def test_non_github_trackers_are_left_alone(self):
        cfg = {"tracker": {"kind": "jira", "close_reasons": {}}}
        self.assertEqual(kb.migrate_close_reasons(cfg), [])
        self.assertEqual(kb.close_reason_problems(cfg), [])
        self.assertEqual(cfg["tracker"]["close_reasons"], {})

    def test_null_store_and_extra_keys_survive(self):
        cfg = {"tracker": {"kind": "github", "close_reasons": None}}
        kb.migrate_close_reasons(cfg)                   # must not raise on `null`
        self.assertEqual(kb.close_reason_problems(cfg), [])
        cfg["tracker"]["close_reasons"]["duplicate"] = "duplicate"
        self.assertEqual(kb.close_reason_problems(cfg), [])   # extra keys are not the kit's business

    def test_blank_config_migrates_clean(self):
        cfg = kb.blank_config()
        cfg["tracker"]["kind"] = "github"
        kb.migrate_config(cfg)
        self.assertEqual(kb.close_reason_problems(cfg), [])


if __name__ == "__main__":
    unittest.main()


class EmptyManifest(unittest.TestCase):
    def test_empty_facts_need_a_note(self):
        # a capability flag may ship a manifest with no store facts yet — only with a note saying so
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "x.json").write_text(json.dumps({"system": "dbt", "requires": "dbt", "facts": []}), encoding="utf-8")
            (d / "y.json").write_text(json.dumps({"system": "dbt", "requires": "dbt", "facts": [], "note": "no store facts yet"}), encoding="utf-8")
            saved = kb.MANIFEST_DIR
            kb.MANIFEST_DIR = d
            try:
                errs = kb.validate_manifests()
            finally:
                kb.MANIFEST_DIR = saved
            self.assertTrue(any("x.json" in e and "empty only with a 'note'" in e for e in errs), errs)
            self.assertFalse(any("y.json" in e for e in errs), errs)

