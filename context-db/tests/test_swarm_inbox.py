"""swarm-inbox's script: the local channels read without a write, the item grammar, bots and own digests dropped, the chat
tier's one-line stop, the draft file. A brand-new script, so there is no main copy to run these against.
Stdlib unittest. Run: make -C .claude/context-db test T=test_swarm_inbox."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "bin"))
from tests import hermetic_env  # noqa: E402

INBOX = HERE.parents[1] / "skills" / "swarm-inbox" / "scripts" / "inbox.py"
KEY = "epic-1"


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = hermetic_env(Path(self._tmp.name).resolve())
        self.root = Path(self.env["CONTEXT_ROOT"])
        self.root.mkdir()
        self.dir = self.root / "state" / "swarm" / KEY

    def config(self, **top) -> None:
        d = self.root / "reference" / "env"
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps({"environment": "ci", **top}), encoding="utf-8")

    def run_cli(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(INBOX), *args], capture_output=True, text=True, env=self.env,
                              input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)

    def out(self, *args: str, stdin: str | None = None) -> str:
        p = self.run_cli(*args, stdin=stdin)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def box(self, lane: str, which: str, *recs: dict, read: int | None = None) -> None:
        d = self.dir / lane
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{which}.ndjson").write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
        if read is not None:
            (d / f"{which}.read").write_text(f"{read}\n", encoding="utf-8")

    @staticmethod
    def rec(word: str, scope: str, at: str, rid: str, sender: str = "lane-7") -> dict:
        line = f"{word} {scope} v=1 id={rid} from={sender}"
        return {"word": word, "scope": scope, "line": line, "v": 1, "id": rid, "from": sender, "at": at}


class Local(Case):
    def test_nothing_is_no_op(self):
        self.assertEqual(self.out("local", KEY).strip(), "NO-OP")

    def test_reads_the_orchestrator_inbox_and_worker_outboxes_past_the_mark(self):
        """DW-6: orchestrator/in.ndjson is swept, and a line before the read mark is not shown again."""
        self.box("orchestrator", "in", self.rec("DONE", "5", "2026-11-01T10:00:00Z", "aaa1"),
                 self.rec("NEEDS", "5", "2026-11-01T11:00:00Z", "aaa2"), read=1)
        self.box("lane-7", "out", self.rec("CLAIMED", "7", "2026-11-01T09:00:00Z", "bbb1"))
        got = self.out("local", KEY).splitlines()
        self.assertEqual(len(got), 2, got)
        self.assertIn("src=orchestrator-in ref=aaa2", got[1])
        self.assertTrue(got[0].startswith("ITEM line 7 src=out:lane-7 ref=bbb1 at=2026-11-01T09:00:00Z"), got[0])

    def test_never_moves_the_read_mark(self):
        self.box("lane-7", "out", self.rec("CLAIMED", "7", "2026-11-01T09:00:00Z", "bbb1"))
        self.out("local", KEY)
        self.assertFalse((self.dir / "lane-7" / "out.read").exists())
        self.assertEqual(self.out("local", KEY).count("ITEM"), 1)

    def test_a_stop_sorts_first(self):
        self.box("lane-7", "out", self.rec("CLAIMED", "7", "2026-11-01T08:00:00Z", "bbb1"))
        self.box("orchestrator", "in", self.rec("STOP", "epic-1", "2026-11-01T12:00:00Z", "ccc1", "owner"))
        self.assertIn("STOP", self.out("local", KEY).splitlines()[0])

    def test_a_due_watch_is_an_item_and_a_future_or_closed_one_is_not(self):
        led = {"schema": 1, "key": KEY, "watches": [
            {"id": "w1", "due": "2026-11-01T10:00:00Z", "owner": "orchestrator", "what": "recheck the cap", "closed": False},
            {"id": "w2", "due": "2026-11-09T10:00:00Z", "owner": "orchestrator", "what": "later", "closed": False},
            {"id": "w3", "due": "2026-11-01T10:00:00Z", "owner": "orchestrator", "what": "done", "closed": True}]}
        (self.root / "state" / "swarm").mkdir(parents=True)
        (self.root / "state" / "swarm" / f"{KEY}.json").write_text(json.dumps(led), encoding="utf-8")
        got = self.out("local", KEY, "--now", "2026-11-02T00:00:00Z")
        self.assertIn("ITEM watch orchestrator src=ledger ref=w1", got)
        self.assertNotIn("w2", got)
        self.assertNotIn("w3", got)


class Since(Case):
    def test_none_then_moves_forward_only(self):
        self.assertEqual(self.out("since", KEY).strip(), "none")
        self.assertIn("swept through 2026-11-02T10:00:00Z", self.out("seen", KEY, "--at", "2026-11-02T10:00:00Z"))
        self.assertEqual(self.out("since", KEY).strip(), "2026-11-02T10:00:00Z")
        self.assertIn("no change", self.out("seen", KEY, "--at", "2026-11-01T10:00:00Z"))
        self.assertEqual(self.out("since", KEY).strip(), "2026-11-02T10:00:00Z")

    def test_a_bad_time_is_a_usage_error(self):
        self.assertEqual(self.run_cli("seen", KEY, "--at", "yesterday").returncode, 2)


class Normalize(Case):
    def raw(self, *items: dict) -> str:
        return json.dumps(list(items))

    def test_a_human_scope_comment_is_a_delta_and_a_bot_comment_is_dropped(self):
        """DW-2."""
        got = self.out("normalize", stdin=self.raw(
            {"kind": "delta", "scope": "674", "src": "comment", "ref": "c1", "at": "2026-11-01T10:00:00Z",
             "gist": "Please also cover the retry path.", "author": "owner", "author_type": "User"},
            {"kind": "delta", "scope": "674", "src": "comment", "ref": "c2", "at": "2026-11-01T10:05:00Z",
             "gist": "Please also cover everything.", "author": "ci-helper[bot]", "author_type": "Bot"}))
        self.assertEqual(got.count("ITEM"), 1, got)
        self.assertIn("ITEM delta 674 src=comment ref=c1", got)
        self.assertNotIn("c2", got)

    def test_all_bots_is_no_op(self):
        got = self.out("normalize", stdin=self.raw(
            {"kind": "comment", "scope": "1", "gist": "x", "author": "someone", "author_type": "Bot"}))
        self.assertEqual(got.strip(), "NO-OP")

    def test_the_configured_review_bot_and_github_bots_are_dropped(self):
        self.config(github={"review_bot": "reviewer-app", "bots": ["lint-app"]})
        got = self.out("normalize", stdin=self.raw(
            {"kind": "comment", "scope": "1", "ref": "a", "gist": "x", "author": "Reviewer-App"},
            {"kind": "comment", "scope": "1", "ref": "b", "gist": "y", "author": "lint-app"},
            {"kind": "comment", "scope": "1", "ref": "c", "gist": "z", "author": "person"}))
        self.assertEqual(got.count("ITEM"), 1)
        self.assertIn("ref=c", got)

    def test_the_swarms_own_digest_comment_is_dropped(self):
        got = self.out("normalize", stdin=self.raw(
            {"kind": "comment", "scope": "1", "gist": "<!-- swarm-digest --> 🟢 digest", "author": "owner"}))
        self.assertEqual(got.strip(), "NO-OP")

    def test_a_question_is_a_decision_item_and_a_long_gist_is_one_short_line(self):
        long = "why " * 100 + "\nsecond line"
        got = self.out("normalize", stdin=self.raw(
            {"kind": "decision", "scope": "674", "ref": "q", "at": "t", "gist": long, "author": "owner"}))
        self.assertEqual(len(got.strip().splitlines()), 1)
        self.assertTrue(got.startswith("ITEM decision 674"))
        self.assertLessEqual(len(got.split('"', 1)[1].rsplit('"', 1)[0]), 120)

    def test_an_unknown_kind_is_dropped_with_a_note(self):
        p = self.run_cli("normalize", stdin=self.raw({"kind": "gossip", "gist": "x"}))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "NO-OP")
        self.assertIn("dropped", p.stderr)

    def test_not_json_is_a_usage_error(self):
        self.assertEqual(self.run_cli("normalize", stdin="{").returncode, 2)

    def test_stop_lines_come_first(self):
        got = self.out("normalize", stdin=self.raw(
            {"kind": "comment", "scope": "1", "at": "2026-11-01T01:00:00Z", "gist": "hello", "author": "p"},
            {"kind": "line", "scope": "1", "at": "2026-11-01T09:00:00Z", "gist": "STOP epic-1 v=1 id=zzz1 from=owner"}))
        self.assertIn("STOP", got.splitlines()[0])


class Chat(Case):
    def test_slack_false_stops_with_one_line(self):
        """DW-3."""
        self.config(systems={"slack": False})
        got = self.out("chat", KEY).strip().splitlines()
        self.assertEqual(got, ["swarm-inbox: chat tier not applicable here — slack is false"])

    def test_no_store_at_all_is_the_same_one_line(self):
        self.assertEqual(len(self.out("chat", KEY).strip().splitlines()), 1)

    def test_slack_true_lists_the_open_drafts_until_they_close(self):
        """DW-5: the drafts the readback has to classify."""
        self.config(systems={"slack": True})
        self.assertEqual(self.out("chat", KEY).strip(), "NO-DRAFTS")
        self.out("draft-add", "--key", KEY, "--channel", "C1", "--thread", "1.2", "--draft-id", "D1", "--gist", "digest 4h")
        self.out("draft-add", "--key", KEY, "--channel", "C1", "--thread", "1.3", "--draft-id", "D2", "--gist", "decision ask")
        got = self.out("chat", KEY)
        self.assertEqual(got.count("DRAFT-OPEN"), 2)
        self.assertIn("draft=D1 channel=C1 thread=1.2", got)
        self.assertIn("closed: sent-edited", self.out("draft-close", "--key", KEY, "D1", "--outcome", "sent-edited"))
        self.assertEqual(self.out("chat", KEY).count("DRAFT-OPEN"), 1)
        self.assertIn("no change", self.out("draft-close", "--key", KEY, "D1", "--outcome", "sent"))

    def test_draft_outcomes_are_items(self):
        got = self.out("normalize", stdin=json.dumps([
            {"kind": "draft-sent", "scope": "674", "src": "chat", "ref": "D1", "gist": "digest sent"},
            {"kind": "draft-edited", "scope": "674", "src": "chat", "ref": "D2", "gist": "sent with edits"},
            {"kind": "draft-pending", "scope": "674", "src": "chat", "ref": "D3", "gist": "still pending"}]))
        for k in ("draft-sent", "draft-edited", "draft-pending"):
            self.assertIn(f"ITEM {k} 674", got)


if __name__ == "__main__":
    unittest.main()
