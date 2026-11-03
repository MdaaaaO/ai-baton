"""docs/miss-check.md — the forked critique before ready, close and handoff. Locks the doc (the fixed brief, `CLEAN`,
the four classes, the result-line grammar, the second-miss rule, the log), the wiring (the three skills name the doc and
the result line, the README and layout index it, the three behaviour cases exist) and that the documented log slug is a
doc `new.sh` can scaffold. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import hermetic_env
from tests.test_ctx_adapter import REAL_CTX

KIT = Path(__file__).resolve().parents[2]
BIN = KIT / "context-db" / "bin"
DOC = (KIT / "docs" / "miss-check.md").read_text(encoding="utf-8")
CLASSES = ("CRITERION", "UNTESTED", "ASSUMPTION", "DECISION")
RESULT = re.compile(r"^Miss check: (clean|fixed \d+, open \d+)$")


def section(title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}.*?\n(.*?)(?=^## |\Z)", DOC, re.S | re.M)
    assert m, f"docs/miss-check.md has no '## {title}' section"
    return m.group(1)


class TheDoc(unittest.TestCase):
    def test_the_brief_is_a_fenced_block_with_the_inputs_and_the_two_outcomes(self):
        blocks = re.findall(r"```\n(.*?)```", section("The brief"), re.S)
        self.assertEqual(len(blocks), 1, "the brief is one fixed fenced block")
        brief = blocks[0]
        for needle in ("MISSED <class>: <item>", "exactly one line: CLEAN", "use no", "<artefact>", "<done-when>", "<diff>"):
            self.assertIn(needle, brief)
        for c in CLASSES:
            self.assertIn(c, brief)

    def test_the_fork_mechanics_and_the_model_rule(self):
        text = section("The brief")
        for needle in ("subagent_type: general-purpose", "model: haiku", "model: sonnet", "deep_lines", "git diff --stat"):
            self.assertIn(needle, text)

    def test_the_result_line_grammar(self):
        text = section("Result line")
        self.assertIn("Miss check: clean", text)
        self.assertIn("Miss check: fixed <n>, open <m>", text)
        self.assertTrue(RESULT.match("Miss check: clean") and RESULT.match("Miss check: fixed 2, open 1"))
        self.assertIsNone(RESULT.match("Miss check: ok"))

    def test_each_class_has_an_action(self):
        table = section("What a miss becomes")
        for c in CLASSES:
            self.assertRegex(table, rf"\| `{c}` \|")
        self.assertIn("`Assumed:`", table)
        self.assertIn("docs/assumptions.md", table)
        self.assertIn("docs/carousel.md", table)

    def test_the_second_miss_goes_to_the_carousel_never_a_third_fork(self):
        text = section("Second-miss rule")
        self.assertIn("survives one fix round", text)
        self.assertIn("carousel", text)
        self.assertIn("never a third fork", text)

    def test_the_log_is_twenty_runs_and_never_blocks_until_a_follow_up(self):
        text = section("The log")
        for needle in ("twenty", "never blocks", "follow-up", "kit-health/miss-check-log", "ctx_log",
                       "useful", "noise", "wrong", "MISSED"):
            self.assertIn(needle, text)

    def test_the_documented_log_slug_is_a_doc_new_sh_can_scaffold(self):
        """the doc says `new.sh TYPE=log DOMAIN=kit-health SLUG=miss-check-log`: new.sh refuses a slug outside
        [a-z0-9._-], so the documented one must pass it and land where the doc says."""
        m = re.search(r"kit-health/([\w.-]+)", section("The log"))
        self.assertIsNotNone(m)
        slug = m.group(1)
        self.assertIn(f"SLUG={slug}", section("The log"))
        with tempfile.TemporaryDirectory() as tmp:
            env = hermetic_env(tmp)
            env.update(CONTEXT_ROOT=str(Path(tmp) / "store"), TYPE="log", DOMAIN="kit-health", SLUG=slug)
            r = subprocess.run(["sh", str(BIN / "new.sh")], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = Path(tmp) / "store" / "kit-health" / f"{slug}.md"
            self.assertTrue(doc.is_file(), r.stdout)
            text = doc.read_text(encoding="utf-8")
            # #666: the scaffold names the section `ctx_log` writes and `log_section:` rotates
            self.assertIn("\nlog_section: Log\n", text.split("\n---\n", 1)[0] + "\n")
            self.assertIn("\n## Log\n", text)
            self.assertIn("`section: Log`", section("The log"))

    def test_the_four_documented_writes_name_the_log_section(self):
        """#666: ctx-store's `log` verb needs the section named and the kit's `log` type sets no default, so every
        documented `ctx_log` to the log doc names it both ways (`section: Log`; `--section Log` on the Bash route)."""
        for rel in ("docs/miss-check.md", "skills/pr-open/SKILL.md", "skills/ticket-close/SKILL.md",
                    "skills/session-handoff/SKILL.md"):
            with self.subTest(rel):
                text = (KIT / rel).read_text(encoding="utf-8")
                self.assertIn("`section: Log`", text)
                self.assertIn("`--section Log`", text)

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_the_scaffold_takes_a_ctx_log_entry_under_its_log_section(self):
        """#666: ctx-store's `log` verb needs the section named (the kit's `log` type sets no default), so a doc
        without a `## Log` heading refuses the documented write; the scaffold carries it and `--section Log` lands."""
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            store.mkdir()
            env = hermetic_env(tmp)
            env.update(CONTEXT_ROOT=str(store), KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1", CTX_ADAPTER_NO_SYNC="1")

            def adapter(*args: str) -> subprocess.CompletedProcess:
                return subprocess.run([sys.executable, str(BIN / "ctx_adapter.py"), *args], env=env,
                                      capture_output=True, text=True, timeout=120)

            r = adapter("adopt")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run(["sh", str(BIN / "new.sh")], env={**env, "TYPE": "log", "DOMAIN": "kit-health",
                               "SLUG": "miss-check-log"}, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = store / "kit-health" / "miss-check-log.md"
            r = adapter("ctx", "log", "kit-health/miss-check-log", "probe entry")
            self.assertNotEqual(r.returncode, 0, "no section named: ctx-store refuses the write")
            self.assertIn("USAGE", r.stdout + r.stderr)
            r = adapter("ctx", "log", "kit-health/miss-check-log", "probe entry", "--section", "Log")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            under = doc.read_text(encoding="utf-8").split("\n## Log\n", 1)[1]
            self.assertRegex(under.lstrip(), r"^- \d{4}-\d{2}-\d{2} — probe entry")

    def test_the_log_names_the_migration_of_a_doc_scaffolded_before_the_field(self):
        """#666: a doc scaffolded before `log_section:` existed has no `## Log` heading and "scaffold it once" means it
        is never re-scaffolded, so the doc says what the documented write exits with and the one-time migration."""
        text = section("The log")
        self.assertIn("`NO_SUCH_SECTION`", text)
        self.assertIn("`ctx_fm` sets `log_section` to `Log`", text)
        self.assertIn("`ctx_insert` puts a `## Log` line", text)

    @unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
    def test_a_doc_scaffolded_before_the_field_takes_the_documented_migration_then_the_write(self):
        """#666: the pre-field scaffold (frontmatter without `log_section`, entries at body level) refuses the
        documented write with `NO_SUCH_SECTION`; `fm log_section Log` plus `insert "## Log"` before the first entry,
        the migration the doc names, and the same write lands under the heading."""
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            store.mkdir()
            env = hermetic_env(tmp)
            env.update(CONTEXT_ROOT=str(store), KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1", CTX_ADAPTER_NO_SYNC="1")

            def adapter(*args: str) -> subprocess.CompletedProcess:
                return subprocess.run([sys.executable, str(BIN / "ctx_adapter.py"), *args], env=env,
                                      capture_output=True, text=True, timeout=120)

            r = adapter("adopt")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = store / "kit-health" / "miss-check-log.md"
            doc.parent.mkdir(parents=True)
            doc.write_text("---\ntitle: miss-check-log\ntype: log\ndomain: kit-health\ntags: []\nstatus: active\n"
                           "updated: 2026-10-30\n---\n\n# miss-check-log\n\n<!-- Append-only log. Newest first. -->\n\n"
                           "- 2026-10-30 — first entry\n", encoding="utf-8")
            r = adapter("ctx", "log", "kit-health/miss-check-log", "probe entry", "--section", "Log")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("NO_SUCH_SECTION", r.stdout + r.stderr)
            r = adapter("ctx", "fm", "kit-health/miss-check-log", "log_section", "Log")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = doc.read_text(encoding="utf-8").split("\n")
            first_entry = next(i for i, line in enumerate(lines, 1) if line.startswith("- "))
            r = adapter("ctx", "insert", "kit-health/miss-check-log", "## Log", "--line", str(first_entry - 1))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = adapter("ctx", "log", "kit-health/miss-check-log", "probe entry", "--section", "Log")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            text = doc.read_text(encoding="utf-8")
            self.assertIn("\nlog_section: Log\n", text.split("\n---\n", 1)[0] + "\n")
            under = text.split("\n## Log\n", 1)[1]
            self.assertRegex(under.lstrip(), r"^- \d{4}-\d{2}-\d{2} — probe entry\n- 2026-10-30 — first entry")


class TheWiring(unittest.TestCase):
    def skill(self, name: str) -> str:
        return (KIT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")

    def test_the_three_call_sites_fork_it_and_write_the_result_line(self):
        for name in ("pr-open", "ticket-close", "session-handoff"):
            text = self.skill(name)
            self.assertIn("docs/miss-check.md", text, name)
            self.assertIn("MISSED", text, name)
            self.assertIn("carousel", text, name)
            self.assertIn("ctx_log", text, name)
        self.assertIn("fixed <n>, open <m>", self.skill("pr-open"))
        self.assertIn("`## One honest limit`", self.skill("pr-open"))
        self.assertIn("fixed <n>, open <m>", self.skill("ticket-close"))
        self.assertIn("### Since last handoff", self.skill("session-handoff"))

    def test_the_readme_and_the_layout_index_it(self):
        self.assertIn("(docs/miss-check.md)", (KIT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("`miss-check.md`", (KIT / "docs" / "layout.md").read_text(encoding="utf-8"))

    def test_the_three_behaviour_cases_exist_and_are_listed(self):
        readme = (KIT / "evals" / "README.md").read_text(encoding="utf-8")
        for case in ("names-unverified", "clean", "second-miss-carousel"):
            d = KIT / "evals" / f"pr-open-behaviour-miss-check-{case}"
            self.assertTrue((d / "prompt.md").is_file() and (d / "graders" / "criteria.md").is_file(), case)
            self.assertIn(d.name, readme)
        total = len(list((KIT / "evals").glob("*behaviour*")))
        self.assertIn(f"({total} total,", readme)


if __name__ == "__main__":
    unittest.main()
