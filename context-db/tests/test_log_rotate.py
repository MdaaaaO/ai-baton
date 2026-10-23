"""log_rotate.py — `ctx maintain` rotates a `type: log` doc's `log_section:` (the kit's half of maintain, since
ctx-store's own pass rotates only a section its type schema names and the kit's `log` type names none).

Unit tests drive `rotate_store` on a temp store with a `write` that replaces the file; the end-to-end ones run the
adapter's Bash route and the PostToolUse hook against the pinned ctx when this machine has it and skip cleanly
otherwise. Every store is a temp dir named by CONTEXT_ROOT — never this machine's `.context/`. Stdlib unittest.
Run: make -C .claude/context-db test T=test_log_rotate."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
ADAPTER = BIN / "ctx_adapter.py"
sys.path.insert(0, str(BIN))

import ctx_adapter  # noqa: E402
import log_rotate as lr  # noqa: E402

SCRUB = ("KIT_CTX", "CTX_STORE", "CTX_LOCK_TIMEOUT", "CTX_ACTOR", "CLAUDE_CODE_SESSION_ID", "WORKSPACE_TZ")


def pinned_ctx() -> Path | None:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(cache) / "ai-baton-kit" / "ctx-store" / ctx_adapter.CTX_VERSION / "ctx"
    return p if p.is_file() and os.access(p, os.X_OK) else None


REAL_CTX = pinned_ctx()
PAD = "z" * 4000  # 8 padded entries are past the 30,000 B size guard


def log_doc(entries: list[str], section: str = "Day log", field: bool = True, doc_type: str = "log",
            extra: str = "") -> str:
    head = f"---\ntitle: Rotation\ntype: {doc_type}\ndomain: ops\nstatus: active\n"
    head += f"log_section: {section}\n" if field else ""
    return (head + "updated: 2026-10-08\n---\n\n# Rotation\n\n## Summary\n\nplain prose\n\n"
            + f"## {section}\n\n" + "\n".join(entries) + "\n" + extra)


def newest_first(n: int = 8) -> list[str]:
    return [f"- 2026-10-{day:02d} — entry {day} {PAD}" for day in range(n, 0, -1)]


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        (self.root / "ops").mkdir(parents=True)
        self.writes: list[str] = []
        self.before_remove = None  # a callable run between the pass's read of a doc and its remove: a concurrent write

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def write(self, key: str, text: str) -> None:
        self.writes.append(key)
        self.put(f"{key}.md", text)

    def remove(self, key: str, old: str) -> None:
        """What `ctx str_replace --new ""` does: remove `old` from the doc as it is now, once, or refuse."""
        if self.before_remove:
            self.before_remove()
        self.writes.append(key)
        path = self.root / f"{key}.md"
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise OSError("NO_MATCH: the text is not in the doc exactly once")
        path.write_text(text.replace(old, "", 1), encoding="utf-8")

    def rotate(self, **kw) -> list[str]:
        return lr.rotate_store(self.root, self.write, self.remove, today="2026-10-23", **kw)

    def lines(self, rel: str) -> list[str]:
        return [ln for ln in (self.root / rel).read_text(encoding="utf-8").splitlines() if ln.startswith("- 2026-")]


class Rotation(Store):
    def test_log_section_rotates_the_oldest_entries_of_that_section_into_the_archive(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        report = self.rotate()
        self.assertEqual(report, ["archived: 2 log entries of ops/rot → archive/ops-rot-log"])
        self.assertEqual(self.lines("ops/rot.md"), entries[:6])  # the newest 6 stay, in their order
        archive = (self.root / "archive" / "ops-rot-log.md").read_text(encoding="utf-8")
        self.assertEqual(self.lines("archive/ops-rot-log.md"), entries[6:])  # moved verbatim, same order
        self.assertIn("\n## Day log\n", archive)
        self.assertIn("type: log\ndomain: ops\nstatus: active\nlog_section: Day log\n", archive)
        self.assertIn("## Summary\n\nplain prose", (self.root / "ops" / "rot.md").read_text(encoding="utf-8"))

    def test_a_second_run_changes_nothing(self):
        self.put("ops/rot.md", log_doc(newest_first()))
        self.rotate()
        self.writes.clear()
        self.assertEqual(self.rotate(), [])  # six padded entries are back under the size guard
        self.assertEqual(self.writes, [])

    def test_an_oldest_first_section_keeps_its_tail_and_appends_to_an_existing_archive(self):
        entries = list(reversed(newest_first()))  # oldest first
        self.put("ops/rot.md", log_doc(entries))
        self.put("archive/ops-rot-log.md", log_doc(["- 2026-09-01 — earlier", "- 2026-09-02 — earlier too"],
                                               extra="\n## Notes\n\nafter\n"))
        self.rotate()
        self.assertEqual(self.lines("ops/rot.md"), entries[2:])
        self.assertEqual(self.lines("archive/ops-rot-log.md"),
                         ["- 2026-09-01 — earlier", "- 2026-09-02 — earlier too"] + entries[:2])
        self.assertTrue((self.root / "archive" / "ops-rot-log.md").read_text(encoding="utf-8").endswith("after\n"))

    def test_a_newest_first_section_prepends_to_an_archive_that_already_has_the_section(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        self.put("archive/ops-rot-log.md", log_doc(["- 2026-09-02 — earlier too", "- 2026-09-01 — earlier"]))
        self.rotate()
        self.assertEqual(self.lines("archive/ops-rot-log.md"),
                         entries[6:] + ["- 2026-09-02 — earlier too", "- 2026-09-01 — earlier"])

    def test_an_archive_without_the_section_gets_it_at_the_end(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        self.put("archive/ops-rot-log.md", log_doc(["- 2026-09-01 — earlier"], section="Other"))
        self.rotate()
        text = (self.root / "archive" / "ops-rot-log.md").read_text(encoding="utf-8")
        self.assertIn("## Other\n\n- 2026-09-01 — earlier", text)
        self.assertTrue(text.rstrip().endswith(entries[7]))
        self.assertIn("\n## Day log\n\n" + entries[6], text)

    def test_the_section_is_whatever_the_doc_names(self):
        entries = [f"- 2026-10-{d:02d} — run {d} {PAD}" for d in range(8, 0, -1)]
        self.put("ops/runs.md", log_doc(entries, section="Run history"))
        self.rotate()
        self.assertEqual(self.lines("archive/ops-runs-log.md"), entries[6:])
        self.assertIn("\n## Run history\n", (self.root / "archive" / "ops-runs-log.md").read_text(encoding="utf-8"))

    def test_a_quoted_field_value_is_read_unquoted(self):
        self.put("ops/rot.md", log_doc(newest_first()).replace("log_section: Day log", 'log_section: "Day log"'))
        self.assertEqual(self.rotate(), ["archived: 2 log entries of ops/rot → archive/ops-rot-log"])

    def test_comments_and_blank_lines_are_not_entries(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(["<!-- a comment", "   spanning lines -->", ""] + entries))
        self.rotate()
        self.assertIn("<!-- a comment", (self.root / "ops" / "rot.md").read_text(encoding="utf-8"))
        self.assertEqual(self.lines("archive/ops-rot-log.md"), entries[6:])

    def test_a_heading_inside_fenced_code_does_not_end_the_section(self):
        entries = newest_first()
        fenced = ["```", "## not a heading", "```"]
        self.put("ops/rot.md", log_doc(entries + fenced))
        self.rotate()
        # the fence belongs to the last entry (its continuation), so it is rotated with it
        archive = (self.root / "archive" / "ops-rot-log.md").read_text(encoding="utf-8")
        self.assertIn("## not a heading", archive)
        self.assertEqual(self.lines("archive/ops-rot-log.md"), newest_first()[6:])
        self.assertNotIn("## not a heading", (self.root / "ops" / "rot.md").read_text(encoding="utf-8"))

    def test_a_missing_or_repeated_section_is_left_alone(self):
        entries = newest_first()
        self.put("ops/gone.md", log_doc(entries).replace("## Day log", "## Elsewhere"))
        self.put("ops/twice.md", log_doc(entries, extra="\n## Day log\n\n- 2026-10-09 — again\n"))
        report = self.rotate()
        self.assertEqual(len(report), 2)
        self.assertTrue(all("nothing to rotate" in line for line in report), report)
        self.assertEqual(self.writes, [])

    def test_a_small_doc_and_a_doc_of_another_type_and_archive_are_never_touched(self):
        self.put("ops/small.md", log_doc(["- 2026-10-01 — one"]))
        self.put("ops/epic.md", log_doc(newest_first(), doc_type="epic"))
        self.put("archive/big-log.md", log_doc(newest_first()))
        self.assertEqual(self.rotate(), [])
        self.assertEqual(self.writes, [])

    def test_keep_comes_from_the_caller(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        self.rotate(keep=3)
        self.assertEqual(self.lines("ops/rot.md"), entries[:3])
        self.assertEqual(self.lines("archive/ops-rot-log.md"), entries[3:])

    def test_the_archive_is_written_before_the_source(self):
        self.put("ops/rot.md", log_doc(newest_first()))
        self.rotate()
        self.assertEqual(self.writes, ["archive/ops-rot-log", "ops/rot"])  # archive first, then the remove

    def test_a_failed_write_is_reported_and_loses_nothing(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))

        def refuse(key: str, text: str) -> None:
            raise OSError("ctx create: READ_ONLY")

        report = lr.rotate_store(self.root, refuse, refuse, today="2026-10-23")
        self.assertEqual(report, ["log: ops/rot not rotated — ctx create: READ_ONLY"])
        self.assertEqual(self.lines("ops/rot.md"), entries)


def multi(day: int, extra: int = 2) -> list[str]:
    """One dated entry with `extra` continuation lines, padded."""
    return [f"- 2026-10-{day:02d} — entry {day} {PAD}"] + [f"  detail {day}.{k}" for k in range(1, extra + 1)]


def flat(blocks: list[list[str]]) -> list[str]:
    return [line for block in blocks for line in block]


class Boundaries(Store):
    """The cut falls between entries: an entry is its dated line plus continuation lines, a fence is never split."""

    def text_of(self, rel: str) -> list[str]:
        return (self.root / rel).read_text(encoding="utf-8").splitlines()

    def test_a_multi_line_entry_on_the_keep_boundary_moves_whole(self):
        blocks = [multi(day) for day in range(8, 0, -1)]  # newest first, six kept, entries 2 and 1 move
        self.put("ops/rot.md", log_doc(flat(blocks)))
        self.rotate()
        source, archive = self.text_of("ops/rot.md"), self.text_of("archive/ops-rot-log.md")
        for block in blocks[:6]:
            self.assertEqual(block, source[source.index(block[0]):source.index(block[0]) + len(block)])
        for block in blocks[6:]:
            self.assertEqual(block, archive[archive.index(block[0]):archive.index(block[0]) + len(block)])
        self.assertFalse([ln for ln in source if ln.startswith("  detail 2.") or ln.startswith("  detail 1.")])
        self.assertEqual(sum(ln.startswith("  detail") for ln in archive), 4)  # no continuation line left behind

    def test_an_oldest_first_multi_line_entry_on_the_boundary_moves_whole(self):
        blocks = [multi(day) for day in range(1, 9)]
        self.put("ops/rot.md", log_doc(flat(blocks)))
        self.rotate()
        source, archive = self.text_of("ops/rot.md"), self.text_of("archive/ops-rot-log.md")
        self.assertEqual(sum(ln.startswith("  detail") for ln in source), 12)
        self.assertEqual(sum(ln.startswith("  detail") for ln in archive), 4)
        self.assertEqual([ln for ln in archive if ln.startswith("- 2026-")][:2], [blocks[0][0], blocks[1][0]])

    def test_a_fenced_block_after_the_last_kept_entry_stays_with_it_whatever_it_contains(self):
        fence = ["```", "- 2026-10-31 — looks like an entry but is code", "## nor a heading", "```"]
        blocks = [multi(day, 0) for day in range(8, 0, -1)]
        blocks[5] = blocks[5] + fence  # the sixth entry, the last one kept, ends in a fence
        self.put("ops/rot.md", log_doc(flat(blocks)))
        self.rotate()
        source, archive = self.text_of("ops/rot.md"), self.text_of("archive/ops-rot-log.md")
        at = source.index(blocks[5][0])
        self.assertEqual(source[at:at + 5], blocks[5])  # entry and fence, together
        self.assertEqual(source.count("```"), 2)
        self.assertNotIn("```", archive)
        self.assertEqual([ln for ln in archive if ln.startswith("- 2026-")], [blocks[6][0], blocks[7][0]])

    def test_a_fenced_block_inside_the_first_moved_entry_moves_with_it(self):
        fence = ["```", "- 2026-10-31 — code", "```"]
        blocks = [multi(day, 0) for day in range(8, 0, -1)]
        blocks[6] = blocks[6] + fence
        self.put("ops/rot.md", log_doc(flat(blocks)))
        self.rotate()
        self.assertNotIn("```", "\n".join(self.text_of("ops/rot.md")))
        archive = self.text_of("archive/ops-rot-log.md")
        at = archive.index(blocks[6][0])
        self.assertEqual(archive[at:at + 4], blocks[6])

    def test_a_section_without_dated_lines_cuts_at_unindented_lines(self):
        blocks = [[f"- run {n} {PAD}", f"    output {n}"] for n in range(8, 0, -1)]
        self.put("ops/rot.md", log_doc(flat(blocks)))
        self.rotate()
        archive = self.text_of("archive/ops-rot-log.md")
        self.assertEqual([ln for ln in archive if ln.startswith("- run")], [b[0] for b in blocks[6:]])
        self.assertEqual([ln for ln in archive if ln.startswith("    output")], [b[1] for b in blocks[6:]])
        source = self.text_of("ops/rot.md")
        self.assertEqual([ln for ln in source if ln.startswith("    output")], [b[1] for b in blocks[:6]])

    def test_a_comment_before_the_first_entry_and_after_the_last_stays_put(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(["<!-- head note -->", ""] + entries + ["", "<!-- tail note -->"]))
        self.rotate()
        source = (self.root / "ops" / "rot.md").read_text(encoding="utf-8")
        archive = (self.root / "archive" / "ops-rot-log.md").read_text(encoding="utf-8")
        self.assertIn("<!-- head note -->", source)
        self.assertIn("<!-- tail note -->", source)
        self.assertNotIn("note -->", archive)
        self.assertNotIn("\n\n\n\n", source)  # the removal leaves no gap of blank lines behind


class Naming(Store):
    def test_archive_key_keeps_the_folder_path(self):
        self.assertEqual(lr.archive_key("ops/rot"), "archive/ops-rot-log")
        self.assertEqual(lr.archive_key("repos/team/rot"), "archive/repos-team-rot-log")
        self.assertEqual(lr.archive_key("rot"), "archive/rot-log")

    def test_two_docs_of_one_name_in_two_folders_get_two_archives(self):
        a = [f"- 2026-10-{d:02d} — ops {d} {PAD}" for d in range(8, 0, -1)]
        b = [f"- 2026-10-{d:02d} — repos {d} {PAD}" for d in range(8, 0, -1)]
        self.put("ops/rot.md", log_doc(a))
        self.put("repos/rot.md", log_doc(b))
        report = self.rotate()
        self.assertEqual(report, ["archived: 2 log entries of ops/rot → archive/ops-rot-log",
                                  "archived: 2 log entries of repos/rot → archive/repos-rot-log"])
        self.assertEqual(self.lines("archive/ops-rot-log.md"), a[6:])
        self.assertEqual(self.lines("archive/repos-rot-log.md"), b[6:])
        self.assertFalse((self.root / "archive" / "rot-log.md").exists())


class Concurrency(Store):
    """The source is changed by someone else between the pass's read and its remove."""

    def section_top(self, line: str) -> None:
        path = self.root / "ops" / "rot.md"
        path.write_text(path.read_text(encoding="utf-8").replace("## Day log\n\n", f"## Day log\n\n{line}\n", 1),
                        encoding="utf-8")

    def test_an_entry_appended_in_the_window_is_kept(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        self.before_remove = lambda: self.section_top("- 2026-10-24 — appended in the window")
        report = self.rotate()
        self.assertEqual(report, ["archived: 2 log entries of ops/rot → archive/ops-rot-log"])
        self.assertEqual(self.lines("ops/rot.md"), ["- 2026-10-24 — appended in the window"] + entries[:6])
        self.assertEqual(self.lines("archive/ops-rot-log.md"), entries[6:])

    def test_a_block_that_changed_in_the_window_is_not_removed_and_the_next_pass_does_not_duplicate(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        path = self.root / "ops" / "rot.md"
        self.before_remove = lambda: path.write_text(
            path.read_text(encoding="utf-8").replace("entry 1 ", "entry 1 (edited) ", 1), encoding="utf-8")
        report = self.rotate()
        self.assertEqual(len(report), 1)
        self.assertTrue(report[0].startswith("log: ops/rot not rotated — NO_MATCH"), report)
        self.assertEqual(len(self.lines("ops/rot.md")), 8)  # nothing lost from the source
        self.assertEqual(self.lines("archive/ops-rot-log.md"), entries[6:])  # the archive has what was moved
        # the edit is undone and the pass runs again: the archive already holds both entries, so it is not touched
        self.before_remove = None
        path.write_text(path.read_text(encoding="utf-8").replace("entry 1 (edited) ", "entry 1 "), encoding="utf-8")
        self.writes.clear()
        self.assertEqual(self.rotate(), ["archived: 2 log entries of ops/rot → archive/ops-rot-log"])
        self.assertEqual(self.writes, ["ops/rot"])  # no archive write
        self.assertEqual(self.lines("archive/ops-rot-log.md"), entries[6:])  # not duplicated
        self.assertEqual(self.lines("ops/rot.md"), entries[:6])

    def test_a_pass_cut_short_after_the_archive_write_is_finished_by_the_next(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))

        def die() -> None:
            raise OSError("ctx: LOCK_TIMEOUT")

        self.before_remove = die
        self.assertIn("not rotated — ctx: LOCK_TIMEOUT", self.rotate()[0])
        self.before_remove = None
        self.rotate()
        self.assertEqual(self.lines("archive/ops-rot-log.md"), entries[6:])
        self.assertEqual(self.lines("ops/rot.md"), entries[:6])

    def test_add_to_archive_skips_only_whole_entries_already_there(self):
        have = log_doc(["- 2026-09-02 — keep", "- 2026-09-01 — keep"])
        self.assertIsNone(lr.add_to_archive(have, [["- 2026-09-02 — keep"]], "Day log", "newest-first", "ops/x", "ops", "d"))
        out = lr.add_to_archive(have, [["- 2026-09-02 — kee"], ["- 2026-09-02 — keep"]], "Day log", "newest-first",
                                "ops/x", "ops", "d")
        self.assertIn("- 2026-09-02 — kee\n- 2026-09-02 — keep\n", out)  # a prefix of an entry is not that entry
        self.assertEqual(out.count("- 2026-09-02 — keep"), 1)  # the whole entry was already there: not added twice


class NoField(Store):
    def test_an_oversized_log_doc_without_log_section_is_reported_with_the_add_the_field_hint(self):
        entries = newest_first()
        before = log_doc(entries, field=False)
        self.put("ops/rot.md", before)
        report = self.rotate()
        self.assertEqual(len(report), 1)
        self.assertIn("ops/rot", report[0])
        self.assertIn("add `log_section: <heading>`", report[0])
        self.assertIn("split the doc by hand", report[0])
        self.assertNotIn("nothing to do", report[0])
        self.assertEqual((self.root / "ops" / "rot.md").read_text(encoding="utf-8"), before)  # untouched
        self.assertFalse((self.root / "archive").exists())

    def test_an_empty_field_value_counts_as_no_field(self):
        self.put("ops/rot.md", log_doc(newest_first()).replace("log_section: Day log", "log_section:"))
        self.assertIn("add `log_section: <heading>`", self.rotate()[0])


class TypeSchema(unittest.TestCase):
    def test_the_log_type_has_no_type_wide_section_default(self):
        data = json.loads((HERE.parent / "ctx-store" / "types" / "log.json").read_text(encoding="utf-8"))
        self.assertNotIn("section", data["log"])  # the section is a per-doc choice (log_section:)

    def test_an_epic_keeps_its_session_log_section(self):
        data = json.loads((HERE.parent / "ctx-store" / "types" / "epic.json").read_text(encoding="utf-8"))
        self.assertEqual(data["log"]["section"], "Session log")

    def test_the_log_template_documents_the_field(self):
        self.assertIn("log_section", (HERE.parent / "_templates" / "log.md").read_text(encoding="utf-8"))


@unittest.skipUnless(REAL_CTX, "the pinned ctx is not installed on this machine")
class Adapter(Store):
    """The Bash route (`ctx_adapter.py ctx maintain`) and the PostToolUse hook, on the pinned ctx."""

    def setUp(self):
        super().setUp()
        self.env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        self.env.update(CONTEXT_ROOT=str(self.root), KIT_CTX=str(REAL_CTX), CTX_NO_WALK="1", CTX_ADAPTER_NO_SYNC="1",
                        KIT_SCRATCH=str(Path(self.tmp.name) / "scratch"))

    def adapter(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ADAPTER), *args], input=stdin, env=self.env, capture_output=True,
                              text=True, cwd=self.tmp.name, timeout=120)

    def adopt(self) -> None:
        r = self.adapter("adopt")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_ctx_maintain_rotates_a_log_doc_with_log_section(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        self.adopt()
        r = self.adapter("ctx", "maintain")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("archived: 2 log entries of ops/rot → archive/ops-rot-log", r.stdout)
        self.assertEqual(self.lines("ops/rot.md"), entries[:6])
        self.assertEqual(self.lines("archive/ops-rot-log.md"), entries[6:])
        self.assertEqual(self.adapter("ctx", "maintain").returncode, 0)  # a second run changes nothing
        self.assertEqual(self.lines("ops/rot.md"), entries[:6])
        r = self.adapter("ctx", "validate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # the rewritten doc and the archive validate

    def test_ctx_maintain_names_a_log_doc_without_the_field_instead_of_staying_silent(self):
        before = log_doc(newest_first(), field=False)
        self.put("ops/rot.md", before)
        self.adopt()
        r = self.adapter("ctx", "maintain")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ops/rot", r.stdout)
        self.assertIn("add `log_section: <heading>`", r.stdout)
        self.assertEqual((self.root / "ops" / "rot.md").read_text(encoding="utf-8"), before)

    def test_an_epic_session_log_still_rotates_through_ctx_itself(self):
        entries = [f"- 2026-01-{day:02d} — entry {day} {PAD}" for day in range(1, 9)]
        self.put("ops/a.md", "---\ntitle: A\ntype: epic\ndomain: ops\nstatus: active\nupdated: 2026-01-08\n---\n# A\n\n"
                 "## Goal\n\ng\n\n## Key decisions & gotchas\n\n## Remaining work\n\n## Session log\n\n"
                 + "\n".join(entries) + "\n")
        self.adopt()
        r = self.adapter("ctx", "maintain")
        self.assertIn("archived: 2 log entries of ops/a", r.stdout)
        self.assertEqual(self.lines("ops/a.md"), entries[2:])

    def test_maintain_help_rotates_nothing(self):
        self.put("ops/rot.md", log_doc(newest_first()))
        self.adopt()
        r = self.adapter("ctx", "maintain", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.lines("ops/rot.md")), 8)

    def test_the_hook_rotates_after_a_ctx_maintain_mcp_call(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        self.adopt()
        payload = json.dumps({"tool_name": "mcp__plugin_ai-baton_ctx__ctx_maintain", "session_id": "s-1"})
        r = self.adapter("hook", "post-tool-use-async", stdin=payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.lines("ops/rot.md"), entries[:6])
        self.assertEqual(self.lines("archive/ops-rot-log.md"), entries[6:])

    def test_the_hook_leaves_other_ctx_writes_alone(self):
        self.put("ops/rot.md", log_doc(newest_first()))
        self.adopt()
        payload = json.dumps({"tool_name": "mcp__plugin_ai-baton_ctx__ctx_log", "session_id": "s-1"})
        self.assertEqual(self.adapter("hook", "post-tool-use-async", stdin=payload).returncode, 0)
        self.assertEqual(len(self.lines("ops/rot.md")), 8)


    def test_json_keeps_stdout_a_json_document_and_sends_the_report_to_stderr(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        self.adopt()
        r = self.adapter("ctx", "--json", "maintain")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        json.loads(r.stdout)  # ctx's own answer, undisturbed
        self.assertIn("archived: 2 log entries of ops/rot → archive/ops-rot-log", r.stderr)
        self.assertEqual(self.lines("ops/rot.md"), entries[:6])

    def test_an_option_maintain_does_not_have_rotates_nothing_and_ctx_refuses_it(self):
        # `ctx maintain` takes no dry-run flag and no doc key: ctx answers a usage error, so no write happens ahead of it
        self.put("ops/rot.md", log_doc(newest_first()))
        self.adopt()
        for extra in (["--dry-run"], ["ops/rot"], ["--check"]):
            r = self.adapter("ctx", "maintain", *extra)
            self.assertNotEqual(r.returncode, 0, extra)
            self.assertNotIn("archived:", r.stdout)
            self.assertEqual(len(self.lines("ops/rot.md")), 8, extra)
            self.assertFalse((self.root / "archive" / "ops-rot-log.md").exists(), extra)

    def test_a_named_store_is_not_rotated(self):
        self.put("ops/rot.md", log_doc(newest_first()))
        self.adopt()
        r = self.adapter("ctx", "--store", str(self.root), "maintain")
        self.assertNotIn("archived:", r.stdout)
        self.assertEqual(len(self.lines("ops/rot.md")), 8)

    def test_ops_remove_only_the_exact_block_under_ctxs_own_text(self):
        entries = newest_first()
        self.put("ops/rot.md", log_doc(entries))
        self.adopt()
        write, remove = ctx_adapter._ctx_ops(REAL_CTX, ctx_adapter._store_args(self.root), dict(self.env, CTX_ACTOR="test"))
        block = "\n".join(entries[6:]) + "\n"
        # someone appends through ctx between the pass's read and its remove: the append survives
        self.assertEqual(self.adapter("ctx", "log", "ops/rot", "appended", "--section", "Day log").returncode, 0)
        remove("ops/rot", block)
        got = self.lines("ops/rot.md")
        self.assertEqual([ln for ln in got if not ln.endswith("appended")], entries[:6])
        self.assertEqual(len([ln for ln in got if ln.endswith("appended")]), 1)  # the append survived the remove
        with self.assertRaises(OSError) as ctxmgr:  # the block is gone: a second remove is stale
            remove("ops/rot", block)
        self.assertIn("NO_MATCH", str(ctxmgr.exception))
        self.assertEqual(self.lines("ops/rot.md"), got)


class MaintainPlan(unittest.TestCase):
    def test_what_the_rotation_takes_from_a_command_line(self):
        plan = ctx_adapter._maintain_plan
        self.assertEqual(plan(["maintain"]), {"today": None, "json": False})
        self.assertEqual(plan(["--json", "maintain"]), {"today": None, "json": True})
        self.assertEqual(plan(["--now", "2026-12-01T00:00:00Z", "maintain"])["today"], "2026-12-01")
        self.assertEqual(plan(["--now=2026-12-01T00:00:00Z", "maintain"])["today"], "2026-12-01")
        self.assertIsNone(plan(["--store", "/tmp/x", "maintain"]))
        self.assertIsNone(plan(["--store=/tmp/x", "maintain"]))
        self.assertIsNone(plan(["maintain", "--dry-run"]))
        self.assertIsNone(plan(["maintain", "ops/rot"]))
        self.assertIsNone(plan(["maintain", "--", "x"]))
        self.assertIsNone(plan(["view", "maintain"]))


if __name__ == "__main__":
    unittest.main()
