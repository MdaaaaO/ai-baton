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


def env_for(root: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_TZ"}
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
