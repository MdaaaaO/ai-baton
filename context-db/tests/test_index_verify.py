"""gen_index.py / verify.py / new.sh on a throw-away CONTEXT_ROOT with its own blank env store. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
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
        self.assertIn("`alpha` `beta`", index)  # tags normalised (quotes stripped)
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


class IndexSizeBudget(ContextRoot):
    """verify.py's INDEX_SIZE_WARN budget (10KB, the same figure as SESSION_INDEX_WARN). A row's
    `title` is unescaped ASCII (besides `|`), so padding it by N bytes grows INDEX.md by exactly
    N bytes — the 10240/10241-byte boundary is reachable precisely, without patching the constant."""

    BUDGET = 10 * 1024

    def test_no_warning_at_the_budget_and_a_warning_one_byte_over(self):
        index_path = self.root / "INDEX.md"
        run(self.root, "gen_index.py")
        self.doc("repos/pad.md", title="X", type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        with_one_char_title = index_path.stat().st_size
        # every other byte in INDEX.md is fixed by setUp's docs; growing this title by one byte
        # grows the file by exactly one byte, so the target length is reachable by arithmetic
        length_at_budget = 1 + (self.BUDGET - with_one_char_title)
        self.assertGreater(length_at_budget, 0)

        self.doc("repos/pad.md", title="X" * length_at_budget, type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        self.assertEqual(index_path.stat().st_size, self.BUDGET)
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertNotIn("INDEX.md is", v.stderr)  # exactly at budget: no warning

        self.doc("repos/pad.md", title="X" * (length_at_budget + 1), type="repo", domain="repos", status="active")
        run(self.root, "gen_index.py")
        self.assertEqual(index_path.stat().st_size, self.BUDGET + 1)
        v = run(self.root, "verify.py")
        self.assertEqual(v.returncode, 0, v.stderr)  # non-fatal — a warning, not a FAIL
        self.assertIn(f"INDEX.md is {self.BUDGET + 1} bytes (> {self.BUDGET // 1024}KB)", v.stderr)
        self.assertIn("archive finished docs", v.stderr)


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


if __name__ == "__main__":
    unittest.main()
