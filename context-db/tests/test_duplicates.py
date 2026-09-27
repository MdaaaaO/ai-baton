"""#19: a mis-resolved merge that repeats a frontmatter key (two `version:` under `metadata:`) or a numbered step in a
unit body fails kit-verify instead of passing with the last value silently kept. Stdlib unittest."""
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
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))
import frontmatter as fmt  # noqa: E402
import kit_verify  # noqa: E402


class DuplicateKeys(unittest.TestCase):
    def test_repeated_keys_are_named_per_level(self):
        block = ["name: x", "metadata:", '  version: "3"', '  updated: "2026-09-26"', '  version: "4"', "name: y"]
        self.assertEqual(fmt.duplicate_keys(block), ["`metadata.version` on lines 3 and 5", "`name` on lines 1 and 6"])

    def test_same_sub_key_under_different_parents_is_fine(self):
        self.assertEqual(fmt.duplicate_keys(["a:", "  v: 1", "b:", "  v: 2", "# a: comment", ""]), [])


class DuplicatedSteps(unittest.TestCase):
    def test_repeated_number_or_text_in_one_list(self):
        body = "1. fetch\n2. verify\n   more detail\n2. verify\n"
        self.assertEqual(kit_verify.duplicated_steps(body), ["body line 4 repeats step `2.` (first on line 2)"])
        body = "1. fetch\n2. verify\n3. verify\n"
        self.assertEqual(kit_verify.duplicated_steps(body), ["body line 3 repeats the text of the step on line 2"])

    def test_separate_lists_and_code_are_not_duplicates(self):
        body = ("## A\n1. fetch\n2. verify\n\n## B\n1. fetch\n2. merge\n\nprose ends a list\n1. again\n"
                "```\n1. code\n1. code\n```\n")
        self.assertEqual(kit_verify.duplicated_steps(body), [])


class EndToEnd(unittest.TestCase):
    def test_kit_verify_fails_a_unit_with_either_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit = Path(tmp) / "skills" / "good-skill"
            shutil.copytree(KIT / "context-db" / "tests" / "fixtures" / "skills" / "good-skill", unit)
            skill = unit / "SKILL.md"
            text = skill.read_text(encoding="utf-8")
            head, sep, body = text.partition("\nmetadata:\n")
            skill.write_text(head + sep + '  version: "99"\n' + body + "\n1. step\n1. step again\n", encoding="utf-8")
            env = {**os.environ, "CONTEXT_ROOT": "/nonexistent/.context"}
            p = subprocess.run([sys.executable, str(BIN / "kit_verify.py"), "--no-env", str(skill)],
                               capture_output=True, text=True, env=env, cwd=KIT)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("frontmatter repeats `metadata.version`", p.stdout + p.stderr)
            self.assertIn("repeats step `1.`", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
