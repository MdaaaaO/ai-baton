"""skills/pr-open/diagram-plan.py's three additions to the PR body plan (docs/diagrams.md): the `ddl`
facet (an object/before/after/grant WHAT table, enabled only by the `diagrams.repos.<repo>.facets.ddl`
env overlay — the same overlay mechanism every other facet override already uses), `--sketch` (the
ticket's last sketch marker for this run's repo vs the PR's changed paths, printing `Sketch: none` /
`matches` / `differs (+a, −b)`, always exit 0, parsed by `sketch.py`'s own grammar — never re-parsed
here), and the diagram lint `--check` now runs over the body's `flowchart`/`graph` Mermaid blocks (a
`(this PR)` node naming no changed path/basename/stem, or an edge with no label — warnings only, the
exit code still follows the marker/drift result). Stdlib unittest, no network (the `--pr`/`--check`
cases use the shared `gh` stub). Run: make -C .claude/context-db test."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = KIT / "context-db" / "bin"
SKILL = KIT / "skills" / "pr-open"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(SKILL))

import kb  # noqa: E402
import kit_profile  # noqa: E402
from tests.fakes.gh_stub import write_gh_stub  # noqa: E402

REPO = "acme/widgets"  # the kit's own placeholder (CONTRIBUTING.md § Placeholders only)
REPO_B = "acme/gadgets"

spec = importlib.util.spec_from_file_location("diagram_plan_under_test", SKILL / "diagram-plan.py")
dp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dp)  # type: ignore[union-attr]


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = dp.main(["diagram-plan.py", *argv])
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def write(tmp: str, name: str, text: str) -> str:
    p = Path(tmp) / name
    p.write_text(text, encoding="utf-8")
    return str(p)


SKETCH_TICKET = (
    "**Sketch**\nWHERE: mod.py talks to other.py\n"
    f"<!-- sketch: repo={REPO} paths=src/mod.py,src/other.py components=- hash=aaaaaaaaaaaa -->\n"
)
SKETCH_TICKET_WITH_COMPONENT = (
    "**Sketch**\nWHERE: the orders model\n"
    f"<!-- sketch: repo={REPO} paths=- components=orders hash=bbbbbbbbbbbb -->\n"
)


# ── --sketch ─────────────────────────────────────────────────────────────────────────────────
class SketchLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_matches_when_every_entry_covers_a_changed_path_and_vice_versa(self):
        f = write(self.tmp.name, "ticket.txt", SKETCH_TICKET)
        rc, out, err = run(["--repo", REPO, "--files", "src/mod.py", "src/other.py", "--sketch", f])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "Sketch: matches")

    def test_component_matches_a_changed_path_by_stem(self):
        f = write(self.tmp.name, "ticket.txt", SKETCH_TICKET_WITH_COMPONENT)
        rc, out, _ = run(["--repo", REPO, "--files", "models/orders.sql", "--sketch", f])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "Sketch: matches")

    def test_differs_lists_plus_then_minus_sorted(self):
        f = write(self.tmp.name, "ticket.txt", SKETCH_TICKET)
        # src/other.py is sketched but not changed (-); src/new.py is changed but not sketched (+)
        rc, out, _ = run(["--repo", REPO, "--files", "src/mod.py", "src/new.py", "--sketch", f])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "Sketch: differs (+src/new.py, −src/other.py)")

    def test_differs_caps_the_combined_list_at_6_with_a_trailing_count(self):
        f = write(self.tmp.name, "ticket.txt", SKETCH_TICKET)  # sketches src/mod.py, src/other.py
        changed = [f"src/extra{i}.py" for i in range(8)]  # 8 unsketched changed paths (+) alone
        rc, out, _ = run(["--repo", REPO, "--files", *changed, "--sketch", f])
        self.assertEqual(rc, 0)
        line = out.strip()
        self.assertTrue(line.startswith("Sketch: differs (+src/extra0.py,src/extra1.py"), line)
        # 8 pluses + 2 minuses (src/mod.py, src/other.py never covered by these changed paths) = 10 total
        self.assertIn("+4 more", line)

    def test_none_when_the_ticket_has_no_marker_for_this_repo(self):
        f = write(self.tmp.name, "ticket.txt", SKETCH_TICKET)  # marker is for REPO, not REPO_B
        rc, out, _ = run(["--repo", REPO_B, "--files", "src/mod.py", "--sketch", f])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "Sketch: none")

    def test_none_when_the_ticket_carries_no_marker_at_all(self):
        f = write(self.tmp.name, "ticket.txt", "Sketch: SKIP (sonnet-sized, specified fix)\n")
        rc, out, _ = run(["--repo", REPO, "--files", "src/mod.py", "--sketch", f])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "Sketch: none")

    def test_reads_stdin_with_a_dash(self):
        import subprocess
        r = subprocess.run([sys.executable, str(SKILL / "diagram-plan.py"), "--repo", REPO,
                             "--files", "src/mod.py", "src/other.py", "--sketch", "-"],
                            input=SKETCH_TICKET, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "Sketch: matches")

    def test_fails_usage_when_no_repo_can_be_named(self):
        f = write(self.tmp.name, "ticket.txt", SKETCH_TICKET)
        rc, out, err = run(["--files", "src/mod.py", "--sketch", f])
        self.assertEqual(rc, 2)
        self.assertIn("--sketch needs a repo", err)


# ── the `ddl` facet: overlay-only ───────────────────────────────────────────────────────────
class DdlFacet(unittest.TestCase):
    """Points kb/kit_profile at a throw-away store (never .context/, per the worker brief's hard rules)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / "reference" / "env"
        self._saved = (kb.ENV, kb.CTX, kb.ROOT, kit_profile.ENV_DIR)
        kb.ENV = self.env
        kb.CTX = Path(self.tmp.name)
        kb.ROOT = Path(self.tmp.name).parent
        kit_profile.ENV_DIR = self.env
        kb.init_blank()
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()

    def tearDown(self):
        kb.ENV, kb.CTX, kb.ROOT, kit_profile.ENV_DIR = self._saved
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.tmp.cleanup()

    def _set_overlay(self, globs: list[str]) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = kb.main(["kb.py", "config-set", "diagrams",
                          json.dumps({"repos": {REPO: {"facets": {"ddl": globs}}}})])
        self.assertEqual(rc or 0, 0, err.getvalue())
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()

    def test_no_overlay_means_no_ddl_facet_ever(self):
        by_facet, _lines, _ignored = dp.classify([("ddl/grants.sql", 50)], REPO)
        self.assertNotIn("ddl", by_facet)

    def test_overlay_turns_on_the_facet_and_its_what_question(self):
        self._set_overlay(["**/ddl/**/*.sql"])
        by_facet, lines, _ignored = dp.classify([("ddl/grants.sql", 50)], REPO)
        self.assertEqual(list(by_facet), ["ddl"])
        p = dp.plan(by_facet, lines, "feature")
        self.assertEqual(p["dominant"], "ddl")
        q = p["questions"]["what"]
        self.assertEqual(q["facet"], "ddl")
        for word in ("object", "before", "after", "grant"):
            self.assertIn(word, q["spec"])
        self.assertNotIn("where", p["questions"])
        self.assertNotIn("runs", p["questions"])

    def test_explain_lists_the_ddl_facet_and_its_what_question(self):
        out = dp.explain()
        self.assertIn("  ddl\n    WHERE", out)
        self.assertIn("object delta", out)
        # never among DEFAULT_FACETS' own globs — ddl only exists once the env overlay names it
        self.assertNotIn("ddl", [f for f, _ in dp.DEFAULT_FACETS])


# ── the diagram lint (warn, never fail) ──────────────────────────────────────────────────────
FLOW_WITH_FINDINGS = """## Diagrams

<!-- diagram-plan: facets=service dominant=service intent=feature where=service,runs=service -->

```mermaid
flowchart LR
  a["mod.py [service] (this PR)"] --> b["mystery box (this PR)"]
```
"""

FLOW_CLEAN = """## Diagrams

<!-- diagram-plan: facets=service dominant=service intent=feature where=service,runs=service -->

```mermaid
flowchart LR
  a["mod.py [service] (this PR)"] -- calls --> c["other.py [service] (existing)"]
```
"""


class LintUnit(unittest.TestCase):
    def test_this_pr_node_naming_no_changed_path_warns(self):
        warnings = dp.lint(FLOW_WITH_FINDINGS, ["src/mod.py"])
        self.assertTrue(any("mystery box" not in w and "node 'b'" in w for w in warnings))

    def test_this_pr_node_naming_a_changed_basename_is_clean(self):
        warnings = dp.lint(FLOW_CLEAN, ["src/mod.py", "src/other.py"])
        self.assertEqual(warnings, [])

    def test_unlabelled_edge_warns_labelled_edge_does_not(self):
        self.assertEqual(dp.unlabelled_edge_count("a --> b"), 1)
        self.assertEqual(dp.unlabelled_edge_count("a -- calls --> b"), 0)
        self.assertEqual(dp.unlabelled_edge_count("a -.-> b"), 1)
        self.assertEqual(dp.unlabelled_edge_count("a -. reads .-> b"), 0)

    def test_sequence_diagram_blocks_are_never_linted(self):
        body = "```mermaid\nsequenceDiagram\n  a->>b: go\n```\n"
        self.assertEqual(dp.lint(body, ["src/mod.py"]), [])

    def test_no_mermaid_blocks_means_no_warnings(self):
        self.assertEqual(dp.lint("Docs-only, no diagram.", ["README.md"]), [])

    def test_two_consecutive_bare_edges_count_as_two_not_one_labelled(self):
        self.assertEqual(dp.unlabelled_edge_count("a --> b\n  b --> c"), 2)

    def test_two_consecutive_bare_dotted_edges_count_as_two_not_one_labelled(self):
        self.assertEqual(dp.unlabelled_edge_count("a -.-> b\n  b -.-> c"), 2)

    def test_a_one_line_bare_chain_counts_every_edge(self):
        """`a --> b --> c` and the dotted form on one line are two bare edges each — the label's first
        character may not be an arrow character, so one bare edge never swallows the next."""
        self.assertEqual(dp.unlabelled_edge_count("a --> b --> c"), 2)
        self.assertEqual(dp.unlabelled_edge_count("a -.-> b -.-> c"), 2)

    def test_one_labelled_and_one_bare_edge_counts_only_the_bare_one(self):
        self.assertEqual(dp.unlabelled_edge_count("a -- calls --> b\n  c --> d"), 1)

    def test_a_labelled_edge_whose_label_spans_no_newline_still_counts_as_labelled(self):
        self.assertEqual(dp.unlabelled_edge_count("a -- calls --> b"), 0)
        self.assertEqual(dp.unlabelled_edge_count("a -. reads .-> b"), 0)


class LintViaCheck(unittest.TestCase):
    """`--check` (needs `--pr`) prints the lint lines without changing the drift exit code — a stubbed
    `gh` on PATH stands in for the PR's files and body, no network."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name) / "bin"
        self.log = Path(self.tmp.name) / "gh.log"
        self._path = __import__("os").environ.get("PATH", "")

    def tearDown(self):
        self.tmp.cleanup()

    def _stub(self, body: str, marker_ok: bool) -> None:
        marker = ("<!-- diagram-plan: facets=service dominant=service intent=feature where=service,runs=service -->"
                  if marker_ok else "")
        full_body = body.replace(
            "<!-- diagram-plan: facets=service dominant=service intent=feature where=service,runs=service -->", marker)
        write_gh_stub(self.bin, {
            "pulls/9/files": "src/mod.py\t30\n",
            "pulls/9": json.dumps({"labels": [], "body": full_body}),
        }, self.log)

    def _run_with_stub(self, argv: list[str]) -> tuple[int, str, str]:
        import os
        import subprocess
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        r = subprocess.run([sys.executable, str(SKILL / "diagram-plan.py"), *argv],
                            capture_output=True, text=True, env=env)
        return r.returncode, r.stdout, r.stderr

    def test_warn_lines_do_not_change_the_ok_exit_code(self):
        self._stub(FLOW_WITH_FINDINGS, marker_ok=True)
        rc, out, _ = self._run_with_stub(["--pr", REPO, "9", "--check"])
        self.assertEqual(rc, 0)
        self.assertIn("LINT: warn", out)
        self.assertIn("OK ", out)

    def test_clean_body_prints_lint_ok(self):
        self._stub(FLOW_CLEAN, marker_ok=True)
        rc, out, _ = self._run_with_stub(["--pr", REPO, "9", "--check"])
        self.assertEqual(rc, 0)
        self.assertIn("LINT: ok", out)

    def test_warn_lines_still_appear_even_when_the_marker_drifted(self):
        self._stub(FLOW_WITH_FINDINGS, marker_ok=False)
        rc, out, _ = self._run_with_stub(["--pr", REPO, "9", "--check"])
        self.assertEqual(rc, 3)
        self.assertIn("LINT: warn", out)
        self.assertIn("NO MARKER", out)

    def test_the_verdict_line_is_first_even_when_the_lint_warns(self):
        # pr-event-brief and pr-watch read only the first stdout line as the verdict — the LINT:
        # line(s) must never precede it, marker-ok or not.
        self._stub(FLOW_WITH_FINDINGS, marker_ok=True)
        rc, out, _ = self._run_with_stub(["--pr", REPO, "9", "--check"])
        self.assertEqual(rc, 0)
        first_line = out.splitlines()[0]
        self.assertTrue(first_line.startswith("OK "), first_line)
        self.assertFalse(first_line.startswith("LINT:"), first_line)


if __name__ == "__main__":
    unittest.main()
