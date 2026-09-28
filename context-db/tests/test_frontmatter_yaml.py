"""A real YAML parser (PyYAML) cross-checks every unit's frontmatter, and the session registry frontmatter
session.py writes, against this kit's own lenient reader (frontmatter.py). frontmatter.py is deliberately not a
real YAML parser (docs/contributing.md — no YAML library, stdlib only), so it reads shapes a real one would
refuse: an unquoted scalar with ': ' in it is read as a nested mapping by real YAML, not as the whole string.

PyYAML is NOT a kit runtime dependency — only this test imports it, and only the CI job(s) that run this
suite install it (.github/versions.env, .github/workflows/ci.yml). Locally, without it installed, this whole
module skips with one line; under CI (GitHub Actions sets CI=true) a missing import is a hard failure instead,
so the cross-check can never go quiet by accident. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
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

try:
    import yaml
except ImportError:
    yaml = None

IN_CI = bool(os.environ.get("CI"))
SKIP_REASON = "PyYAML not installed (kit runtime stays stdlib-only; the CI job installs it — see .github/versions.env)"


def frontmatter_block(p: Path) -> str:
    parts = fmt.split(p.read_text(encoding="utf-8"))
    assert parts is not None, p
    return "\n".join(parts[0])


@unittest.skipIf(yaml is None and not IN_CI, SKIP_REASON)
class RealYamlCrossCheck(unittest.TestCase):
    def setUp(self):
        if yaml is None:  # only reachable when IN_CI: never a silent skip on a real CI run
            self.fail("PyYAML is not importable under CI=true — the job running this suite must install it "
                      "(pinned in .github/versions.env, see .github/workflows/ci.yml)")

    def test_every_unit_frontmatter_is_valid_yaml(self):
        units = fmt.units(KIT)
        self.assertTrue(units)
        for p in units:
            block = frontmatter_block(p)
            try:
                doc = yaml.safe_load(block)
            except yaml.YAMLError as e:
                self.fail(f"{p.relative_to(KIT)}: frontmatter is not valid YAML — {e}")
            self.assertIsInstance(doc, dict, p)

    def test_yaml_and_frontmatter_py_read_the_same_description(self):
        """Not just 'parses' — the point of quoting is that a real YAML parser and this kit's own lenient one
        must agree on what the value IS, or a skill's trigger text silently differs between Claude Code (a
        real YAML reader) and the kit's own tooling (kit_verify.py's budget, migrate_frontmatter.py)."""
        for p in fmt.units(KIT):
            ours = fmt.unquote(fmt.load(p).get("description", ""))
            theirs = yaml.safe_load(frontmatter_block(p)).get("description", "")
            self.assertEqual(ours, theirs, p)

    def test_session_registry_frontmatter_round_trips_tricky_values(self):
        """session.py writes one frontmatter block per active session (working_on / responsibilities free
        text) — read only by the kit's own parser today; a stricter reader (the ctx-store backend) needs it
        to be real YAML too, exactly like a skill's description. Each of these shapes breaks (or nearly
        breaks) a real YAML parser when written bare: a nested-mapping colon, an issue reference (' #'), a
        sequence-entry dash, a flow separator, and free text that is itself already quoted."""
        cases = [
            "lands: the week file plus the report block",
            "fix PR #261 review",
            "- x looks like a sequence entry",
            ",x looks like a flow separator",
            "'quoted: text'",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {k: v for k, v in os.environ.items() if k not in ("WORKSPACE_TZ", "CLAUDE_CODE_SESSION_ID")}
            env["CONTEXT_ROOT"] = str(root)
            for i, value in enumerate(cases):
                name = f"t-yaml-cross-check-{i}"
                r = subprocess.run([sys.executable, str(BIN / "session.py"), "register", "--name", name,
                                   "--no-stats", "--working", value], env=env, cwd=BIN, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                block = frontmatter_block(root / "sessions" / f"{name}.md")
                try:
                    doc = yaml.safe_load(block)
                except yaml.YAMLError as e:
                    self.fail(f"{value!r}: session registry frontmatter is not valid YAML — {e}\n{block}")
                self.assertEqual(doc["working_on"], value, value)


if __name__ == "__main__":
    unittest.main()
