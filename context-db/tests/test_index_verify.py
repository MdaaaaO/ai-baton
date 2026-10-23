"""gen_index.py / verify.py / new.sh on a throw-away CONTEXT_ROOT with its own blank env store. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"

DOC = """---
title: {title}
type: {type}
domain: {domain}
tags: [alpha, "beta"]
status: {status}
updated: {updated}
---

# {title}
"""


MAKE_VARS = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES")  # an outer `make test` must not leak into a make run here


def env_for(root: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("WORKSPACE_TZ", *MAKE_VARS)}
    return {**env, "CONTEXT_ROOT": str(root), **extra}


def run(root: Path, script: str, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BIN / script), *args], env=env_for(root, **extra), cwd=BIN,
                          capture_output=True, text=True)


class ContextRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        r = run(self.root, "kb.py", "init", "--blank")
        assert r.returncode == 0, r.stderr
        (self.root / "reference").mkdir(exist_ok=True)
        (self.root / "repos").mkdir()
        self.doc("reference/conventions.md", title="Conventions", type="reference", domain="reference", status="reference")
        self.doc("repos/kit.md", title="The kit", type="repo", domain="repos", status="active")

    def tearDown(self):
        self.tmp.cleanup()

    def doc(self, rel: str, **kw: str) -> Path:
        kw.setdefault("updated", "2026-09-26")
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DOC.format(**kw), encoding="utf-8")
        return p


class IndexAndVerify(ContextRoot):
    def test_index_lists_every_doc_and_verify_passes(self):
        r = run(self.root, "gen_index.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("docs across 2 domains", r.stdout)  # the 2 docs + the blank store's env docs under reference/env/
        index = (self.root / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("reference/conventions.md", index)
        self.assertIn("repos/kit.md", index)
        self.assertIn("reference/env/slack.md", index)
        self.assertNotIn("Tags", index)  # the table carries no Tags column: tags stay in frontmatter
        self.assertNotIn("`alpha`", index)
        self.assertIn("| Doc | Title | Type | Status | Updated |", index)
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("OK — ", v.stdout)

    def test_verify_reports_schema_problems_by_file(self):
        self.doc("repos/bad.md", title="Bad", type="novel", domain="nowhere", status="maybe", updated="26.09.2026")
        (self.root / "repos" / "nofm.md").write_text("# no frontmatter\n", encoding="utf-8")
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 1)
        for needle in ("repos/bad.md: type 'novel'", "repos/bad.md: status 'maybe'", "repos/bad.md: domain 'nowhere'",
                       "repos/bad.md: updated '26.09.2026'", "repos/nofm.md: no frontmatter block"):
            self.assertIn(needle, v.stderr)

    def test_verify_accepts_a_quoted_updated_date_and_still_rejects_a_bad_one(self):
        self.doc("repos/quoted.md", title="Quoted", type="repo", domain="repos", status="active", updated='"2026-10-23"')
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.doc("repos/quoted.md", title="Quoted", type="repo", domain="repos", status="active", updated="'26.10.2026'")
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 1)
        self.assertIn("repos/quoted.md: updated ''26.10.2026'' is not YYYY-MM-DD", v.stderr)

    def test_stale_index_is_detected(self):
        run(self.root, "gen_index.py")
        self.doc("repos/new.md", title="New", type="repo", domain="repos", status="active")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 1)
        self.assertIn("INDEX.md is stale", v.stderr)

    def test_non_utf8_doc_is_named_and_the_rest_is_indexed(self):
        # one bad byte killed `make index` / `make verify` with a traceback that named no file
        (self.root / "repos" / "latin1.md").write_bytes(b"---\ntitle: caf\xe9\ntype: repo\ndomain: repos\nstatus: active\nupdated: 2026-09-26\n---\n")
        r = run(self.root, "gen_index.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("repos/latin1.md: not valid UTF-8", r.stderr)
        self.assertIn("repos/kit.md", (self.root / "INDEX.md").read_text(encoding="utf-8"))
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 1)
        self.assertNotIn("Traceback", v.stderr)
        self.assertIn("repos/latin1.md: not valid UTF-8", v.stderr)

    def test_index_diff_ignores_only_the_generated_line(self):
        # every line starting with `_` was dropped from the comparison, so a hand-edit slipped through
        run(self.root, "gen_index.py")
        p = self.root / "INDEX.md"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("generated 2026", "generated 2020"), encoding="utf-8")  # only the date moved
        self.assertEqual(run(self.root, "verify.py").returncode, 0)
        p.write_text(text + "_a stray line_\n", encoding="utf-8")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 1)
        self.assertIn("INDEX.md is stale", v.stderr)

    def test_type_index_is_not_a_doc_type(self):
        self.doc("repos/idx.md", title="Idx", type="index", domain="repos", status="active")
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py")
        self.assertIn("repos/idx.md: type 'index'", v.stderr)

    def test_env_domains_are_accepted(self):
        run(self.root, "kb.py", "config-set", "domains", '["airflow"]')
        self.doc("airflow/dag-x.md", title="DAG x", type="epic", domain="airflow", status="active")
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)

    def test_engine_dirs_are_skipped(self):
        (self.root / "sessions").mkdir()
        (self.root / "sessions" / "live.md").write_text("no frontmatter, operational\n", encoding="utf-8")
        (self.root / "reference" / "env" / "_templates").mkdir(parents=True, exist_ok=True)
        r = run(self.root, "gen_index.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("sessions/live.md", r.stderr)

    def test_archived_rows_are_folded_not_listed(self):
        # a domain's table holds only its active rows; archived rows fold into one summary
        # line (count + the `make find` command that lists them) instead of staying in the table
        self.doc("repos/old.md", title="Old thing", type="repo", domain="repos", status="archived")
        r = run(self.root, "gen_index.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("9 docs across 2 domains", r.stdout)  # the header count still counts every doc, archived too
        index = (self.root / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("repos/kit.md", index)  # active row: present
        self.assertNotIn("repos/old.md", index)  # archived row: absent from the table
        self.assertIn("_1 archived — `make -C $BATON/context-db find DOMAIN=repos STATUS=archived` lists them_", index)
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        # the folded line's command lists the archived doc and nothing else of the domain
        find = ["make", "-s", "-C", str(BIN.parent), "find", f"CONTEXT={self.root}", "DOMAIN=repos"]
        m = subprocess.run(find + ["STATUS=archived"], env=env_for(self.root), cwd=self.root,
                           capture_output=True, text=True)
        self.assertEqual(m.returncode, 0, m.stderr)
        self.assertEqual(m.stdout.split(), ["repos/old.md"])
        # without the status filter the whole domain comes back, as before
        m = subprocess.run(find, env=env_for(self.root), cwd=self.root, capture_output=True, text=True)
        self.assertEqual(m.returncode, 0, m.stderr)
        self.assertEqual(m.stdout.split(), ["repos/kit.md", "repos/old.md"])
        # a status no doc of the domain has: no line, still exit 0
        m = subprocess.run(find + ["STATUS=superseded"], env=env_for(self.root), cwd=self.root,
                           capture_output=True, text=True)
        self.assertEqual(m.returncode, 0, m.stderr)
        self.assertEqual(m.stdout.split(), [])

    def run_find(self, *args: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(["make", "-s", "-C", str(BIN.parent), "find", f"CONTEXT={self.root}", *args],
                              env=env_for(self.root), cwd=self.root, capture_output=True, text=True)

    def find(self, *args: str) -> "list[str]":
        m = self.run_find(*args)
        self.assertEqual(m.returncode, 0, m.stderr)
        return m.stdout.split()

    def test_find_reads_the_frontmatter_never_the_body(self):
        # a body line shaped like a frontmatter key (a pasted example, a quoted doc) is not the doc's own key
        p = self.doc("repos/quoting.md", title="Quoting", type="repo", domain="repos", status="active")
        p.write_text(p.read_text(encoding="utf-8") + "\ntags: [internal]\ndomain: projects\nstatus: archived\n",
                     encoding="utf-8")
        self.doc("repos/old.md", title="Old thing", type="repo", domain="repos", status="archived")
        self.assertEqual(self.find("TAG=internal"), [])
        self.assertEqual(self.find("DOMAIN=projects"), [])
        self.assertEqual(self.find("DOMAIN=repos", "STATUS=archived"), ["repos/old.md"])
        self.assertEqual(self.find("TAG=alpha", "STATUS=archived"), ["repos/old.md"])

    def test_find_matches_a_tag_as_a_whole_entry(self):
        for name, tags in (("health", "[kit-health]"), ("kit", "[kit, other]")):
            p = self.doc(f"repos/{name}.md", title=name, type="repo", domain="repos", status="active")
            p.write_text(p.read_text(encoding="utf-8").replace('[alpha, "beta"]', tags), encoding="utf-8")
        self.assertEqual(self.find("TAG=kit"), ["repos/kit.md"])
        self.assertEqual(self.find("TAG=kit-health"), ["repos/health.md"])

    def test_find_skips_what_the_index_skips(self):
        # a session file is no row of the index, so `find` does not list it either
        self.doc("sessions/lane-topic.md", title="A session", type="repo", domain="repos", status="active")
        self.assertNotIn("sessions/lane-topic.md", self.find("DOMAIN=repos"))

    def test_find_names_a_file_it_cannot_read_and_lists_the_rest(self):
        self.doc("repos/kept.md", title="Kept", type="repo", domain="repos", status="active")
        (self.root / "repos" / "gone.md").symlink_to(self.root / "nowhere.md")
        m = self.run_find("DOMAIN=repos")
        self.assertIn("repos/kept.md", m.stdout.split())
        self.assertIn("find: cannot read repos/gone.md: ", m.stderr)
        self.assertNotEqual(m.returncode, 0)

    def test_all_archived_domain_has_fold_line_and_no_table(self):
        # repos/ starts with exactly one doc (kit.md, active); flip it archived so every doc
        # in the domain is archived — the heading must still show, with no empty table markup
        self.doc("repos/kit.md", title="The kit", type="repo", domain="repos", status="archived")
        r = run(self.root, "gen_index.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        index = (self.root / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("## repos", index)
        start = index.index("## repos")
        rest = index[start:]
        nxt = rest.find("\n## ", 1)
        section = rest[:nxt] if nxt != -1 else rest
        self.assertNotIn("| Doc | Title |", section)  # no table header
        self.assertNotIn("|---|", section)  # no table rule either
        self.assertIn("_1 archived — `make -C $BATON/context-db find DOMAIN=repos STATUS=archived` lists them_", section)
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)


def active_row_bytes(index_text: str) -> int:
    """Bytes of the `status: active` and `status: closed` table rows of a generated INDEX.md (the figure verify
    budgets against INDEX_SIZE_WARN), counted straight from the file so the test does not lean on the generator's
    own helpers."""
    return sum(len(line.encode("utf-8")) + 1 for line in index_text.splitlines()
               if line.startswith("| [`") and line.split("|")[4].strip() in ("active", "closed"))


class IndexSizeBudget(ContextRoot):
    """verify.py's INDEX_SIZE_WARN budget (10KB, the same figure as SESSION_INDEX_WARN) counts the bytes of the
    `status: active` and `status: closed` rows; INDEX_TOTAL_SIZE_WARN (2x) bounds the whole file. A row's `title` is unescaped ASCII (besides `|`), so padding it by N bytes grows
    that figure by exactly N bytes — the 10240/10241-byte boundary is reachable precisely, without patching the
    constant."""

    BUDGET = 10 * 1024

    def active_bytes(self) -> int:
        return active_row_bytes((self.root / "INDEX.md").read_text(encoding="utf-8"))

    def test_no_warning_at_the_budget_and_a_warning_one_byte_over(self):
        run(self.root, "gen_index.py")
        self.doc("repos/pad.md", title="X", type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        # every other byte of the active rows is fixed by setUp's docs; growing this title by one byte
        # grows the figure by exactly one byte, so the target length is reachable by arithmetic
        length_at_budget = 1 + (self.BUDGET - self.active_bytes())
        self.assertGreater(length_at_budget, 0)

        self.doc("repos/pad.md", title="X" * length_at_budget, type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        self.assertEqual(self.active_bytes(), self.BUDGET)
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertNotIn("INDEX.md:", v.stderr)  # exactly at budget: no warning

        self.doc("repos/pad.md", title="X" * (length_at_budget + 1), type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        self.assertEqual(self.active_bytes(), self.BUDGET + 1)
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)  # non-fatal — a warning, not a FAIL
        self.assertIn(f"take {self.BUDGET + 1} bytes (> {self.BUDGET // 1024} KB", v.stderr)
        self.assertIn("archive finished docs", v.stderr)

    def test_ninety_rows_across_statuses_budget_counts_active_and_closed_and_prints_counts(self):
        # 20 active, 4 closed and 66 reference rows (plus setUp's own) over several domains, titles padded so the
        # active and closed rows alone pass the budget while the whole file is far larger
        for d in ("repos", "alpha", "beta", "gamma"):
            (self.root / d).mkdir(exist_ok=True)
        pad = "T" * 600
        for i in range(20):
            self.doc(f"repos/live{i:02d}.md", title=f"Live {i} {pad}", type="epic", domain="repos", status="active")
        for i in range(4):
            self.doc(f"repos/done{i:02d}.md", title=f"Done {i} {pad}", type="epic", domain="repos", status="closed")
        for d in ("alpha", "beta", "gamma"):
            for i in range(22):
                self.doc(f"{d}/ref{i:02d}.md", title=f"Ref {d} {i} {pad}", type="reference", domain=d, status="reference")
        run(self.root, "kb.py", "config-set", "domains", '["alpha", "beta", "gamma"]')
        # each of the three has 22 > 12 reference rows, which the default fold would collapse: raise it so all stay listed
        r = run(self.root, "gen_index.py", INDEX_REFERENCE_FOLD="100")
        self.assertEqual(r.returncode, 0, r.stderr)
        index = (self.root / "INDEX.md").read_text(encoding="utf-8")
        self.assertGreater(len(index.encode("utf-8")), 3 * self.BUDGET)  # reference rows dominate the file
        active_bytes = active_row_bytes(index)
        self.assertGreater(active_bytes, self.BUDGET)
        v = run(self.root, "verify.py", INDEX_REFERENCE_FOLD="100")
        self.assertEqual(v.returncode, 0, v.stderr)
        # setUp's repos/kit.md is the 21st active row (+ 4 closed); its env-store docs add to the reference count
        self.assertIn(f"its 25 active/closed rows take {active_bytes} bytes", v.stderr)
        self.assertIn("INDEX.md is ", v.stderr)  # the whole file (far past 20 KB) trips the total budget too
        self.assertRegex(v.stderr, r"active 21, closed 4, reference (\d+), archived 0")
        self.assertGreaterEqual(int(re.search(r"reference (\d+),", v.stderr).group(1)), 66)

    def test_reference_rows_alone_never_trip_the_active_budget(self):
        for d in ("alpha", "beta"):
            (self.root / d).mkdir(exist_ok=True)
        run(self.root, "kb.py", "config-set", "domains", '["alpha", "beta"]')
        pad = "T" * 400
        for d in ("alpha", "beta"):
            for i in range(12):  # at the fold threshold: all listed
                self.doc(f"{d}/ref{i:02d}.md", title=f"Ref {i} {pad}", type="reference", domain=d, status="reference")
        run(self.root, "gen_index.py")
        size = (self.root / "INDEX.md").stat().st_size
        self.assertTrue(self.BUDGET < size < 2 * self.BUDGET, size)  # over the active budget, under the total one
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertNotIn("INDEX.md", v.stderr)

    def test_total_budget_warns_when_the_active_rows_fit(self):
        for d in ("alpha", "beta", "gamma"):
            (self.root / d).mkdir(exist_ok=True)
        run(self.root, "kb.py", "config-set", "domains", '["alpha", "beta", "gamma"]')
        pad = "T" * 600
        for d in ("alpha", "beta", "gamma"):
            for i in range(12):  # no domain past the fold, so every row is listed
                self.doc(f"{d}/ref{i:02d}.md", title=f"Ref {i} {pad}", type="reference", domain=d, status="reference")
        run(self.root, "gen_index.py")
        index = (self.root / "INDEX.md").read_text(encoding="utf-8")
        self.assertLess(active_row_bytes(index), self.BUDGET)
        self.assertGreater(len(index.encode("utf-8")), 2 * self.BUDGET)
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)  # non-fatal
        self.assertNotIn("INDEX.md:", v.stderr)  # the active/closed budget is not what tripped
        self.assertRegex(v.stderr, rf"INDEX\.md is {len(index.encode('utf-8'))} bytes \(> 20 KB for the whole file\)")
        self.assertRegex(v.stderr, r"listed: reference \d+ rows, \d+ KB, active 1 row, \d+ KB")
        self.assertIn("lower INDEX_REFERENCE_FOLD / archive closed docs", v.stderr)
        # the remedy works: folding the reference rows brings the file back under the total budget
        run(self.root, "gen_index.py", INDEX_REFERENCE_FOLD="5")
        self.assertNotIn("INDEX.md", run(self.root, "verify.py", INDEX_REFERENCE_FOLD="5").stderr)
        # and the total budget is overridable
        run(self.root, "gen_index.py")
        self.assertNotIn("INDEX.md is", run(self.root, "verify.py", INDEX_TOTAL_SIZE_WARN="100000").stderr)

    def test_total_budget_defaults_to_twice_the_active_budget(self):
        self.doc("repos/pad.md", title="X" * 400, type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        size = (self.root / "INDEX.md").stat().st_size
        self.assertNotIn("INDEX.md is", run(self.root, "verify.py", INDEX_SIZE_WARN=str(size)).stderr)  # 2x size: fits
        self.assertIn("INDEX.md is", run(self.root, "verify.py", INDEX_SIZE_WARN=str(size // 2 - 1)).stderr)

    def test_closed_rows_count_toward_the_budget_and_are_never_folded(self):
        (self.root / "alpha").mkdir()
        run(self.root, "kb.py", "config-set", "domains", '["alpha"]')
        pad = "T" * 600
        for i in range(15):  # past the reference fold of 12: closed rows still stay listed
            self.doc(f"alpha/done{i:02d}.md", title=f"Done {i} {pad}", type="epic", domain="alpha", status="closed")
        run(self.root, "gen_index.py")
        index = (self.root / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("alpha/done14.md", index)
        self.assertNotIn("reference docs —", index.split("## alpha")[1])
        self.assertGreater(active_row_bytes(index), self.BUDGET)
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("its 16 active/closed rows take", v.stderr)
        self.assertRegex(v.stderr, r"closed 15")

    def test_warning_rounds_the_budget_like_the_doc_warnings(self):
        self.doc("repos/pad.md", title="X" * 400, type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py", INDEX_SIZE_WARN="500")
        self.assertIn("(> 1 KB;", v.stderr)  # kb() rounds up; never "> 0KB"
        self.assertNotIn("> 0", v.stderr)

    def test_malformed_override_is_named_on_stderr_and_keeps_the_default(self):
        self.doc("repos/pad.md", title="X" * 400, type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py", INDEX_SIZE_WARN="10KB")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("verify: ignoring INDEX_SIZE_WARN=10KB (not an integer)", v.stderr)
        self.assertEqual(v.stderr.count("ignoring INDEX_SIZE_WARN"), 1)
        self.assertNotIn("INDEX.md:", v.stderr)  # the 10 KB default still applies
        self.assertIn("(not positive)", run(self.root, "verify.py", ACTIVE_SIZE_WARN="0").stderr)
        quiet = run(self.root, "verify.py", INDEX_SIZE_WARN="", ACTIVE_SIZE_WARN="20480")
        self.assertNotIn("ignoring", quiet.stderr)  # unset, empty and valid values never print

    def test_budget_is_overridable_from_the_environment(self):
        self.doc("repos/pad.md", title="X" * 400, type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        self.assertNotIn("INDEX.md:", run(self.root, "verify.py").stderr)
        v = run(self.root, "verify.py", INDEX_SIZE_WARN="300")
        self.assertIn("INDEX.md:", v.stderr)


class IndexReferenceFold(ContextRoot):
    """gen_index.py folds a domain's `status: reference` rows into one line once it holds more than
    INDEX_REFERENCE_FOLD (12) of them, the way an archived row is folded."""

    def make_series(self, n: int, domain: str = "snapshots") -> None:
        (self.root / domain).mkdir(exist_ok=True)
        run(self.root, "kb.py", "config-set", "domains", f'["{domain}"]')
        for i in range(n):
            self.doc(f"{domain}/snap{i:02d}.md", title=f"Snapshot {i}", type="reference", domain=domain, status="reference")
        self.doc(f"{domain}/entry.md", title="Series entry", type="epic", domain=domain, status="active")

    def section(self, domain: str) -> str:
        index = (self.root / "INDEX.md").read_text(encoding="utf-8")
        rest = index[index.index(f"## {domain}\n"):]
        nxt = rest.find("\n## ", 1)
        return rest[:nxt] if nxt != -1 else rest

    def test_twenty_five_reference_rows_fold_into_one_line(self):
        self.make_series(25)
        r = run(self.root, "gen_index.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        section = self.section("snapshots")
        self.assertIn("_25 reference docs — `make -C $BATON/context-db find DOMAIN=snapshots STATUS=reference` lists them_", section)
        self.assertNotIn("snapshots/snap00.md", section)  # the rows themselves are gone
        self.assertIn("snapshots/entry.md", section)  # the active entry doc stays listed
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertNotIn("INDEX.md:", v.stderr)
        # still discoverable
        f = subprocess.run([sys.executable, str(BIN / "find_frontmatter.py"), str(self.root), "DOMAIN", "snapshots",
                            "STATUS", "reference"], capture_output=True, text=True, env=env_for(self.root))
        self.assertEqual(len(f.stdout.split()), 25, f.stderr)

    def test_twelve_reference_rows_stay_listed(self):
        self.make_series(12)
        run(self.root, "gen_index.py")
        section = self.section("snapshots")
        self.assertIn("snapshots/snap11.md", section)
        self.assertNotIn("reference docs —", section)

    def test_an_all_reference_domain_folds_without_an_empty_table(self):
        (self.root / "snapshots").mkdir()
        run(self.root, "kb.py", "config-set", "domains", '["snapshots"]')
        for i in range(13):
            self.doc(f"snapshots/snap{i:02d}.md", title=f"Snapshot {i}", type="reference", domain="snapshots", status="reference")
        run(self.root, "gen_index.py")
        section = self.section("snapshots")
        self.assertNotIn("| Doc |", section)
        self.assertIn("_13 reference docs — ", section)

    def test_fold_threshold_is_overridable(self):
        self.make_series(5)
        run(self.root, "gen_index.py", INDEX_REFERENCE_FOLD="3")
        self.assertIn("_5 reference docs — ", self.section("snapshots"))


class SizeBudgets(ContextRoot):
    """verify.py's per-doc budgets: ACTIVE_SIZE_WARN (30,000 B, the ctx-store SIZE_GUARD) for any type but
    reference/repo, REFERENCE_SIZE_WARN (64 KB) for `type: reference` and `type: repo`; archive/ and
    `status: archived` docs exempt; one folded line per domain, remedy named by what dominates the largest doc."""

    def big_doc(self, rel: str, *, type: str, status: str = "active", log_kb: int = 0, total_kb: int = 31,
                sections: tuple[str, ...] = ("Goal", "Notes"), domain: str = "") -> Path:
        folder = rel.split("/")[0]
        domain = domain or folder
        (self.root / folder).mkdir(exist_ok=True)
        head = f"---\ntitle: Big\ntype: {type}\ndomain: {domain}\nstatus: {status}\nupdated: 2026-09-30\n---\n\n# Big\n\n"
        body = ""
        if log_kb:
            body += "## Session log\n\n" + ("- 2026-09-01 · one log line of filler text to take some room\n" * 1000)[:log_kb * 1024] + "\n\n"
        body_so_far = len((head + body).encode())
        rest = total_kb * 1024 - body_so_far
        per = rest // len(sections)
        for i, name in enumerate(sections):
            fill = per - len(f"## {name}\n\n") - 2  # the heading, and the newline pair closing the section
            body += f"## {name}\n\n" + "x" * fill + "\n\n"
        p = self.root / rel
        p.write_text(head + body, encoding="utf-8")
        return p

    def verify(self, **env: str) -> subprocess.CompletedProcess:
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py", **env)
        self.assertEqual(v.returncode, 0, v.stderr)  # a size finding is never a FAIL
        return v

    def test_session_log_dominating_names_the_session_log_remedy(self):
        p = self.big_doc("repos/logheavy.md", type="epic", log_kb=20)
        self.assertGreater(p.stat().st_size, 30 * 1024)
        v = self.verify()
        self.assertIn("repos: 1 doc over budget, largest logheavy (31 KB)", v.stderr)
        self.assertRegex(v.stderr, r"the session log is 2[01] KB of it: move its entries to archive/logheavy-log\.md")
        self.assertNotIn("largest sections", v.stderr)

    def test_no_session_log_names_the_largest_sections(self):
        p = self.big_doc("repos/nolog.md", type="epic", sections=("Goal", "Key decisions", "Notes"))
        self.assertGreater(p.stat().st_size, 30 * 1024)
        self.assertNotIn("## Session log", p.read_text(encoding="utf-8"))
        v = self.verify()
        self.assertIn("repos: 1 doc over budget, largest nolog (31 KB)", v.stderr)
        self.assertRegex(v.stderr, r"largest sections: \w[\w ]* \(1\d KB\), \w[\w ]* \(1\d KB\)")
        self.assertNotIn("move its entries", v.stderr)

    def test_small_session_log_does_not_get_the_log_remedy(self):
        self.big_doc("repos/smalllog.md", type="epic", log_kb=2, sections=("Goal", "Notes"))
        v = self.verify()
        self.assertIn("largest sections: ", v.stderr)
        self.assertNotIn("move its entries", v.stderr)

    def test_reference_doc_is_held_to_its_own_budget(self):
        p = self.big_doc("reference/deepdive.md", type="reference", status="reference", total_kb=31)
        self.assertGreater(p.stat().st_size, 30 * 1024)
        v = self.verify()
        self.assertNotIn("over budget", v.stderr)  # 31 KB reference: no finding
        self.assertNotIn("deepdive", v.stderr)

        p = self.big_doc("reference/deepdive.md", type="reference", status="reference", total_kb=65)
        v = self.verify()
        self.assertIn("reference: 1 doc over budget, largest deepdive (65 KB)", v.stderr)
        self.assertIn("a `type: reference` doc has its own 64 KB budget", v.stderr)
        self.assertIn("64 KB for `type: reference`/`repo`)", v.stderr)

    def test_repo_deep_dive_is_held_to_the_large_budget(self):
        p = self.big_doc("repos/deepdive.md", type="repo", total_kb=31)
        self.assertGreater(p.stat().st_size, 30 * 1024)
        v = self.verify()
        self.assertNotIn("over budget", v.stderr)  # 31 KB repo doc: no finding
        self.assertNotIn("deepdive", v.stderr)

        self.big_doc("repos/deepdive.md", type="repo", total_kb=65)
        v = self.verify()
        self.assertIn("repos: 1 doc over budget, largest deepdive (65 KB)", v.stderr)
        self.assertIn("a `type: repo` doc has its own 64 KB budget", v.stderr)
        self.assertIn("64 KB for `type: reference`/`repo`)", v.stderr)

    def test_findings_fold_to_one_line_per_domain(self):
        for i in range(5):
            self.big_doc(f"reference/snap{i}.md", type="reference", status="reference", total_kb=65 + i)
        self.big_doc("repos/a.md", type="epic", total_kb=31)
        self.big_doc("repos/b.md", type="epic", total_kb=40)
        v = self.verify()
        lines = [l for l in v.stderr.splitlines() if l.startswith("  - ")]
        self.assertEqual(len(lines), 2, v.stderr)  # one per domain, not one per doc
        self.assertTrue(any(l.startswith("  - reference: 5 docs over budget, largest snap4 (69 KB)") for l in lines), lines)
        self.assertTrue(any(l.startswith("  - repos: 2 docs over budget, largest b (40 KB)") for l in lines), lines)
        self.assertIn("7 doc(s) over budget", v.stderr)

    def test_archived_docs_are_exempt_and_budgets_are_overridable(self):
        self.big_doc("repos/old.md", type="epic", status="archived", total_kb=40)
        self.assertNotIn("over budget", self.verify().stderr)
        self.big_doc("repos/live.md", type="epic", total_kb=2)
        self.assertNotIn("over budget", self.verify().stderr)
        self.assertIn("repos: 1 doc over budget, largest live", self.verify(ACTIVE_SIZE_WARN="1024").stderr)
        self.big_doc("reference/ref.md", type="reference", status="reference", total_kb=2)
        self.assertIn("reference: 1 doc over budget, largest ref", self.verify(REFERENCE_SIZE_WARN="1024").stderr)

    def test_archive_folder_docs_are_exempt_whatever_their_frontmatter_says(self):
        # `ctx maintain` gives a new archive/<slug>-log.md its source doc's domain and the log type's first status
        p = self.big_doc("archive/old-log.md", type="log", domain="repos", total_kb=40)
        self.assertIn("domain: repos\nstatus: active\n", p.read_text(encoding="utf-8"))
        self.assertNotIn("over budget", self.verify().stderr)

    def test_active_budget_is_the_ctx_store_size_guard(self):
        # ctx validate warns, and ctx maintain rotates, past 30,000 B (SIZE_GUARD): verify warns past the same size
        p = self.big_doc("repos/edge.md", type="epic", total_kb=30)
        self.assertGreater(p.stat().st_size, 30000)
        self.assertLessEqual(p.stat().st_size, 30 * 1024)
        self.assertIn("repos: 1 doc over budget, largest edge (30 KB)", self.verify().stderr)


class LedgerWarnings(ContextRoot):
    """verify.py's decision-ledger check (docs/carousel.md § After the answer): a line under Key
    decisions & gotchas that already looks ledger-shaped (a date, then `→`) but is missing its
    `; not <rejected>` clause warns; a free-form gotcha line in the same section never does."""

    def ledger_doc(self, rel: str, good: str, no_reject: str, free_form: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntitle: Ledger\ntype: repo\ndomain: repos\nstatus: active\nupdated: 2026-09-30\n---\n\n"
            "# Ledger\n\n## Key decisions & gotchas\n\n"
            f"- {good}\n- {no_reject}\n- {free_form}\n\n## Remaining work\n",
            encoding="utf-8",
        )
        return p

    def test_ledger_shaped_line_without_rejected_option_warns_once(self):
        good = "2026-09-29 · pick a queue → SQS; not Kafka — ops already run SQS elsewhere"
        no_reject = "2026-09-28 · pick a cache → Redis — it was already in the stack"
        arrows_in_prose = "(2026-09-27) the cliff is invalidation (tools → system → messages); batch always-on edits"
        free_form = "watch out, the fixture loader is order-dependent"
        self.ledger_doc("repos/ledger.md", good, no_reject, free_form + "\n- " + arrows_in_prose)
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)  # non-fatal — a warning, not a FAIL
        self.assertEqual(v.stderr.count("repos/ledger.md:"), 1, v.stderr)  # exactly one warning
        self.assertIn(f"repos/ledger.md: - {no_reject}", v.stderr)
        self.assertNotIn(good, v.stderr)
        self.assertNotIn(free_form, v.stderr)


class OwnerClaimWarnings(ContextRoot):
    """verify.py warns when an environment.md states who owns code: that fact lives in the repo's CODEOWNERS file."""

    def env_doc(self, body: str) -> None:
        p = self.root / "reference" / "environment.md"
        p.write_text("---\ntitle: Environment\ntype: reference\ndomain: reference\nstatus: reference\n"
                     f"updated: 2026-10-15\n---\n\n# Environment\n\n{body}\n", encoding="utf-8")
        run(self.root, "gen_index.py")

    def test_claims_warn_once_each_and_stay_non_fatal(self):
        claim = "Review routing: `acme/widgets` CODEOWNERS = @example/team-a and @example/team-b"
        phrase = "The Code Owner for the pipeline is @octocat."
        self.env_doc(f"- {claim}\n- {phrase}\n- Chat channel: the team reads #widgets.")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("2 environment.md line(s) state who owns code", v.stderr)
        self.assertIn(f"reference/environment.md: - {claim}", v.stderr)
        self.assertIn(phrase, v.stderr)
        self.assertNotIn("#widgets", v.stderr)

    def test_a_plain_mention_of_code_owners_is_not_a_claim(self):
        self.env_doc("- Reviewers: pr-open requests the code owners GitHub resolves from the repo's CODEOWNERS file.\n"
                     "- The CODEOWNERS file decides review routing; never restate it here.")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertNotIn("state who owns code", v.stderr)

    def test_a_clean_environment_doc_and_other_docs_do_not_warn(self):
        self.env_doc("- Tracker: GitHub issues.")
        (self.root / "repos" / "owners.md").write_text(
            "---\ntitle: Owners\ntype: repo\ndomain: repos\nstatus: active\nupdated: 2026-10-15\n---\n\n"
            "CODEOWNERS = a repo doc may say this\n", encoding="utf-8")
        run(self.root, "gen_index.py")
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertNotIn("state who owns code", v.stderr)


class NewSh(ContextRoot):
    def new(self, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(BIN / "new.sh")], env=env_for(self.root, **env), capture_output=True, text=True)

    def test_scaffolds_every_type_from_the_engine_templates(self):
        run(self.root, "kb.py", "config-set", "domains", '["airflow"]')
        for t, dom in (("epic", "airflow"), ("reference", ""), ("repo", ""), ("meeting", ""), ("1on1", ""),
                       ("oncall", ""), ("self-assessment", ""), ("pr-review", ""), ("log", "airflow")):
            r = self.new(TYPE=t, DOMAIN=dom, SLUG=f"smoke-{t}", TITLE=f"Smoke {t}")
            self.assertEqual(r.returncode, 0, f"{t}: {r.stderr}")
            dest = Path(r.stdout.strip())
            self.assertTrue(dest.is_file(), dest)
            text = dest.read_text(encoding="utf-8")
            self.assertIn(f"title: Smoke {t}", text)
            self.assertNotIn("{{", text)  # every placeholder substituted
        v = run(self.root, "gen_index.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertEqual(run(self.root, "verify.py").returncode, 0)

    def test_epic_scaffold_carries_the_optional_architecture_section(self):
        # the section is in the template but not in the epic type's required list (#389): a fresh scaffold
        # carries it, and the doc still validates with the kit's own light checker.
        run(self.root, "kb.py", "config-set", "domains", '["airflow"]')
        r = self.new(TYPE="epic", DOMAIN="airflow", SLUG="arch-smoke", TITLE="Arch smoke")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(r.stdout.strip()).read_text(encoding="utf-8")
        self.assertIn("## Architecture", text)
        self.assertLess(text.index("## Key decisions & gotchas"), text.index("## Architecture"))
        self.assertLess(text.index("## Architecture"), text.index("## Infra / secrets locations"))
        self.assertEqual(run(self.root, "gen_index.py").returncode, 0)
        self.assertEqual(run(self.root, "verify.py").returncode, 0)

    def test_epic_and_repo_scaffolds_carry_the_optional_plain_words_section(self):
        # the section is in both templates but in neither type's required list: a fresh scaffold carries it
        # (after the section it follows), and the doc still validates with the kit's own light checker.
        run(self.root, "kb.py", "config-set", "domains", '["airflow"]')
        r = self.new(TYPE="epic", DOMAIN="airflow", SLUG="words-smoke", TITLE="Words smoke")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(r.stdout.strip()).read_text(encoding="utf-8")
        self.assertIn("## In plain words", text)
        self.assertLess(text.index("## Key decisions & gotchas"), text.index("## In plain words"))
        self.assertLess(text.index("## In plain words"), text.index("## Architecture"))
        self.assertIn("operator", text.split("## In plain words", 1)[1].split("##", 1)[0])  # the comment says who confirms
        r = self.new(TYPE="repo", DOMAIN="airflow", SLUG="words-card", TITLE="Words card")
        self.assertEqual(r.returncode, 0, r.stderr)
        card = Path(r.stdout.strip()).read_text(encoding="utf-8")
        self.assertLess(card.index("## What it is"), card.index("## In plain words"))
        self.assertLess(card.index("## In plain words"), card.index("## Where things are"))
        self.assertEqual(run(self.root, "gen_index.py").returncode, 0)
        self.assertEqual(run(self.root, "verify.py").returncode, 0)

    def test_special_characters_in_values_survive(self):
        # the sed substitution was unescaped — `/`, `&`, `\\` and the `|` delimiter corrupted the scaffold
        title = "A/B & C\\D | E $x"
        r = self.new(TYPE="repo", SLUG="weird", TITLE=title)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(r.stdout.strip()).read_text(encoding="utf-8")
        self.assertIn(f"title: {title}\n", text)
        self.assertIn(f"# {title}", text)
        r = self.new(TYPE="repo", SLUG="nl", TITLE="two\nlines")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("title: two lines\n", Path(r.stdout.strip()).read_text(encoding="utf-8"))

    def test_refuses_to_clobber_and_unknown_type(self):
        r = self.new(TYPE="repo", SLUG="kit", TITLE="dup")
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing to clobber", r.stderr)
        r = self.new(TYPE="novel", SLUG="x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown TYPE", r.stderr)
        r = self.new(TYPE="epic", SLUG="x")  # epic needs a domain
        self.assertNotEqual(r.returncode, 0)

    def test_slug_and_domain_cannot_leave_the_content_root(self):
        # #139: SLUG/DOMAIN are path components — `make new` must never write outside .context/
        for env in ({"TYPE": "repo", "SLUG": "../../escape"}, {"TYPE": "repo", "SLUG": ".hidden"},
                    {"TYPE": "log", "DOMAIN": "../..", "SLUG": "x"}, {"TYPE": "epic", "DOMAIN": "a/b", "SLUG": "x"},
                    {"TYPE": "repo", "SLUG": "Upper"}, {"TYPE": "repo", "SLUG": "a b"}):
            r = self.new(**env)
            self.assertEqual(r.returncode, 2, f"{env}: {r.stderr}")
            self.assertIn("must match", r.stderr)
        for escaped in (self.root.parent / "escape.md", self.root.parent.parent / "escape.md", self.root.parent / "x.md"):
            self.assertFalse(escaped.exists(), escaped)

    def test_name_check_forces_a_c_locale(self):
        # `[a-z]` / `[!a-z0-9._-]` in a shell `case` pattern are collation ranges on some libcs — under a
        # locale whose default collation treats letters case-insensitively, they can silently accept an
        # upper-case SLUG the check means to reject (#139). new.sh must pin LC_ALL=C before name_ok runs,
        # so the check means the same thing regardless of the caller's (or the OS's) default locale.
        text = (BIN / "new.sh").read_text(encoding="utf-8")
        before_check = text.split("name_ok()", 1)[0]
        self.assertIn("LC_ALL=C", before_check, "new.sh must force a C locale before the SLUG/DOMAIN check runs")

    def test_a_failed_template_lookup_is_an_error(self):
        # was `2>/dev/null || true`: a broken lookup silently fell back to the built-in template
        fake = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, fake)
        (fake / "python3").write_text('#!/bin/sh\n[ "$2" = template ] && { echo boom >&2; exit 3; }\nexec ' + sys.executable + ' "$@"\n')
        (fake / "python3").chmod(0o755)
        r = self.new(TYPE="repo", SLUG="tmpl-fail", PATH=f"{fake}:{os.environ['PATH']}")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("boom", r.stderr)
        self.assertIn("template repo failed", r.stderr)
        self.assertFalse((self.root / "repos" / "tmpl-fail.md").exists())

    def test_make_new_passes_the_title_verbatim(self):
        title = """it's "q" $(touch PWNED-a) `touch PWNED-b` $(shell touch PWNED-c) $$x"""
        r = subprocess.run(["make", "-s", "-C", str(BIN.parent), "new", f"CONTEXT={self.root}", "TYPE=repo", "SLUG=mk-title",
                            f"TITLE={title}"], env=env_for(self.root), cwd=self.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"title: {title}\n", (self.root / "repos" / "mk-title.md").read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in self.root.parent.rglob("PWNED*")] + [p.name for p in BIN.parent.rglob("PWNED*")], [])

    def test_env_store_template_override_wins(self):
        tdir = self.root / "reference" / "env" / "_templates"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "repo.md").write_text("---\ntitle: {{TITLE}}\ntype: repo\ndomain: {{DOMAIN}}\nstatus: active\nupdated: {{DATE}}\n---\n\nOVERRIDE {{SLUG}}\n", encoding="utf-8")
        r = self.new(TYPE="repo", SLUG="over", TITLE="Over")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OVERRIDE over", Path(r.stdout.strip()).read_text(encoding="utf-8"))


class ReadmeTemplateBudgets(unittest.TestCase):
    """The store README template states the real fold and index budgets, so the prose cannot drift from the code."""

    def test_template_names_the_default_fold_and_budgets(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("INDEX_")}
        code = "import gen_index, verify; print(gen_index.INDEX_REFERENCE_FOLD, verify.INDEX_SIZE_WARN // 1024)"
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=BIN, capture_output=True, text=True, check=True)
        fold, size_kb = out.stdout.split()
        text = " ".join((BIN.parent / "context-README.template.md").read_text(encoding="utf-8").split())
        self.assertIn(f"more than {fold} (`INDEX_REFERENCE_FOLD`)", text)
        self.assertIn(f"`active` and `closed` rows pass {size_kb} KB (`INDEX_SIZE_WARN`;", text)
        self.assertIn(f"whole file passes {2 * int(size_kb)} KB (`INDEX_TOTAL_SIZE_WARN`", text)

    def test_template_names_the_default_active_budget(self):
        env = {k: v for k, v in os.environ.items() if k != "ACTIVE_SIZE_WARN"}
        code = "import verify; print(verify.ACTIVE_SIZE_WARN)"
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=BIN, capture_output=True, text=True, check=True)
        text = " ".join((BIN.parent / "context-README.template.md").read_text(encoding="utf-8").split())
        self.assertIn(f"doc exceeds **{int(out.stdout):,} B**", text)


if __name__ == "__main__":
    unittest.main()
