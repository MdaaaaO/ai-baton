"""type_template.py: a ctx-store type's declared `sections` and `log.section` tied to its template's `## `
headings, plus the inventory check (every template has a type, every template-less type is on an explicit
allow-list). Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bin"))

import type_template as tt  # noqa: E402

# Types with no template of their own: scaffolded nowhere (`session`), or hand-maintained with no body
# (`ledger`) — a brand-new template-less type must be added here on purpose, never fall through silently.
NO_TEMPLATE_TYPES = {"session", "ledger"}

# `default.md` seeds an unknown type; `self-assessment-charter.md` is the self-assessment domain's own
# README seed (`kit-health.py`'s `seed_pairs`), not a doc type.
NO_TYPE_TEMPLATES = {"default", "self-assessment-charter"}


class LiveStoreTies(unittest.TestCase):
    """These hold on `main` today — only `epic` declares `sections`, and it already agrees with its
    template — so they prove nothing by themselves; `FixtureFindings` below proves the function fires."""

    def test_every_declared_section_is_a_template_heading(self):
        for type_path in sorted(tt.TYPES_DIR.glob("*.json")):
            sections, _log_section = tt.type_sections(type_path)
            if not sections:
                continue
            template = tt.TEMPLATES_DIR / f"{type_path.stem}.md"
            self.assertTrue(template.is_file(), f"{type_path.stem}: declares sections but has no template")
            missing = tt.missing_sections(sections, template.read_text(encoding="utf-8"))
            self.assertEqual(missing, [], f"{type_path.stem}: {missing} not a heading of {template.name}")
            repeated = tt.repeated_sections(sections, template.read_text(encoding="utf-8"))
            self.assertEqual(repeated, [], f"{type_path.stem}: {repeated} more than once in {template.name}")

    def test_declared_log_section_is_among_declared_sections(self):
        for type_path in sorted(tt.TYPES_DIR.glob("*.json")):
            sections, log_section = tt.type_sections(type_path)
            self.assertFalse(tt.log_section_untied(sections, log_section),
                              f"{type_path.stem}: log.section {log_section!r} not in {sections!r}")

    def test_every_template_but_default_and_charter_has_a_type_file(self):
        templates = {p.stem for p in tt.TEMPLATES_DIR.glob("*.md")} - NO_TYPE_TEMPLATES
        types = {p.stem for p in tt.TYPES_DIR.glob("*.json")}
        self.assertEqual(templates - types, set(), "template with no type file")

    def test_every_template_less_type_is_on_the_explicit_allow_list(self):
        templates = {p.stem for p in tt.TEMPLATES_DIR.glob("*.md")}
        types = {p.stem for p in tt.TYPES_DIR.glob("*.json")}
        self.assertEqual(types - templates, NO_TEMPLATE_TYPES,
                          "a type without a template must be named in NO_TEMPLATE_TYPES on purpose")


class FixtureFindings(unittest.TestCase):
    """The plain tie test above cannot fail on `main` (only `epic` declares sections, and it already
    agrees) — these feed `missing_sections`/`log_section_untied` fixtures that DO disagree, so the finding
    itself is proven, not just the absence of one today."""

    def test_a_declared_section_missing_from_the_template_is_found(self):
        template = "## Goal\n\n## Notes\n"
        self.assertEqual(tt.missing_sections(["Goal", "Notes"], template), [])
        self.assertEqual(tt.missing_sections(["Goal", "Risks"], template), ["Risks"])

    def test_a_heading_inside_fenced_code_is_not_a_section(self):
        for fence in ("```", "~~~"):
            with self.subTest(fence=fence):
                template = f"## Goal\n\n{fence}markdown\n## Notes\n{fence}\n\n## After\n"
                self.assertEqual(tt.heading_list(template), ["Goal", "After"])
                self.assertEqual(tt.missing_sections(["Goal", "Notes"], template), ["Notes"])

    def test_a_heading_is_read_the_way_the_store_reads_it(self):
        template = "##  Goal  \n## Notes ##\n##\tTabbed\n##NoSpace\n### Deeper\n# Title\n"
        self.assertEqual(tt.heading_list(template), ["Goal", "Notes ##"])

    def test_a_section_named_twice_is_found(self):
        template = "## Goal\n\n## Notes\n\n## Goal\n"
        self.assertEqual(tt.missing_sections(["Goal", "Notes"], template), [])
        self.assertEqual(tt.repeated_sections(["Goal", "Notes"], template), ["Goal"])

    def test_log_section_outside_declared_sections_is_found(self):
        self.assertFalse(tt.log_section_untied(["Goal", "Log"], "Log"))
        self.assertTrue(tt.log_section_untied(["Goal", "Notes"], "Log"))
        self.assertFalse(tt.log_section_untied(["Goal"], None))

    def test_a_fixture_type_file_with_both_problems_is_caught_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            type_path = Path(tmp) / "widget.json"
            type_path.write_text(json.dumps({
                "sections": ["Goal", "Orphan section"],
                "log": {"section": "Untied log section"},
            }), encoding="utf-8")
            sections, log_section = tt.type_sections(type_path)
            template_text = "## Goal\n\nbody\n"
            missing = tt.missing_sections(sections, template_text)
            self.assertEqual(missing, ["Orphan section"])
            self.assertTrue(tt.log_section_untied(sections, log_section))


if __name__ == "__main__":
    unittest.main()
