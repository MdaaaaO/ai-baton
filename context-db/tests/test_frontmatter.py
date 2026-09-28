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

    def test_a_hand_written_single_quoted_metadata_value_is_quoted_not_double_wrapped(self):
        """A hand-written `version: '5'` must be canonicalized to `version: "5"` (metadata is double-quote
        only) — not wrapped a second time around its own quotes (`"'5'"`), which `quote = fmt.quote` (no
        longer unquoting first) used to produce."""
        text = "---\nname: x\ndescription: d\nversion:\nupdated: 2026-09-26\nreviewed: 2026-09-25\nmetadata:\n  version: '5'\n---\nbody\n"
        new, changes = mig.migrate_text(text)
        fm = fmt.parse(new)
        self.assertEqual(fm["metadata"]["version"], '"5"')
        self.assertEqual(fmt.unquote(fm["metadata"]["version"]), "5")
        self.assertEqual(mig.migrate_text(new)[1], [])  # idempotent: quoting an already-quoted value is a no-op

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
            for name in ("frontmatter.py", "fsutil.py", "migrate_frontmatter.py"):
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

    def test_body_read_of_an_undeclared_fact_fails(self):
        # a body that reads a fact via `kit_profile.py get`/`kb.py get` must name it in metadata.facts —
        # caught here, not the first time a session hits the missing key mid-skill
        text = MIGRATED.replace(
            "body line with `requires: [slack]` that must stay untouched",
            "body line with `requires: [slack]` that must stay untouched\n\n"
            "Read `python3 kit_profile.py get tracker.repos` first.",
        )
        errors = verify(text)
        self.assertTrue(any("tracker.repos" in e and "does not declare it in metadata.facts" in e for e in errors), errors)
        declared = text.replace('facts: "slack.channel eng-help,tracker.kind"',
                                 'facts: "slack.channel eng-help,tracker.kind,tracker.repos"')
        self.assertEqual(verify(declared), [])

    def test_bare_whole_section_read_needs_no_declaration(self):
        text = MIGRATED.replace(
            "body line with `requires: [slack]` that must stay untouched",
            "body line with `requires: [slack]` that must stay untouched\n\n"
            "Read `python3 kit_profile.py get tracker` first.",
        )
        self.assertEqual(verify(text), [])

    def test_systems_flag_fact_bypasses_the_manifest_but_checks_membership(self):
        # `systems.*` is not discovered through a manifest (every store carries the whole set) — declaring one
        # only has to name a real capability flag
        ok = MIGRATED.replace('facts: "slack.channel eng-help,tracker.kind"', 'facts: "systems.slack"')
        self.assertEqual(verify(ok), [])
        bad = MIGRATED.replace('facts: "slack.channel eng-help,tracker.kind"', 'facts: "systems.telegram"')
        errors = verify(bad)
        self.assertTrue(any("unknown capability flag 'telegram'" in e for e in errors), errors)

    def test_dynamic_systems_gate_requires_each_concrete_flag_declared(self):
        # a body that reads `get systems.<x>` dynamically still names the concrete flags it gates on elsewhere
        # (pr-review's env-specific enrichment) — each of those must be declared too
        text = MIGRATED.replace(
            "body line with `requires: [slack]` that must stay untouched",
            "body line with `requires: [slack]` that must stay untouched\n\n"
            "Gated by `python3 kit_profile.py get systems.<x>`; jira enrichment needs `systems.jira`.",
        )
        errors = verify(text)
        self.assertTrue(any("systems.jira" in e and "does not declare it in metadata.facts" in e for e in errors), errors)
        declared = text.replace('facts: "slack.channel eng-help,tracker.kind"',
                                 'facts: "slack.channel eng-help,tracker.kind,systems.jira"')
        self.assertEqual(verify(declared), [])

    def test_stale_reviewed_is_reported_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "skills" / "demo" / "SKILL.md"
            p.parent.mkdir(parents=True)
            p.write_text(MIGRATED, encoding="utf-8")
            errors, stale = [], []
            kit_verify.check_unit(p, p.relative_to(tmp), errors, stale, 30, date(2027, 1, 1))
            self.assertEqual(errors, [])
            self.assertEqual(len(stale), 1)

    def test_unquoted_colon_space_scalar_fails_kit_verify(self):
        """The actual shape a real YAML parser refused (`description: …lands: the week file…`) even though
        this module's own lenient parser reads it fine — kit-verify must fail it, unquoted, and pass once
        migrate_frontmatter.py (or a hand edit) quotes it."""
        bad = MIGRATED.replace("description: Demo skill. Use when testing. Not for production.",
                               "description: Demo skill: use when testing. Not for production.")
        errors = verify(bad)
        self.assertTrue(any("'description:' contains ': '" in e for e in errors), errors)
        fixed = MIGRATED.replace("description: Demo skill. Use when testing. Not for production.",
                                 'description: "Demo skill: use when testing. Not for production."')
        self.assertEqual(verify(fixed), [])

    def test_leading_indicator_and_trailing_colon_scalars_fail_kit_verify(self):
        errors = verify(MIGRATED.replace("context: fork", "context: &anchor"))
        self.assertTrue(any("'context:' starts with '&'" in e for e in errors), errors)
        errors = verify(MIGRATED.replace("context: fork", "context: fork:"))
        self.assertTrue(any("'context:' ends with ':'" in e for e in errors), errors)

    def test_balanced_flow_collection_is_not_flagged(self):
        """`arguments: [repo, pr, event]` is a deliberate YAML flow sequence (frontmatter.parse_csv's legacy
        `[a, b]` form) — valid as is; only an unbalanced bracket is a real problem."""
        errors = verify(MIGRATED.replace("context: fork", "context: fork\narguments: [repo, pr]"))
        self.assertEqual([e for e in errors if "arguments" in e], [])
        errors = verify(MIGRATED.replace("context: fork", "context: fork\narguments: [repo, pr"))
        self.assertTrue(any("'arguments:' starts with '[' with no matching ']'" in e for e in errors), errors)


class EscapedQuotesRoundTrip(unittest.TestCase):
    """A description that itself contains a literal `"` (quoting a phrase, e.g. aws-sso-login's `\\"SSO
    session…\\"`) is written as `\\"` inside the double-quoted value — strip_comment's naive `v.find('"', 1)`
    stopped at that escaped quote instead of the real closing one, silently truncating everything after it.
    Exercised for real by test_frontmatter_yaml.py's PyYAML cross-check; this is the always-on (no PyYAML
    needed) version of the same regression."""

    def test_strip_comment_does_not_stop_at_an_escaped_quote(self):
        raw = '"Use whenever it fails with \\"SSO session\\" or before check." # a comment'
        self.assertEqual(fmt.strip_comment(raw), '"Use whenever it fails with \\"SSO session\\" or before check."')

    def test_unquote_decodes_the_escapes_strip_comment_kept(self):
        raw = '"Use whenever it fails with \\"SSO session\\" or before check."'
        self.assertEqual(fmt.unquote(fmt.strip_comment(raw)), 'Use whenever it fails with "SSO session" or before check.')

    def test_full_unit_with_an_escaped_quote_is_not_truncated(self):
        text = MIGRATED.replace(
            "description: Demo skill. Use when testing. Not for production.",
            'description: "Use whenever it fails with \\"SSO session\\" or before check."')
        fm = fmt.parse(text)
        self.assertEqual(fmt.unquote(fm["description"]), 'Use whenever it fails with "SSO session" or before check.')

    def test_doubled_single_quote_is_not_the_close(self):
        # YAML's own escape for a literal `'` inside a single-quoted scalar: `''`
        self.assertEqual(fmt.strip_comment("'it''s fine' # trailing"), "'it''s fine'")


class IsQuoted(unittest.TestCase):
    """The one "already quoted, either style" check kit_verify.py and migrate_frontmatter.py both call —
    `is_quoted_string` stays the stricter double-quote-only check `metadata:` requires."""

    def test_either_quote_style_counts_is_quoted_string_is_double_only(self):
        self.assertTrue(fmt.is_quoted('"double"'))
        self.assertTrue(fmt.is_quoted("'single'"))
        self.assertFalse(fmt.is_quoted("bare"))
        self.assertFalse(fmt.is_quoted(""))
        self.assertFalse(fmt.is_quoted_string("'single'"))
        self.assertTrue(fmt.is_quoted_string('"double"'))


class PlainScalarProblem(unittest.TestCase):
    def test_fine_unquoted_values(self):
        for v in ("", "sonnet", "fork", "true", "6", "a plain sentence.", "[repo, pr, event]", '{a: b}'):
            self.assertIsNone(fmt.plain_scalar_problem(v), v)

    def test_colon_space_and_trailing_colon(self):
        self.assertEqual(fmt.plain_scalar_problem("lands: the week file"), "contains ': ', which YAML reads as a nested mapping")
        self.assertEqual(fmt.plain_scalar_problem("a mapping opener:"), "ends with ':', which YAML reads as an empty mapping")

    def test_leading_indicator_characters(self):
        for c in "]}&*!|>'\"%@`":
            self.assertIsNotNone(fmt.plain_scalar_problem(f"{c}oops"), c)

    def test_unbalanced_flow_collection_is_a_problem_balanced_is_not(self):
        self.assertIsNone(fmt.plain_scalar_problem("[a, b]"))
        self.assertIsNone(fmt.plain_scalar_problem("{a: b}"))
        self.assertIsNotNone(fmt.plain_scalar_problem("[a, b"))
        self.assertIsNotNone(fmt.plain_scalar_problem("{a: b"))

    def test_leading_hash_and_mid_value_comment_are_problems(self):
        """A leading '#' and a ' #' anywhere (not just the description budget check in kit_verify.py) must be
        caught by plain_scalar_problem itself, so no caller (session.py's quoted_value) can forget it — a
        real YAML parser reads either as a comment and truncates the value there."""
        self.assertIsNotNone(fmt.plain_scalar_problem("#leads with a hash"))
        self.assertEqual(fmt.plain_scalar_problem("fix PR #261 review"),
                         "contains ' #', which YAML reads as a comment")
        self.assertIsNone(fmt.plain_scalar_problem("no hash here at all"))

    def test_leading_comma_is_a_problem(self):
        self.assertIsNotNone(fmt.plain_scalar_problem(",oops"))

    def test_leading_dash_question_colon_only_with_a_following_space_or_alone(self):
        for v in ("- oops", "? oops", "-", "?", ":"):
            self.assertIsNotNone(fmt.plain_scalar_problem(v), v)
        for v in ("-5", "-quiet", "?ok", ":ok"):
            self.assertIsNone(fmt.plain_scalar_problem(v), v)

    def test_quote_escapes_backslash_before_quote(self):
        # backslash escaped BEFORE quotes: an already-escaped quote must not be double-escaped
        self.assertEqual(fmt.quote('a "quoted" value'), '"a \\"quoted\\" value"')
        self.assertEqual(fmt.quote(r"a \ backslash"), '"a \\\\ backslash"')
        self.assertEqual(fmt.quote('needs "quotes" and \\ backslashes'), '"needs \\"quotes\\" and \\\\ backslashes"')


class MigrationQuotesTopLevelScalars(unittest.TestCase):
    """migrate_frontmatter.py's generic pass: any top-level scalar a real YAML parser would refuse gets quoted,
    not just the metadata block — the bug this fixes is exactly an unquoted `description:` with ': ' in it."""

    def test_unquoted_description_with_colon_space_is_quoted(self):
        text = MIGRATED.replace("description: Demo skill. Use when testing. Not for production.",
                                "description: Demo skill: use when testing. Not for production.")
        new, changes = mig.migrate_text(text)
        self.assertIn("description: quoted", changes)
        fm = fmt.parse(new)
        self.assertEqual(fmt.unquote(fm["description"]), "Demo skill: use when testing. Not for production.")
        self.assertTrue(fmt.is_quoted_string(fm["description"]))
        self.assertEqual(mig.migrate_text(new)[1], [])  # idempotent

    def test_already_quoted_description_is_left_alone(self):
        text = MIGRATED.replace("description: Demo skill. Use when testing. Not for production.",
                                'description: "Demo skill: use when testing. Not for production."')
        self.assertEqual(mig.migrate_text(text), (text, []))

    def test_already_single_quoted_description_is_left_alone(self):
        """A top-level scalar has no double-quote-only requirement (unlike `metadata:`): a valid single-quoted
        `description: 'Demo: skill'` is already fine YAML and must not be flagged or rewritten."""
        text = MIGRATED.replace("description: Demo skill. Use when testing. Not for production.",
                                "description: 'Demo: skill. Use when testing. Not for production.'")
        self.assertEqual(mig.migrate_text(text), (text, []))
        fm = fmt.parse(text)
        self.assertEqual(fmt.unquote(fm["description"]), "Demo: skill. Use when testing. Not for production.")

    def test_balanced_flow_collection_value_is_not_quoted(self):
        text = MIGRATED.replace("context: fork", "context: fork\narguments: [repo, pr, event]")
        new, changes = mig.migrate_text(text)
        self.assertEqual(changes, [])
        self.assertIn("arguments: [repo, pr, event]", new)

    def test_trailing_comment_survives_quoting(self):
        text = MIGRATED.replace("context: fork", 'context: fork\nlicense: MIT: the kit license  # not a real value')
        new, changes = mig.migrate_text(text)
        self.assertIn("license: quoted", changes)
        self.assertIn('license: "MIT: the kit license"  # not a real value\n', new)


if __name__ == "__main__":
    unittest.main()
