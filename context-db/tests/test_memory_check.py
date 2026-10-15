"""skills/memory-prune/memory_check.py — the read-only auto-memory audit: every finding class (INDEX_BUDGET, LONG_LINE,
NO_FILE, NO_INDEX, DANGLING, FRONTMATTER, RULES_QUOTE, SECRET, LARGE), the histogram, the memory-dir resolution order
and the exit codes (0 within budget, 1 past it, 2 on a missing dir). Every fixture is a temp dir; a secret-shaped
string is assembled at run time. Stdlib unittest. Run: make -C .claude/context-db test T=test_memory_check."""
from __future__ import annotations
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[2]
SCRIPT = KIT / "skills" / "memory-prune" / "memory_check.py"


def load():
    spec = importlib.util.spec_from_file_location("memory_check_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


mc = load()

RULE = ("Never print secret values (parameter-store, tokens, `settings.local.json`) into the transcript — "
        "compare in-shell, print only match/no-match.")
CUT = RULE.index(" into")
WORKSPACE = f"# W\n\n## Rules (always on)\n\n**Communication**\n- {RULE[:CUT]}\n  {RULE[CUT + 1:]}\n- Short rule.\n\n## Cost\n\n- not a rule\n"


def note(name: str, body: str = "Residue only.", kind: str = "feedback") -> str:
    return f'---\nname: {name}\ndescription: "what {name} holds"\nmetadata:\n  type: {kind}\n---\n\n{body}\n'


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mem = self.root / "memory"
        self.mem.mkdir()
        self.ws = self.root / "WORKSPACE.md"
        self.ws.write_text(WORKSPACE, encoding="utf-8")
        self.write("a.md", note("a"))
        self.write("b.md", note("b", "See [[a]] for the rest."))
        self.index("- [A](a.md) — first note", "- [B](b.md) — second note")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, text: str) -> None:
        (self.mem / name).write_text(text, encoding="utf-8")

    def index(self, *lines: str) -> None:
        self.write("MEMORY.md", "# Memory index\n\n" + "\n".join(lines) + "\n")

    def run_check(self, **env: str) -> tuple[int, str]:
        e = dict(os.environ, MEMORY_DIR=str(self.mem))
        for k in ("MEMORY_INDEX_WARN", "MEMORY_LINE_WARN"):
            e.pop(k, None)
        e.update(env)
        p = subprocess.run([sys.executable, str(SCRIPT), "--workspace", str(self.ws)], env=e, capture_output=True,
                           text=True, timeout=60)
        return p.returncode, p.stdout

    def kinds(self, out: str) -> list[str]:
        return [line.split(" ", 1)[0] for line in out.splitlines()[1:]]


class ExitCodes(Fixture):
    def test_clean_dir_exits_0_with_one_summary_line(self):
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertTrue(out.startswith("memory-check: index "), out)
        self.assertIn("within budget", out)
        self.assertIn("2 notes", out)

    def test_missing_dir_exits_2(self):
        rc, out = self.run_check(MEMORY_DIR=str(self.root / "nope"))
        self.assertEqual(rc, 2, out)
        self.assertIn("no memory dir", out)

    def test_index_over_budget_exits_1(self):
        rc, out = self.run_check(MEMORY_INDEX_WARN="40")
        self.assertEqual(rc, 1, out)
        self.assertIn("INDEX_BUDGET", self.kinds(out))
        self.assertIn("OVER BUDGET — /memory-prune", out.splitlines()[0])

    def test_long_index_line_exits_1_and_the_limit_is_an_env_knob(self):
        self.index("- [A](a.md) — " + "x" * 160, "- [B](b.md) — short")
        rc, out = self.run_check()
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.kinds(out), ["LONG_LINE"])
        self.assertIn("MEMORY.md:3 ", out)
        rc, out = self.run_check(MEMORY_LINE_WARN="400")
        self.assertEqual(rc, 0, out)

    def test_a_bad_knob_value_falls_back_to_the_default(self):
        rc, out = self.run_check(MEMORY_INDEX_WARN="lots")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"/ {mc.INDEX_WARN} B", out)

    def test_findings_inside_the_budget_do_not_change_the_exit(self):
        self.write("c.md", note("c"))  # unindexed
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.kinds(out), ["NO_INDEX"])


class Findings(Fixture):
    def test_index_line_without_a_file(self):
        self.index("- [A](a.md) — a", "- [B](b.md) — b", "- [Gone](gone.md) — removed")
        _, out = self.run_check()
        self.assertEqual(self.kinds(out), ["NO_FILE"])
        self.assertIn("MEMORY.md:5 links `gone.md`", out)

    def test_file_without_an_index_line(self):
        self.write("orphan.md", note("orphan"))
        _, out = self.run_check()
        self.assertEqual(self.kinds(out), ["NO_INDEX"])
        self.assertIn("NO_INDEX orphan.md", out)

    def test_missing_index_file(self):
        (self.mem / "MEMORY.md").unlink()
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)
        self.assertIn("NO_FILE MEMORY.md — the index itself is missing", out)
        self.assertEqual(self.kinds(out).count("NO_INDEX"), 2)

    def test_dangling_wiki_link(self):
        self.write("b.md", note("b", "See [[a]], [[a|alias]] and [[nowhere]]."))
        _, out = self.run_check()
        self.assertEqual(self.kinds(out), ["DANGLING"])
        self.assertIn("DANGLING b.md:8 [[nowhere]]", out)

    def test_frontmatter_problems(self):
        self.write("a.md", "no block at all\n")
        self.write("b.md", '---\nname: b\ndescription: "d"\n---\nbody\n')
        self.write("c.md", note("c", kind="diary"))
        self.write("d.md", '---\nname: d\ndescription: "d"\ntype: user\n---\nbody\n')  # older top-level form: valid
        self.index(*(f"- [{n}]({n}.md) — x" for n in "abcd"))
        _, out = self.run_check()
        lines = [x for x in out.splitlines() if x.startswith("FRONTMATTER")]
        self.assertEqual(len(lines), 3, out)
        self.assertIn("FRONTMATTER a.md no `---` frontmatter block", out)
        self.assertIn("FRONTMATTER b.md missing metadata.type", out)
        self.assertIn("FRONTMATTER c.md metadata.type `diary`", out)

    def test_rules_quote_needs_twelve_consecutive_words(self):
        words = mc.words(RULE)
        self.write("a.md", note("a", "Rule, restated: " + " ".join(words[2:14]) + "."))
        self.write("b.md", note("b", "Only eleven: " + " ".join(words[2:13]) + "."))
        _, out = self.run_check()
        self.assertEqual(self.kinds(out), ["RULES_QUOTE"], out)
        self.assertIn("RULES_QUOTE a.md repeats WORKSPACE.md § Rules", out)

    def test_rules_quote_spans_a_wrapped_rule_and_ignores_markdown(self):
        # the fixture rule wraps before `into`; the quote crosses the wrap and drops the backticks
        self.write("a.md", note("a", "**Never print secret values** (parameter-store, tokens, settings.local.json) into the transcript."))
        _, out = self.run_check()
        self.assertEqual(self.kinds(out), ["RULES_QUOTE"], out)

    def test_rules_outside_the_rules_section_are_not_matched(self):
        self.assertEqual(mc.rules_lines(self.ws)[1], "Short rule.")
        self.assertEqual(len(mc.rules_lines(self.ws)), 2)
        self.assertEqual(mc.rules_lines(self.root / "missing.md"), [])

    def test_secret_shape_is_named_never_printed(self):
        token = "gh" + "p_" + "A1b2C3d4" * 4
        self.write("a.md", note("a", f"fixture: {token}"))
        _, out = self.run_check()
        self.assertEqual(self.kinds(out), ["SECRET"])
        self.assertIn("SECRET a.md:8 GitHub token", out)
        self.assertNotIn(token, out)

    def test_large_note(self):
        self.write("a.md", note("a", "word " * 2200))
        _, out = self.run_check()
        self.assertEqual(self.kinds(out), ["LARGE"])
        self.assertIn("LARGE a.md 10 KB", out)


class Dump(Fixture):
    def test_dump_is_every_file_verbatim_fenced_and_redacted(self):
        token = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
        self.write("b.md", note("b", f"## heading\n\n```sh\nkey {token}\n```"))
        before = {p.name: p.read_bytes() for p in self.mem.iterdir()}
        p = subprocess.run([sys.executable, str(SCRIPT), "--dir", str(self.mem), "--dump"], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout
        self.assertTrue(out.startswith("## Run "), out)
        self.assertEqual([x for x in out.splitlines() if x.startswith("### ")], ["### a.md", "### b.md", "### MEMORY.md"])
        self.assertNotIn(token, out)
        self.assertIn("key <redacted: AWS access key id>", out)
        self.assertIn("````markdown\n" + note("a").rstrip("\n") + "\n````", out)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.mem.iterdir()}, "--dump must not write")

    def test_fence_outgrows_the_longest_backtick_run(self):
        self.write("a.md", note("a", "`````five`````"))
        self.assertIn("``````markdown", mc.dump(self.mem, "2026-01-01 00:00"))

    def test_dump_on_a_missing_dir_exits_2(self):
        p = subprocess.run([sys.executable, str(SCRIPT), "--dir", str(self.root / "nope"), "--dump"], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(p.returncode, 2)


class Helpers(unittest.TestCase):
    def test_histogram_buckets(self):
        self.assertEqual(mc.histogram([10, 80, 81, 150, 151, 200, 201]), "<=80: 2 · 81-150: 2 · 151-200: 2 · >200: 1")

    def test_memory_dir_order(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            store = t / "ws" / ".context"
            (store / "memory").mkdir(parents=True)
            self.assertEqual(mc.memory_dir(str(t / "x")), t / "x")
            with mock.patch.dict(os.environ, {"MEMORY_DIR": str(t / "y"), "CONTEXT_ROOT": str(store)}):
                self.assertEqual(mc.memory_dir(), t / "y")
            with mock.patch.dict(os.environ, {"MEMORY_DIR": "", "CONTEXT_ROOT": str(store)}):
                self.assertEqual(mc.memory_dir(), store / "memory")
            # no durable copy: the harness dir setup.sh links from, ~/.claude/projects/<slug>/memory
            (store / "memory").rmdir()
            harness = t / "home" / ".claude" / "projects" / str(store.parent).replace("/", "-") / "memory"
            harness.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"MEMORY_DIR": "", "CONTEXT_ROOT": str(store), "HOME": str(t / "home")}):
                self.assertEqual(mc.memory_dir(), harness)


if __name__ == "__main__":
    unittest.main()
