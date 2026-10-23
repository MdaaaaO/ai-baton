"""gen_sessions.py — the archive sweep and the two generated indexes, on a throwaway CONTEXT_ROOT."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from typing import Optional
from datetime import datetime, timedelta, timezone
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import session_stats  # noqa: E402 — same dir; the one spend-basis wording, quoted here too


def stamp(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_file(root: Path, name: str, status: str, ended_days_ago: float, next_prompt: str, hb: str = "",
                 registered: Optional[str] = "") -> Path:
    """A session doc; `registered` defaults to the heartbeat (registered, never touched since), None omits the field
    (a doc written before session.py stamped it)."""
    hb = hb or stamp(ended_days_ago)
    reg = "" if registered is None else f"registered: {registered or hb}\n"
    body = f"# Session: {name}\n\n## Next session\n\n{next_prompt}\n\n## Session stats\n\nnone\n"
    p = root / "sessions" / f"{name}.md"
    p.write_text(f"---\nsession: {name}\nref: abc123\nstatus: {status}\nepic: E-1\nworking_on: x\n"
                 f"responsibilities: y\nstats: 1 turns · 0.1h · ~$1\n{reg}heartbeat: {hb}\nupdated: 2026-09-26\n---\n\n{body}",
                 encoding="utf-8")
    return p


def env_for(root: Path, **extra: str) -> dict:
    # No WORKSPACE_TZ: the generator must resolve the zone from the temp root's own (blank) env store,
    # exactly as it does on a CI runner — a shell that exports it would otherwise mask a missing store.
    env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_TZ"}
    return {**env, "CONTEXT_ROOT": str(root), **extra}


def run(root: Path, *args: str, days: str = "7", max_ended: str = "5") -> str:
    env = env_for(root, SESSION_ARCHIVE_DAYS=days, SESSION_ARCHIVE_NOPROMPT_HOURS="48",
                  SESSION_INDEX_MAX_ENDED=max_ended)
    r = subprocess.run([sys.executable, str(BIN / "gen_sessions.py"), *args], env=env, cwd=BIN,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def blank_root(name: str = ".context") -> tuple[tempfile.TemporaryDirectory, Path]:
    """A throwaway CONTEXT_ROOT (`<tmp>/<name>`, a workspace's usual shape) with a blank env store
    (gen_sessions resolves the display timezone from it) — never the live store; the caller owns cleanup."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / name
    root.mkdir()
    r = subprocess.run([sys.executable, str(BIN / "kb.py"), "init", "--blank"], env=env_for(root),
                       cwd=BIN, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (root / "sessions").mkdir()
    return tmp, root


class ArchiveSweep(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = blank_root()
        session_file(self.root, "live", "active", 0, "")
        session_file(self.root, "fresh-with-prompt", "ended", 1, "Register as fresh-2, read X.")
        session_file(self.root, "fresh-no-prompt", "ended", 1, "")  # crashed session inside the 48 h grace window
        session_file(self.root, "stale-no-prompt", "ended", 3, "")
        session_file(self.root, "old-with-prompt", "ended", 30, "Register as old-2.")
        session_file(self.root, "bad-heartbeat", "ended", 0, "Register as bad-2.", hb="yesterday")
        (self.root / "sessions" / "_ledger.md").write_text("| a | b |\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_moves_nothing(self):
        out = run(self.root, "--dry-run")
        self.assertIn("would archive stale-no-prompt", out)
        self.assertIn("would archive old-with-prompt", out)
        self.assertNotIn("would archive fresh-with-prompt", out)
        self.assertNotIn("would archive fresh-no-prompt", out)
        self.assertNotIn("would archive bad-heartbeat", out)
        self.assertFalse((self.root / "sessions" / "archive").exists())
        self.assertFalse((self.root / "SESSION_INDEX.md").exists())

    def test_sweep_moves_only_what_nothing_waits_on(self):
        out = run(self.root)
        self.assertIn("2 archived (2 in sessions/archive/)", out)
        live = sorted(p.name for p in (self.root / "sessions").glob("*.md"))
        self.assertEqual(live, ["_ledger.md", "bad-heartbeat.md", "fresh-no-prompt.md", "fresh-with-prompt.md", "live.md"])
        arch = sorted(p.name for p in (self.root / "sessions" / "archive").glob("*.md"))
        self.assertEqual(arch, ["INDEX.md", "old-with-prompt.md", "stale-no-prompt.md"])
        idx = (self.root / "SESSION_INDEX.md").read_text()
        self.assertIn("`fresh-with-prompt`", idx)
        # fresh-no-prompt is still live (inside the 48h grace window) but has nothing to hand over —
        # not listed in the Ended table at all, unlike the old first-line-preview behaviour. bad-heartbeat
        # (unparsable heartbeat, never swept) has a real prompt and stays listed alongside it.
        self.assertNotIn("fresh-no-prompt", idx)
        self.assertNotIn("old-with-prompt", idx)
        self.assertNotIn("<details>", idx)
        self.assertIn("2 ended listed", idx)
        self.assertIn("1 ended with no prompt (not listed)", idx)
        # the starter, never the prompt's own words
        self.assertIn("Register as the successor of fresh-with-prompt; your prompt is in "
                      ".context/sessions/fresh-with-prompt.md § Next session.", idx)
        self.assertNotIn("Register as fresh-2", idx)
        self.assertNotIn("Register as bad-2", idx)
        aidx = (self.root / "sessions" / "archive" / "INDEX.md").read_text()
        self.assertIn("| `old-with-prompt` | E-1 |", aidx)
        self.assertIn("[.context/sessions/archive/old-with-prompt.md](old-with-prompt.md)", aidx)
        self.assertNotIn("Register as old-2.", aidx)
        self.assertIn("2 archived_", aidx)
        # a second run is a no-op that keeps the archive index in step
        out2 = run(self.root)
        self.assertNotIn(" archived", out2)  # no new moves
        self.assertIn("(2 in sessions/archive/)", out2)

    def test_no_archive_flag_and_zero_days(self):
        run(self.root, "--no-archive")
        self.assertFalse((self.root / "sessions" / "archive").exists())
        self.assertTrue((self.root / "SESSION_INDEX.md").exists())
        run(self.root, days="0")  # every ended session — except one whose ended-at cannot be parsed
        self.assertEqual(sorted(p.name for p in (self.root / "sessions").glob("*.md")),
                         ["_ledger.md", "bad-heartbeat.md", "live.md"])

    def test_name_collision_gets_dated_suffix(self):
        run(self.root)  # archives stale-no-prompt
        session_file(self.root, "stale-no-prompt", "ended", 4, "")
        run(self.root)
        names = sorted(p.name for p in (self.root / "sessions" / "archive").glob("stale-no-prompt*.md"))
        self.assertEqual(len(names), 2)
        self.assertTrue(any(n.startswith("stale-no-prompt-20") for n in names))

    def test_vanished_file_is_skipped_not_fatal(self):
        # a concurrent regen sweeps a row between our glob and our os.replace: in-process, with archive_path
        # patched to remove the source first, so the real os.replace raises and the except branch is exercised
        import importlib
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        env = env_for(self.root, SESSION_ARCHIVE_DAYS="7", SESSION_ARCHIVE_NOPROMPT_HOURS="48")
        with mock.patch.dict(os.environ, env, clear=True):  # undone on exit — nothing leaks into later tests
            sys.path.insert(0, str(BIN))
            try:
                gen = importlib.reload(importlib.import_module("gen_sessions"))
            finally:
                sys.path.pop(0)
            real = gen.archive_path

            def steal_then_path(name, m):
                dst = real(name, m)
                if name == "old-with-prompt":
                    os.unlink(m["_path"])  # the other regen got there first
                return dst

            buf = io.StringIO()
            with mock.patch.object(gen, "archive_path", steal_then_path), redirect_stdout(buf):
                rc = gen.main([])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("1 archived", out)  # stale-no-prompt moved, old-with-prompt skipped, no traceback
        self.assertNotIn("old-with-prompt", (self.root / "SESSION_INDEX.md").read_text())
        self.assertFalse((self.root / "sessions" / "archive" / "old-with-prompt.md").exists())

    def test_doc_without_session_field_restores_by_stem(self):
        # a hand-written doc has no `session:` line — the sweep names it by its stem, so the restore must too
        p = session_file(self.root, "handmade", "ended", 3, "")
        p.write_text("\n".join(l for l in p.read_text().splitlines() if not l.startswith("session:")) + "\n")
        run(self.root)
        self.assertTrue((self.root / "sessions" / "archive" / "handmade.md").exists())
        r = subprocess.run([sys.executable, str(BIN / "session.py"), "touch", "--name", "handmade", "--no-stats"],
                           env=env_for(self.root), cwd=BIN, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restored handmade.md from sessions/archive/handmade.md", r.stdout)
        self.assertTrue((self.root / "sessions" / "handmade.md").exists())
        live = self.root / "sessions" / "handmade.md"
        self.assertIn("session: handmade", live.read_text())  # the write-back names the doc, no blank field
        self.assertFalse((self.root / "sessions" / "archive" / "handmade.md").exists())  # moved, not copied
        # age it past the window again: the round-trip must land under the same archive name, never `.md`
        old_hb = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        live.write_text(re.sub(r"^heartbeat: .*$", f"heartbeat: {old_hb}", live.read_text(), flags=re.M))
        run(self.root)
        self.assertEqual(sorted(p.name for p in (self.root / "sessions" / "archive").glob("handmade*.md")), ["handmade.md"])
        self.assertFalse((self.root / "sessions" / "archive" / ".md").exists())

    def test_resumed_session_refines_handoff_after_archive(self):
        # crashed session: ended without a prompt, archived after the grace window, resumed later —
        # session-end … --next must find the file (moved back from the archive) and the prompt then keeps it live
        run(self.root)
        self.assertTrue((self.root / "sessions" / "archive" / "stale-no-prompt.md").exists())
        nxt = self.root / "next.md"
        nxt.write_text("Register as stale-2, re-arm the watch on #1.")
        r = subprocess.run([sys.executable, str(BIN / "session.py"), "end", "--name", "stale-no-prompt",
                            "--next", str(nxt), "--no-stats"], env=env_for(self.root), cwd=BIN,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restored stale-no-prompt.md from sessions/archive/", r.stdout)
        self.assertTrue((self.root / "sessions" / "stale-no-prompt.md").exists())
        self.assertFalse((self.root / "sessions" / "archive" / "stale-no-prompt.md").exists())
        idx = (self.root / "SESSION_INDEX.md").read_text()
        self.assertIn("`stale-no-prompt`", idx.split("## Ended")[1])
        self.assertIn("Register as the successor of stale-no-prompt; your prompt is in "
                      ".context/sessions/stale-no-prompt.md § Next session.", idx)
        self.assertNotIn("Register as stale-2", idx)  # the prompt's own words never land in the index

    def test_restore_picks_the_newest_archived_file_for_a_reused_name(self):
        run(self.root)  # stale-no-prompt (3 d) → archive/stale-no-prompt.md
        session_file(self.root, "stale-no-prompt", "ended", 2.5, "")  # same name, later lane, 60 h > 48 h
        run(self.root)  # → archive/stale-no-prompt-YYYYMMDD.md (name taken)
        self.assertEqual(len(list((self.root / "sessions" / "archive").glob("stale-no-prompt*.md"))), 2)
        session_file(self.root, "stale-no-prompt-20260901", "ended", 2.2, "")  # a lane named after a date, newer
        run(self.root)  # swept too — must never be restored under the other name
        # a successor re-registering the swept name (step 1: "register under the name it proposes") continues
        # the newest doc — never the older lane's — and a later touch finds the live file
        outs = []
        for cmd in (["register"], ["touch"]):
            r = subprocess.run([sys.executable, str(BIN / "session.py"), cmd[0], "--name", "stale-no-prompt",
                                *cmd[1:], "--no-stats"], env=env_for(self.root), cwd=BIN,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout)
        self.assertRegex(outs[0], r"restored stale-no-prompt\.md from sessions/archive/stale-no-prompt-\d{8}\.md")
        self.assertNotIn("restored", outs[1])
        left = sorted(p.name for p in (self.root / "sessions" / "archive").glob("stale-no-prompt*.md"))
        self.assertEqual(left, ["stale-no-prompt-20260901.md", "stale-no-prompt.md"])  # older lane + stranger stay archived
        self.assertIn("status: active", (self.root / "sessions" / "stale-no-prompt.md").read_text())  # re-registered = active
        self.assertIn("`stale-no-prompt`", (self.root / "SESSION_INDEX.md").read_text().split("## Ended")[0])

    def test_bad_days_value_falls_back(self):
        out = run(self.root, "--dry-run", days="abc")
        self.assertIn("would archive old-with-prompt", out)
        self.assertNotIn("would archive fresh-with-prompt", out)


class ConsumedHandoff(unittest.TestCase):
    """An ended session whose § Next session names a successor that is registered leaves at the next sweep, age aside."""

    def setUp(self):
        self.tmp, self.root = blank_root()

    def tearDown(self):
        self.tmp.cleanup()

    def live(self):
        return sorted(p.stem for p in (self.root / "sessions").glob("*.md") if not p.name.startswith("_"))

    def test_chain_archives_consumed_links_and_keeps_the_head(self):
        session_file(self.root, "sess-a", "ended", 1, "Register as sess-b, pick up the parser work.")
        session_file(self.root, "sess-b", "ended", 0.5, "Register as `sess-c`, then re-arm the watch.")
        session_file(self.root, "sess-c", "active", 0, "")
        out = run(self.root, "--dry-run")
        self.assertIn("would archive sess-a", out)
        self.assertIn("would archive sess-b", out)
        self.assertIn("(successor sess-b registered)", out)
        self.assertIn("(successor sess-c registered)", out)
        self.assertNotIn("would archive sess-c", out)
        self.assertEqual(self.live(), ["sess-a", "sess-b", "sess-c"])  # dry run moves nothing
        run(self.root)
        self.assertEqual(self.live(), ["sess-c"])
        self.assertEqual(sorted(p.stem for p in (self.root / "sessions" / "archive").glob("sess-*.md")),
                         ["sess-a", "sess-b"])

    def test_unregistered_successor_stays(self):
        session_file(self.root, "sess-d", "ended", 1, "Register as sess-e, pick up the parser work.")
        out = run(self.root, "--dry-run")
        self.assertNotIn("would archive", out)
        run(self.root)
        self.assertEqual(self.live(), ["sess-d"])
        self.assertIn("`sess-d`", (self.root / "SESSION_INDEX.md").read_text())

    def archived(self, name: str, ended_days_ago: float, next_prompt: str = "") -> None:
        """A session already swept to sessions/archive/ (written there directly, ended `ended_days_ago` ago)."""
        p = session_file(self.root, name, "ended", ended_days_ago, next_prompt)
        (self.root / "sessions" / "archive").mkdir(exist_ok=True)
        p.rename(self.root / "sessions" / "archive" / p.name)

    def test_successor_archived_after_the_hand_off_counts_as_registered(self):
        self.archived("sess-f", 0.5)  # registered after sess-h ended, ran, ended and was swept
        session_file(self.root, "sess-h", "ended", 1, "Register as sess-f, continue.")
        self.assertIn("would archive sess-h", run(self.root, "--dry-run"))
        # a collision-suffixed archive file still names its base session
        (self.root / "sessions" / "archive" / "sess-f.md").rename(self.root / "sessions" / "archive" / "sess-f-20260901.md")
        self.assertIn("(successor sess-f registered)", run(self.root, "--dry-run"))

    def test_name_that_predates_the_hand_off_is_not_a_successor(self):
        self.archived("sess-f", 30)  # an older lane that happens to carry the name
        session_file(self.root, "sess-h", "ended", 1, "Register as sess-f, continue.")
        self.assertNotIn("would archive sess-h", run(self.root, "--dry-run"))
        run(self.root)
        self.assertEqual(self.live(), ["sess-h"])
        # the same for a live ended lane that was already there when sess-h ended
        session_file(self.root, "sess-o", "ended", 5, "")
        session_file(self.root, "sess-p", "ended", 1, "Register as sess-o, continue.")
        self.assertNotIn("would archive sess-p", run(self.root, "--dry-run"))
        # ... and a lane that was already live when the hand-off ended stays no successor after its next touch:
        # the heartbeat moves past the end, the `registered` stamp does not
        session_file(self.root, "sess-q", "active", 2, "")
        session_file(self.root, "sess-r", "ended", 1, "Register as sess-q, continue.")
        self.assertNotIn("would archive sess-r", run(self.root, "--dry-run"))
        session_file(self.root, "sess-q", "active", 0, "", registered=stamp(2))  # touched since, registered before
        self.assertNotIn("would archive sess-r", run(self.root, "--dry-run"))
        run(self.root)
        self.assertIn("sess-r", self.live())

    def test_a_fresh_register_after_the_end_consumes_the_hand_off(self):
        session_file(self.root, "sess-r", "ended", 1, "Register as sess-y, continue.")
        session_file(self.root, "sess-y", "active", 0, "", registered=stamp(0.5))  # registered after sess-r ended
        out = run(self.root, "--dry-run")
        self.assertIn("would archive sess-r", out)
        self.assertIn("(successor sess-y registered)", out)
        # an older ended lane of that name, re-registered after the end, carries the new stamp: consumed too
        session_file(self.root, "sess-o", "ended", 1, "Register as sess-z, continue.")
        session_file(self.root, "sess-z", "active", 0, "", registered=stamp(0.2))
        self.assertIn("(successor sess-z registered)", run(self.root, "--dry-run"))

    def test_a_lane_without_a_registered_stamp_is_never_a_successor(self):
        session_file(self.root, "sess-r", "ended", 1, "Register as sess-y, continue.")
        session_file(self.root, "sess-y", "active", 0, "", registered=None)  # written before the field existed
        self.assertNotIn("would archive sess-r", run(self.root, "--dry-run"))

    def test_equal_timestamps_and_unparsable_ones_do_not_count(self):
        hb = stamp(1)  # relative, never a fixed date: one that ages past ARCHIVE_DAYS archives both lanes by age
        session_file(self.root, "sess-s", "ended", 0, "Register as sess-t.", hb=hb)
        session_file(self.root, "sess-t", "active", 0, "", hb=hb)  # same second: not provably later
        session_file(self.root, "sess-u", "ended", 0, "Register as sess-v.", hb=hb)
        session_file(self.root, "sess-v", "active", 0, "", hb="yesterday")
        self.assertNotIn("would archive sess-s", run(self.root, "--dry-run"))
        self.assertNotIn("would archive sess-u", run(self.root, "--dry-run"))

    def test_successor_in_any_live_status_counts(self):
        session_file(self.root, "sess-i", "ended", 1, "Register as sess-j.")
        session_file(self.root, "sess-j", "idle", 0, "")
        self.assertIn("would archive sess-i", run(self.root, "--dry-run"))

    def test_self_reference_and_no_successor_note_do_not_count(self):
        session_file(self.root, "sess-k", "ended", 1, "Register as sess-k again later.")
        session_file(self.root, "sess-l", "ended", 1, "No successor. Register as sess-k if you disagree.")
        out = run(self.root, "--dry-run")
        self.assertNotIn("would archive", out)

    def test_windows_still_apply_without_a_named_successor(self):
        session_file(self.root, "sess-m", "ended", 1, "Read the context doc and carry on.")  # real prompt, no successor
        session_file(self.root, "sess-n", "ended", 30, "Read the context doc and carry on.")
        out = run(self.root, "--dry-run")
        self.assertNotIn("would archive sess-m", out)
        self.assertIn("would archive sess-n", out)
        self.assertIn("> 7d", out)

    def consumed(self, prompt: str, successor: str = "sess-x1") -> bool:
        """Whether an ended session with this § Next session is swept because `successor` registered."""
        for p in (self.root / "sessions").glob("*.md"):
            p.unlink()
        session_file(self.root, "sess-w", "ended", 1, prompt)
        session_file(self.root, successor, "active", 0, "")
        return "would archive sess-w" in run(self.root, "--dry-run")

    def test_every_register_wording_names_its_successor(self):
        for prompt in (
            "Register as sess-x1, pick up the parser.",
            "Register as session sess-x1 (successor of sess-w); your prompt is below.",
            "Register as `sess-x1`.",
            'Register as "sess-x1" and re-arm the watch.',
            "register under the name sess-x1, then read the doc.",
            "Register as the successor of sess-w; your name is sess-x1.",
            "Register as the successor of sess-w. Session name: sess-x1.",
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(self.consumed(prompt))

    def test_wording_that_names_no_successor_never_sweeps(self):
        # the word in the name slot, a placeholder, the predecessor in "successor of", and non-convention names
        for prompt in (
            "Register as session <name> (successor of sess-w).",
            "Register as the successor of sess-x1; your prompt is in .context/sessions/sess-w.md § Next session.",
            "Register as NAME, then carry on.",
            "Register as <name>.",
            "Register as the lane successor.",
            "Register as sess-x1-this-name-is-far-too-long-for-the-convention.",
            "Register as sess.",
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(self.consumed(prompt))

    def test_parsed_names(self):
        cases = {
            "Register as session kit-13 (successor of kit-12).": ["kit-13"],
            "Register as the successor of kit-12.": [],
            "Register as the successor of kit-12; your name is kit-14-x.": ["kit-14-x"],
            "Register as session <name>, successor of kit-12.": [],
            "Register as `kit-13`.": ["kit-13"],
            "Register under the name kit-13.": ["kit-13"],
        }
        code = "import json, sys, gen_sessions; print(json.dumps([gen_sessions.named_successors({'_next': t}) for t in json.loads(sys.argv[1])]))"
        r = subprocess.run([sys.executable, "-c", code, json.dumps(list(cases))], env=env_for(self.root), cwd=BIN,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), list(cases.values()))

    def test_archive_index_note_names_the_reason(self):
        session_file(self.root, "sess-a", "ended", 1, "Register as sess-b.")
        session_file(self.root, "sess-b", "active", 0, "")
        run(self.root)
        self.assertIn("named successor registered", (self.root / "sessions" / "archive" / "INDEX.md").read_text())
        self.assertIn("named successor has registered", (self.root / "SESSION_INDEX.md").read_text())


class EndedTablePromptPointer(unittest.TestCase):
    """#266: the Ended table points at a real next-session prompt instead of quoting its own words, and
    lists a session at all only when it left one — an empty prompt, a whitespace-only one, and a "no
    successor" note (session-handoff's wording for a lane that ended with nothing to hand over) all count
    as no prompt, same as never writing `## Next session`."""

    def setUp(self):
        self.tmp, self.root = blank_root()

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_prompt_gets_a_starter_and_a_link_not_its_own_words(self):
        session_file(self.root, "has-prompt", "ended", 0.1,
                     "Register as has-prompt-2, re-arm the watch on the release PR and finish the migration.")
        idx = run(self.root)
        text = (self.root / "SESSION_INDEX.md").read_text()
        self.assertIn("`has-prompt`", text.split("## Ended")[1])
        # the starter: paste-ready, points at the file — never the stored sentence's own words
        self.assertIn("Register as the successor of has-prompt; your prompt is in "
                      ".context/sessions/has-prompt.md § Next session.", text)
        # label = the root-relative paste-able path; href = relative to SESSION_INDEX.md's own directory
        # (.context/) — a Markdown renderer resolves the link against the linking file, not the workspace root
        self.assertIn("[.context/sessions/has-prompt.md](sessions/has-prompt.md)", text)
        self.assertNotIn("re-arm the watch on the release PR", text)
        self.assertNotIn("finish the migration", text)

    def test_session_link_href_resolves_from_the_index_file_not_the_root(self):
        # the label must stay the root-relative, paste-able path (what session-register's startup step
        # resolves), but the href must resolve against SESSION_INDEX.md's own directory — a root-relative
        # href here 404s in a Markdown renderer (VS Code preview, GitHub, Obsidian), which looks for
        # <content-root>/<content-root>/sessions/<name>.md.
        session_file(self.root, "has-prompt", "ended", 0.1, "Register as has-prompt-2.")
        run(self.root)
        text = (self.root / "SESSION_INDEX.md").read_text()
        m = re.search(r"\[(\.context/sessions/has-prompt\.md)\]\(([^)]+)\)", text)
        self.assertIsNotNone(m, text)
        label, href = m.group(1), m.group(2)
        self.assertTrue(label.startswith(".context/"))  # the content root's own basename
        self.assertEqual(href, "sessions/has-prompt.md")

    def test_starter_path_follows_a_content_root_not_named_dot_context(self):
        tmp, root = blank_root("kb-store")
        try:
            session_file(root, "has-prompt", "ended", 0.1, "Register as has-prompt-2.")
            run(root)
            text = (root / "SESSION_INDEX.md").read_text()
            self.assertIn("your prompt is in kb-store/sessions/has-prompt.md § Next session.", text)
            self.assertNotIn(".context/sessions/has-prompt.md", text)
        finally:
            tmp.cleanup()

    def test_empty_prompt_is_not_listed(self):
        session_file(self.root, "no-prompt", "ended", 0.1, "")
        run(self.root)
        text = (self.root / "SESSION_INDEX.md").read_text()
        self.assertNotIn("no-prompt", text)
        self.assertIn("0 ended listed", text)
        self.assertIn("1 ended with no prompt (not listed)", text)
        self.assertTrue((self.root / "sessions" / "no-prompt.md").exists())  # kept, just not listed

    def test_whitespace_only_prompt_is_not_listed(self):
        session_file(self.root, "blank-prompt", "ended", 0.1, "   \n\t   \n")
        run(self.root)
        text = (self.root / "SESSION_INDEX.md").read_text()
        self.assertNotIn("blank-prompt", text)
        self.assertIn("0 ended listed", text)

    def test_no_successor_note_is_not_listed(self):
        session_file(self.root, "folded-lane", "ended", 0.1,
                     "No successor: the lane folds into has-prompt.")
        session_file(self.root, "has-prompt", "ended", 0.1, "Register as has-prompt-2.")
        run(self.root)
        text = (self.root / "SESSION_INDEX.md").read_text()
        self.assertNotIn("folded-lane", text)
        self.assertIn("`has-prompt`", text.split("## Ended")[1])
        self.assertIn("1 ended listed", text)
        self.assertIn("1 ended with no prompt (not listed)", text)
        self.assertTrue((self.root / "sessions" / "folded-lane.md").exists())  # still on disk, still archivable later

    def test_no_follow_up_note_is_not_listed(self):
        session_file(self.root, "done-lane", "ended", 0.1, "No follow-up from this session.")
        run(self.root)
        self.assertNotIn("done-lane", (self.root / "SESSION_INDEX.md").read_text())

    def test_older_fold_gets_the_path_but_no_block(self):
        session_file(self.root, "newest", "ended", 0.1, "Register as newest-2.", hb=(
            datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        session_file(self.root, "older", "ended", 0.1, "Register as older-2.", hb=(
            datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        run(self.root, max_ended="1")
        idx = (self.root / "SESSION_INDEX.md").read_text()
        self.assertIn("## Ended (newest 1)", idx)
        self.assertIn("```text\nRegister as the successor of newest;", idx)
        self.assertIn("<details>", idx)
        fold = idx.split("<details>", 1)[1]
        self.assertIn("`older`", fold)
        self.assertIn("[.context/sessions/older.md](sessions/older.md)", fold)
        self.assertNotIn("```", fold)  # no starter block for a folded, older entry
        self.assertNotIn("Register as older-2", idx)  # the prompt's own words never appear anywhere

    def test_archived_session_prompt_is_a_link_not_the_prompt_text(self):
        session_file(self.root, "old-real", "ended", 30, "Register as old-real-2, finish the audit.")
        session_file(self.root, "old-blank", "ended", 30, "")
        run(self.root, days="7")
        aidx = (self.root / "sessions" / "archive" / "INDEX.md").read_text()
        # label = the root-relative path; href = relative to archive/INDEX.md's own directory
        self.assertIn("[.context/sessions/archive/old-real.md](old-real.md)", aidx)
        self.assertNotIn("finish the audit", aidx)
        self.assertIn("| `old-blank` | E-1 |", aidx)
        # old-blank's row still ends in a bare "-" for the Prompt column, not a link
        self.assertRegex(aidx, r"\| `old-blank` \| E-1 \| [^|]+ \| `old-blank\.md` \| - \|")


class SpendBasisWordingMatchesSessionStats(unittest.TestCase):
    """One definition of "session spend" (main + subagents), stated once (`session_stats.SPEND_BASIS`)
    and repeated verbatim wherever a number is shown — the footer must quote that constant, not its own
    hardcoded wording that can drift from what `session_stats.py` actually computes."""

    def setUp(self):
        self.tmp, self.root = blank_root()

    def tearDown(self):
        self.tmp.cleanup()

    def test_footer_quotes_the_shared_spend_basis_constant(self):
        run(self.root)
        idx = (self.root / "SESSION_INDEX.md").read_text()
        self.assertIn(session_stats.SPEND_BASIS, idx)
        self.assertNotIn("main session only", idx)


if __name__ == "__main__":
    unittest.main()
