"""alerts-sweep regressions: an unpaginated Slack read silently dropped the rest of a busy channel, and the
fork wrote `STATE`/`KB` straight through Bash (`sed -i`, an append, `make … index`) while its own docs
(agents/triage.md, skills/alerts-sweep/README.md) called it read-only — the tool restriction on Edit/Write
never stopped a Bash command doing the same thing by another door. The fix moves every write into
`skills/alerts-sweep/scripts/advance-state.py`, a script only the MAIN session runs, and teaches
kit_verify.py to flag a skill forked to a read-only agent whose own body still carries one of those forms.
Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))

import frontmatter as fmt  # noqa: E402
import kit_verify  # noqa: E402

SKILL_MD = KIT / "skills" / "alerts-sweep" / "SKILL.md"
ADVANCE_STATE = KIT / "skills" / "alerts-sweep" / "scripts" / "advance-state.py"
KB_PY = KIT / "context-db" / "bin" / "kb.py"


def skill_body() -> str:
    parts = fmt.split(SKILL_MD.read_text(encoding="utf-8"))
    return parts[1] if parts else ""


class ForkWriteLeak(unittest.TestCase):
    """kit_verify.check_forked_write_leak / read_only_fork_agents: a skill whose `agent:` denies both Edit
    and Write must not carry `sed -i`, a shell append or `make … index` in its own body."""

    def check(self, agent: str, body: str) -> list[str]:
        errors: list[str] = []
        fm = {"agent": agent} if agent else {}
        kit_verify.check_forked_write_leak("skills/x/SKILL.md", fm, body, errors)
        return errors

    def test_read_only_fork_agents_names_the_edit_and_write_denying_agent(self):
        agents = kit_verify.read_only_fork_agents()
        self.assertIn("triage", agents)  # disallowedTools: Edit, Write, ...
        self.assertNotIn("review-runner", agents)  # keeps Write for its own triage sheet — not read-only

    def test_sed_dash_i_is_flagged(self):
        errors = self.check("triage", "1. run `sed -i -E 's/a/b/' \"$STATE\"`\n")
        self.assertTrue(any("sed -i" in e and "denies Edit/Write" in e for e in errors), errors)

    def test_shell_append_is_flagged(self):
        errors = self.check("triage", 'echo "$LINE" >> "$KB"\n')
        self.assertTrue(any(">>" in e for e in errors), errors)

    def test_make_index_is_flagged(self):
        errors = self.check("triage", "Then `make -C $BATON/context-db index`.\n")
        self.assertTrue(any("make … index" in e for e in errors), errors)

    def test_nested_placeholder_angle_brackets_are_not_a_false_positive(self):
        # the kit's own nested-placeholder shape (`<outer <inner>>`) ends in two literal '>' with no
        # whitespace before them — it must never read as a shell append redirect
        errors = self.check("triage", "thread: <no replies | N replies, last by <name>>\n")
        self.assertEqual(errors, [])

    def test_a_skill_with_no_agent_field_is_never_checked(self):
        errors = self.check("", "run `sed -i -E 's/a/b/' \"$STATE\"`\n")
        self.assertEqual(errors, [])

    def test_a_fork_that_may_write_is_not_flagged(self):
        errors = self.check("review-runner", "run `sed -i -E 's/a/b/' \"$STATE\"`\n")
        self.assertEqual(errors, [])

    def test_a_clean_body_is_silent(self):
        errors = self.check("triage", "1. Read the channel.\n2. Classify each message.\n3. Return the brief.\n")
        self.assertEqual(errors, [])

    def test_the_real_alerts_sweep_skill_is_clean(self):
        # the regression itself: on origin/main this body still runs `sed -i` and `make … index` directly —
        # restoring that file here (git show origin/main:skills/alerts-sweep/SKILL.md) reproduces the failure
        parts = fmt.split(SKILL_MD.read_text(encoding="utf-8"))
        fm = fmt.parse_lines(parts[0])
        errors: list[str] = []
        kit_verify.check_forked_write_leak(SKILL_MD.relative_to(KIT), fm, parts[1], errors)
        self.assertEqual(errors, [], errors)

    def test_kit_verify_no_env_passes_on_the_whole_kit(self):
        env = dict(os.environ, CONTEXT_ROOT="/nonexistent/.context")
        p = subprocess.run([sys.executable, str(BIN / "kit_verify.py"), "--no-env"], capture_output=True, text=True, env=env, cwd=KIT)
        self.assertEqual(p.returncode, 0, p.stderr)


class Pagination(unittest.TestCase):
    """Step 2 must loop on the connector's own cursor rather than trust one page — a busy channel, or a
    weekend gap, returns more than one page and the rest must never be silently dropped."""

    def test_step_2_handles_a_cursor(self):
        self.assertTrue(re.search(r"next_cursor|has_more", skill_body()), "no cursor/has_more handling in step 2")

    def test_a_failed_read_returns_a_named_failure_not_no_op(self):
        self.assertIn("alerts-sweep: read failed — state not advanced", skill_body())


def load_advance_state():
    import importlib.util
    spec = importlib.util.spec_from_file_location("advance_state_under_test", ADVANCE_STATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class AdvanceStatePureFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_advance_state()

    def test_safe_rel_accepts_a_dotcontext_relative_path(self):
        self.assertEqual(self.mod.safe_rel(".context/a/b.md"), Path(".context/a/b.md"))

    def test_safe_rel_rejects_absolute_dotdot_and_outside_dotcontext(self):
        self.assertIsNone(self.mod.safe_rel("/etc/passwd"))
        self.assertIsNone(self.mod.safe_rel(".context/../etc/passwd"))
        self.assertIsNone(self.mod.safe_rel("elsewhere/state.md"))

    def test_advance_state_text_updates_ts_and_date(self):
        text = '---\nupdated: "2020-01-01"\n---\n\n**Last swept through:** ts `100.100`\n'
        out = self.mod.advance_state_text(text, "200.200", "2026-09-28")
        self.assertIn("ts `200.200`", out)
        self.assertIn('updated: "2026-09-28"', out)

    def test_advance_state_text_requires_both_markers(self):
        with self.assertRaises(SystemExit):
            self.mod.advance_state_text("no markers here\n", "1", "2026-09-28")
        with self.assertRaises(SystemExit):
            self.mod.advance_state_text("**Last swept through:** ts `1`\n", "2", "2026-09-28")  # no 'updated:'

    def test_append_recurrences_creates_the_heading_once(self):
        out = self.mod.append_recurrences_text("# KB\n\nintro\n", ["- line one"])
        self.assertEqual(out.count("## Recurrence log"), 1)
        self.assertIn("- line one", out)

    def test_append_recurrences_reuses_an_existing_heading(self):
        base = "# KB\n\n## Recurrence log\n\n- line one\n"
        out = self.mod.append_recurrences_text(base, ["- line two"])
        self.assertEqual(out.count("## Recurrence log"), 1)
        self.assertIn("- line one\n- line two\n", out)

    def test_append_recurrences_with_no_lines_is_a_no_op(self):
        self.assertEqual(self.mod.append_recurrences_text("# KB\n", []), "# KB\n")


class AdvanceStateCLI(unittest.TestCase):
    """The script end to end: two atomic file writes, path safety, and (opt-in) the index rebuild —
    always against a temp store, never the live one (CONTEXT_ROOT is always overridden below)."""

    def seed(self, tmp: Path) -> None:
        d = tmp / ".context" / "oncall"
        d.mkdir(parents=True)
        (d / "state.md").write_text('---\nupdated: "2020-01-01"\n---\n\n**Last swept through:** ts `100.100`\n', encoding="utf-8")
        (d / "kb.md").write_text("# Pattern KB\n", encoding="utf-8")

    def run_script(self, tmp: Path, *args: str):
        return subprocess.run([sys.executable, str(ADVANCE_STATE), "--root", str(tmp), *args], capture_output=True, text=True)

    def test_advances_state_and_appends_kb_lines(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self.seed(tmp)
            line = "- 2026-09-28 100.200 some_dag/some_task — an entry"
            p = self.run_script(tmp, "--state", ".context/oncall/state.md", "--kb", ".context/oncall/kb.md",
                                 "--ts", "999.500", "--recurrence", line)
            self.assertEqual(p.returncode, 0, p.stderr)
            state_text = (tmp / ".context/oncall/state.md").read_text()
            self.assertIn("ts `999.500`", state_text)
            self.assertIn(date.today().isoformat(), state_text)
            kb_text = (tmp / ".context/oncall/kb.md").read_text()
            self.assertIn("## Recurrence log", kb_text)
            self.assertIn(line, kb_text)

    def test_no_recurrence_lines_leaves_kb_untouched(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self.seed(tmp)
            before = (tmp / ".context/oncall/kb.md").read_text()
            p = self.run_script(tmp, "--state", ".context/oncall/state.md", "--kb", ".context/oncall/kb.md", "--ts", "1")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual((tmp / ".context/oncall/kb.md").read_text(), before)

    def test_rejects_a_dotdot_path(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self.seed(tmp)
            p = self.run_script(tmp, "--state", ".context/../outside.md", "--kb", ".context/oncall/kb.md", "--ts", "1")
            self.assertEqual(p.returncode, 2)
            self.assertIn(".context/", p.stderr)

    def test_rejects_an_absolute_path(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self.seed(tmp)
            p = self.run_script(tmp, "--state", "/etc/passwd", "--kb", ".context/oncall/kb.md", "--ts", "1")
            self.assertEqual(p.returncode, 2)

    def test_no_stray_tmp_files_after_a_write(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self.seed(tmp)
            self.run_script(tmp, "--state", ".context/oncall/state.md", "--kb", ".context/oncall/kb.md", "--ts", "1")
            leftovers = [p.name for p in (tmp / ".context/oncall").iterdir() if p.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_reindex_rebuilds_the_root_given_store_only(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            env = dict(os.environ, CONTEXT_ROOT=str(tmp / ".context"))
            subprocess.run([sys.executable, str(KB_PY), "init", "--blank"], env=env, check=True, capture_output=True)
            self.seed(tmp)
            p = self.run_script(tmp, "--state", ".context/oncall/state.md", "--kb", ".context/oncall/kb.md",
                                 "--ts", "2", "--reindex")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue((tmp / ".context" / "INDEX.md").is_file())


if __name__ == "__main__":
    unittest.main()
