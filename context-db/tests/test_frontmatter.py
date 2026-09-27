"""frontmatter.py / migrate_frontmatter.py / kit_verify.check_unit — the Claude Code profile of the Agent Skills
spec. Stdlib unittest, no env store needed. Run: make -C .claude/context-db test."""
from __future__ import annotations
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "bin"))

import frontmatter as fmt  # noqa: E402
import kit_verify  # noqa: E402
import migrate_frontmatter as mig  # noqa: E402

LEGACY = """---
name: demo
description: Demo skill. Use when testing. Not for production.
version: 6
updated: 2026-09-26
reviewed: 2026-09-25
facts: [slack.channel eng-help, tracker.kind]
requires: [slack, airflow]
context: fork
agent: triage
---

# demo

body line with `requires: [slack]` that must stay untouched
"""

MIGRATED = """---
name: demo
description: Demo skill. Use when testing. Not for production.
compatibility: "Designed for Claude Code; needs airflow, slack (systems.*)"
metadata:
  version: "6"
  updated: "2026-09-26"
  reviewed: "2026-09-25"
  requires: "airflow,slack"
  facts: "slack.channel eng-help,tracker.kind"
context: fork
agent: triage
---

# demo

body line with `requires: [slack]` that must stay untouched
"""


class Parser(unittest.TestCase):
    def test_nested_metadata_values_are_raw(self):
        fm = fmt.parse(MIGRATED)
        self.assertEqual(fm["name"], "demo")
        self.assertEqual(fm["metadata"]["version"], '"6"')
        self.assertEqual(fmt.unquote(fm["metadata"]["version"]), "6")
        self.assertTrue(fmt.is_quoted_string(fm["metadata"]["updated"]))
        self.assertFalse(fmt.is_quoted_string("6"))
        self.assertEqual(fmt.requires_of(fm), ["airflow", "slack"])
        self.assertEqual(fmt.facts_of(fm), ["slack.channel eng-help", "tracker.kind"])
        self.assertEqual(fm["context"], "fork")

    def test_legacy_list_form_still_parses(self):
        self.assertEqual(fmt.parse_csv("[slack, airflow]"), ["slack", "airflow"])
        self.assertEqual(fmt.parse_csv('"a,b , c"'), ["a", "b", "c"])
        self.assertEqual(fmt.parse_csv(""), [])

    def test_no_block_and_unterminated_block(self):
        self.assertIsNone(fmt.parse("# no frontmatter\n"))
        self.assertIsNone(fmt.parse("---\nname: x\n"))

    def test_empty_and_whitespace_values_never_raise(self):
        # `metadata: ␠`, a nested key with no value, CRLF endings — each used to reach strip_comment("") → IndexError
        self.assertEqual(fmt.strip_comment(""), "")
        self.assertEqual(fmt.strip_comment("   "), "")
        fm = fmt.parse("---\nname: x\nmetadata: \n  facts:\n  version: \"1\"\r\nmodel: sonnet\r\n---\n")
        self.assertEqual(fm["metadata"], {"facts": "", "version": '"1"'})
        self.assertEqual(fm["model"], "sonnet")

    def test_comments_outside_quotes_are_dropped(self):
        fm = fmt.parse('---\nmetadata:\n  requires: "slack"   # a flag\nmodel: sonnet # cheap\n---\n')
        self.assertEqual(fm["metadata"]["requires"], '"slack"')
        self.assertEqual(fm["model"], "sonnet")

    def test_every_kit_unit_parses_and_declares_metadata(self):
        self.assertTrue((KIT / "docs" / "templates" / "skill" / "SKILL.md").is_file())  # the scaffold, outside skills/
        self.assertFalse((KIT / "skills" / "_template").exists())  # Claude Code would load it as a live skill
        for p in fmt.units(KIT):
            fm = fmt.load(p)
            self.assertIsNotNone(fm, p)
            self.assertNotIn("_unparsed", fm, p)
            self.assertIn("version", fmt.meta(fm), p)
            for k in fmt.KIT_META_KEYS:
                self.assertNotIn(k, fm, f"{p}: {k} still at the top level")

    def test_all_facts_is_sorted_unique(self):
        facts = fmt.all_facts(KIT)
        self.assertEqual(facts, sorted(set(facts)))
        self.assertTrue(facts)


class Migration(unittest.TestCase):
    def test_moves_keys_adds_compatibility_keeps_body(self):
        new, changes = mig.migrate_text(LEGACY)
        self.assertEqual(new, MIGRATED)
        self.assertIn("compatibility: added", changes)

    def test_idempotent(self):
        new, changes = mig.migrate_text(MIGRATED)
        self.assertEqual(changes, [])
        self.assertEqual(new, MIGRATED)

    def test_existing_metadata_wins_and_bare_values_are_quoted(self):
        text = '---\nname: x\ndescription: d\nversion: 2\nmetadata:\n  version: 3  # bumped\n  owner: kit\n---\nbody\n'
        new, changes = mig.migrate_text(text)
        fm = fmt.parse(new)
        self.assertEqual(fm["metadata"]["version"], '"3"')  # the trailing comment is not quoted into the value
        self.assertIn('  version: "3"  # bumped\n', new)  # …and is kept after it
        self.assertEqual(fm["metadata"]["owner"], '"kit"')
        self.assertNotIn("version", {k for k in fm if k != "metadata"})
        self.assertIn("version: dropped (metadata.version already set)", changes)
        self.assertEqual(mig.migrate_text(new)[1], [])

    def test_no_frontmatter_is_left_alone(self):
        self.assertEqual(mig.migrate_text("just a body\n"), ("just a body\n", []))

    def test_moved_key_value_comes_from_the_canonical_parser(self):
        """A MOVED key with nothing after the colon: frontmatter.parse_lines is the one source of truth for what
        a key's value is — migrate_frontmatter must read that value, not re-derive it with its own regex match."""
        text = "---\nname: x\ndescription: d\nversion:\nupdated: 2026-09-26\nreviewed: 2026-09-25\n---\nbody\n"
        lines, _ = fmt.split(text)
        canonical = fmt.parse_lines(lines)["version"]
        self.assertEqual(canonical, {})  # frontmatter.py's own reading: a bare key opens a nested mapping, not ""
        new, changes = mig.migrate_text(text)
        fm = fmt.parse(new)
        self.assertEqual(fm["metadata"]["version"], '""')  # migrate coerces the same non-scalar reading to empty
        self.assertIn("version: → metadata.version", changes)
        self.assertEqual(mig.migrate_text(new)[1], [])  # idempotent

    def test_body_fenced_dashes_are_kept_byte_for_byte(self):
        """The frontmatter boundary is the shared `frontmatter.split` — a `---` that shows up later, inside a
        fenced code block in the body (e.g. a doc illustrating the format), must never be mistaken for the
        closing fence."""
        body = "\n# demo\n\n```\n---\nname: example\n---\n```\n"
        text = f"---\nname: demo\ndescription: d\nversion: 6\nupdated: 2026-09-26\nreviewed: 2026-09-25\n---\n{body}"
        new, changes = mig.migrate_text(text)
        self.assertTrue(new.endswith(body), new)
        self.assertIn("version: → metadata.version", changes)

    def test_cli_accepts_a_relative_path_inside_the_kit(self):
        # the documented invocation runs from the workspace root with `.claude/skills/<x>/SKILL.md`
        import os
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            kit_copy = Path(tmp) / ".claude"
            (kit_copy / "skills" / "demo").mkdir(parents=True)
            (kit_copy / "skills" / "demo" / "SKILL.md").write_text(LEGACY, encoding="utf-8")
            (kit_copy / "context-db" / "bin").mkdir(parents=True)
            for name in ("frontmatter.py", "migrate_frontmatter.py"):
                (kit_copy / "context-db" / "bin" / name).write_bytes((KIT / "context-db" / "bin" / name).read_bytes())
            r = subprocess.run([sys.executable, ".claude/context-db/bin/migrate_frontmatter.py", "--check", ".claude/skills/demo/SKILL.md"],
                               cwd=tmp, capture_output=True, text=True, env={**os.environ})
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("would migrate skills/demo/SKILL.md", r.stdout)
            r = subprocess.run([sys.executable, ".claude/context-db/bin/migrate_frontmatter.py", ".claude/skills/demo/SKILL.md"],
                               cwd=tmp, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("migrated skills/demo/SKILL.md", r.stdout)

    def test_cli_dry_run_writes_nothing_and_check_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "SKILL.md"
            f.write_text(LEGACY, encoding="utf-8")
            self.assertEqual(mig.main(["mig", "--check", str(f)]), 3)
            self.assertEqual(f.read_text(encoding="utf-8"), LEGACY)
            self.assertEqual(mig.main(["mig", str(f)]), 0)
            self.assertEqual(f.read_text(encoding="utf-8"), MIGRATED)
            self.assertEqual(mig.main(["mig", "--check", str(f)]), 0)


def verify(text: str, name: str = "demo", agent: bool = False) -> list[str]:
    """kit_verify.check_unit on one unit written to a temp kit-shaped tree."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / ("agents" if agent else f"skills/{name}") / (f"{name}.md" if agent else "SKILL.md")
        p.parent.mkdir(parents=True)
        p.write_text(text, encoding="utf-8")
        errors: list[str] = []
        kit_verify.check_unit(p, p.relative_to(tmp), errors, [], 0, date(2026, 9, 26))
        return errors


class Verifier(unittest.TestCase):
    def test_migrated_unit_passes(self):
        self.assertEqual(verify(MIGRATED), [])

    def test_legacy_top_level_keys_fail_with_migration_hint(self):
        errors = verify(LEGACY)
        self.assertTrue(any("'version:' belongs under `metadata:`" in e for e in errors), errors)
        self.assertTrue(any("migrate_frontmatter.py" in e for e in errors), errors)

    def test_unknown_top_level_key_fails_and_agent_keys_are_agents_only(self):
        errors = verify(MIGRATED.replace("context: fork\n", "context: fork\nbogus: 1\n"))
        self.assertTrue(any("top-level key 'bogus'" in e for e in errors), errors)
        errors = verify(MIGRATED.replace("context: fork\n", "context: fork\nmaxTurns: 3\n"))
        self.assertTrue(any("top-level key 'maxTurns'" in e for e in errors), errors)
        agent = MIGRATED.replace("context: fork\nagent: triage\n", "maxTurns: 3\ntools: Read\ncolor: cyan\nomitClaudeMd: true\n")
        self.assertEqual(verify(agent, agent=True), [])

    def test_bare_metadata_value_and_bad_version_fail(self):
        errors = verify(MIGRATED.replace('version: "6"', "version: 6"))
        self.assertTrue(any("metadata.version must be a double-quoted string" in e for e in errors), errors)
        errors = verify(MIGRATED.replace('version: "6"', 'version: "6.1"'))
        self.assertTrue(any("is not an integer" in e for e in errors), errors)
        errors = verify(MIGRATED.replace('updated: "2026-09-26"', 'updated: "26.09.2026"'))
        self.assertTrue(any("not YYYY-MM-DD" in e for e in errors), errors)

    def test_requires_needs_compatibility_naming_each_flag(self):
        errors = verify(MIGRATED.replace('compatibility: "Designed for Claude Code; needs airflow, slack (systems.*)"\n', ""))
        self.assertTrue(any("carries `compatibility:" in e for e in errors), errors)
        errors = verify(MIGRATED.replace("needs airflow, slack", "needs slack"))
        self.assertTrue(any("does not name the required flag 'airflow'" in e for e in errors), errors)
        errors = verify(MIGRATED.replace('requires: "airflow,slack"', 'requires: "airflow,slack,teletext"'))
        self.assertTrue(any("'teletext' is not a known capability flag" in e for e in errors), errors)

    def test_bare_scalar_with_a_hash_comment_is_flagged(self):
        errors = verify(MIGRATED.replace("Not for production.", "Not for PR #123 triage."))
        self.assertTrue(any("'description:' contains a `#` comment" in e for e in errors), errors)
        hash_first = [e for e in verify(MIGRATED.replace("context: fork", "context: #fork")) if "'context:' contains a `#` comment" in e]
        self.assertTrue(hash_first and "(kept: '')" in hash_first[0], hash_first)  # YAML keeps nothing for `key: #…`
        self.assertTrue(any("'context:' contains a `#` comment" in e for e in verify(MIGRATED.replace("context: fork", "context: fork\t# comment"))))
        self.assertEqual([e for e in verify(MIGRATED.replace("agent: triage\n", "agent: triage\n# a comment line\n")) if "comment" in e], [])
        self.assertEqual(verify(MIGRATED.replace("description: Demo skill. Use when testing. Not for production.",
                                                 'description: "Demo skill. Use for PR #123. Not for production."')), [])

    def test_name_must_match_directory(self):
        errors = verify(MIGRATED.replace("name: demo", "name: demo-2"))
        self.assertTrue(any("must equal the directory name 'demo'" in e for e in errors), errors)

    def test_missing_metadata_and_retired_keys(self):
        errors = verify("---\nname: demo\ndescription: d\nenvironments: [acme]\n---\n")
        self.assertTrue(any("missing `metadata:`" in e for e in errors), errors)
        self.assertTrue(any("'environments:' is retired" in e for e in errors), errors)

    def test_facts_without_manifest_fail(self):
        errors = verify(MIGRATED.replace("tracker.kind", "teletext.page 100"))
        self.assertTrue(any("has no discovery manifest" in e for e in errors), errors)

    def test_stale_reviewed_is_reported_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "skills" / "demo" / "SKILL.md"
            p.parent.mkdir(parents=True)
            p.write_text(MIGRATED, encoding="utf-8")
            errors, stale = [], []
            kit_verify.check_unit(p, p.relative_to(tmp), errors, stale, 30, date(2027, 1, 1))
            self.assertEqual(errors, [])
            self.assertEqual(len(stale), 1)


if __name__ == "__main__":
    unittest.main()
