"""docs/templates/skill/SKILL.md § 1 Detect: the capability-tier gate snippet compares
`kit_profile.py get systems.<flag>` against the literal the CLI actually prints for a false flag (a blank
env store's `systems.*` are false by default) — not Python's `False`, which `get` never prints. Stdlib
unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import io
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
BIN = KIT / "context-db" / "bin"
sys.path.insert(0, str(BIN))

import kb  # noqa: E402
import kit_profile  # noqa: E402

TEMPLATE = KIT / "docs" / "templates" / "skill" / "SKILL.md"
# the backtick-quoted literal the template's Detect step compares `kit_profile.py get systems.<flag>`
# against, e.g. "— `false` →" (the broken form wrote `False`, which the CLI never prints)
GATE_LITERAL = re.compile(r"kit_profile\.py get systems\.<flag>`\s*—\s*`([^`]+)`\s*→")


class SkillTemplateGate(unittest.TestCase):
    """A test never reads the machine's own store: kit_profile/kb point at a throw-away blank one, whose
    `systems.*` are false by construction — the same value the template's snippet is meant to match."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = Path(self.tmp.name) / "reference" / "env"
        self._saved = (kit_profile.ENV_DIR, kb.ENV)
        kit_profile.ENV_DIR = env
        kb.ENV = env
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        kb.init_blank()

    def tearDown(self):
        kit_profile.ENV_DIR, kb.ENV = self._saved
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.tmp.cleanup()

    def run_get(self, key: str) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            rc = kit_profile.cli(["kit_profile.py", "get", key])
        self.assertEqual(rc, 0)
        return out.getvalue().strip()

    def test_template_gate_literal_matches_what_kit_profile_get_actually_prints(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        m = GATE_LITERAL.search(text)
        self.assertIsNotNone(m, "docs/templates/skill/SKILL.md § 1 Detect: gate snippet not found in the "
                              "expected shape (`kit_profile.py get systems.<flag>` — `<value>` →)")
        actual = self.run_get("systems.jira")  # a blank store's systems.* default to false
        self.assertEqual(actual, "false")  # sanity: the CLI prints the JSON literal, never Python's False
        self.assertEqual(m.group(1), actual,
                          "the template compares `kit_profile.py get systems.<flag>` against a value the "
                          "CLI never prints for a false flag")


if __name__ == "__main__":
    unittest.main()
