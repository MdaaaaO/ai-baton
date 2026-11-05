"""swarm_msg.py: every grammar line of docs/swarm.md round-trips, free text is refused, the inbox/outbox files, the ack rule,
and the registry's role and state in SESSION_INDEX.md. Every case runs on a throw-away CONTEXT_ROOT. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import swarm_msg as sm  # noqa: E402

SHA = "a1b2c3d"
# word, positional args, fields: one of every line of the table
LINES = [
    ("ASSIGN", ["123", "brief.md"], {"lease": "client", "rebrief": "1", "note": "fix: a b, \"c\""}),
    ("ASSIGN", ["123", "brief.md"], {}),
    ("ACK", ["123"], {"re": "7f3a"}),
    ("CLAIMED", ["123", "feat/x"], {}),
    ("PR", ["123", "feat/x", SHA], {"body": "pr.md"}),
    ("READY", ["123", SHA], {}),
    ("MERGED", ["123", "45", SHA], {}),
    ("BLOCKED", ["123", "budget", "orchestrator"], {}),
    ("BLOCKED", ["123", "stop", "owner"], {"sha": SHA, "ran": "pushed; ran make ci"}),
    ("DONE", ["123"], {}),
    ("HOLD", ["client"], {"releaser": "owner", "reason": "owner is playing"}),
    ("RELEASE", ["client"], {}),
    ("RELEASE", ["client"], {"reason": "done"}),
    ("STOP", ["epic-1"], {"reason": "budget hard stop"}),
    ("PARKED", ["123"], {"reason": "waits on an answer"}),
    ("STANDBY", ["worker-a"], {"until": "2026-11-05T10:00:00Z"}),
    ("RETIRED", ["worker-a"], {}),
]
TEXTS = [
    ("NEEDS", "NEEDS tracker.repo ai-baton"),
    ("DECISION", "DECISION merge now? | yes | no"),
    ("Assumed:", "Assumed: took option one (revisit: review)"),
]


def run(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("WORKSPACE_")}
    env["CONTEXT_ROOT"] = str(root)
    return subprocess.run([sys.executable, str(BIN / "swarm_msg.py"), *args], env=env, input=stdin,
                          capture_output=True, text=True, cwd=BIN)


class Grammar(unittest.TestCase):
    def test_every_line_round_trips(self):  # DW-1
        for word, args, fields in LINES:
            rec = sm.build(word, args, fields, "worker-a", "7f3a")
            back = sm.parse_line(rec["line"])
            self.assertEqual(back["word"], word)
            self.assertEqual((back["v"], back["id"], back["from"]), ("1", "7f3a", "worker-a"))
            for k, v in fields.items():
                self.assertEqual(back[k], v, rec["line"])
            self.assertEqual(sm.build(word, args, fields, "worker-a", "7f3a")["line"], rec["line"])
            self.assertNotIn("\n", rec["line"])

    def test_text_lines_travel_as_records(self):  # DW-1
        for word, text in TEXTS:
            rec = sm.build(word, [text], {}, "worker-a", "7f3a")
            self.assertEqual(rec["line"], text)  # v, id and from are not in the text
            back = sm.parse_any(json.dumps(rec))
            self.assertEqual((back["word"], back["line"], back["id"], back["from"]), (word, text, "7f3a", "worker-a"))
            with self.assertRaises(sm.MsgError):
                sm.parse_line(text)

    def test_free_text_and_malformed_lines_are_refused(self):  # DW-1
        for bad in ("please look at 123", "", "ready 123 a1b2c3d v=1 id=7f3a from=w", "READY 123 v=1 id=7f3a from=w",
                    "READY 123 a1b2c3d id=7f3a from=w", "READY 123 a1b2c3d v=1 from=w", "READY 123 a1b2c3d v=1 id=7f3a",
                    "READY 123 a1b2c3d v=2 id=7f3a from=w", "READY 123 zz v=1 id=7f3a from=w",
                    "READY 123 a1b2c3d v=1 id=7f3a from=w color=red", "BLOCKED 1 sleepy owner v=1 id=7f3a from=w",
                    "PR 1 b a1b2c3d v=1 id=7f3a from=w", "HOLD c v=1 id=7f3a from=w reason=x",
                    "ACK 1 v=1 id=7f3a from=w", 'READY 1 a1b2c3d v=1 id=7f3a from=w note="open'):
            with self.assertRaises(sm.MsgError, msg=bad):
                sm.parse_line(bad)
        for word, args, fields in (("HELLO", ["x"], {}), ("READY", ["1"], {}), ("ASSIGN", ["1", "f"], {"rebrief": "2"}),
                                   ("PARKED", ["1"], {}), ("NEEDS", ["not a need"], {}), ("DECISION", ["one | two"], {}),
                                   ("Assumed:", ["no checkpoint"], {}), ("READY", ["1", SHA], {"note": "a\nb"})):
            with self.assertRaises(sm.MsgError, msg=word):
                sm.build(word, args, fields, "w")
        r = run(Path(tempfile.gettempdir()), "parse", "please look at 123")
        self.assertEqual(r.returncode, 2)
        self.assertIn("free text", r.stderr)

    def test_status_lines_carry_the_fields_the_ledger_reader_takes(self):  # DW-1
        rec = sm.parse_line(f"BLOCKED 123 stop owner v=1 id=7f3a from=w-a sha={SHA} ran=\"pushed x\" note=\"n\"")
        self.assertEqual(rec, {"word": "BLOCKED", "ticket": "123", "reason": "stop", "who": "owner", "v": "1", "id": "7f3a",
                               "from": "w-a", "sha": SHA, "ran": "pushed x", "note": "n"})
        rec = sm.parse_line(f"PR #123 feat/x {SHA} v=1 id=7f3a from=w body=pr.md")
        self.assertEqual((rec["ticket"], rec["branch"], rec["sha"], rec["body"]), ("123", "feat/x", SHA, "pr.md"))

    def test_the_ids_are_new_and_unique(self):
        taken = {sm.new_id(set()) for _ in range(50)}
        self.assertEqual(len(taken), 50)
        self.assertNotIn(sm.build("DONE", ["1"], {}, "w", taken=taken)["id"], taken)


class Boxes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def fmt(self, *args: str) -> str:
        r = run(self.root, "format", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def send(self, worker: str, line: str, *flags: str):
        r = run(self.root, "send", "--key", "e1", *flags, worker, line)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def box(self, worker: str, which: str) -> list[dict]:
        p = self.root / "state" / "swarm" / "e1" / worker / f"{which}.ndjson"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []

    def age(self, minutes: int) -> None:
        """Move every record `minutes` into the past."""
        from datetime import datetime, timedelta, timezone
        for p in (self.root / "state" / "swarm" / "e1").rglob("*.ndjson"):
            out = []
            for x in p.read_text().splitlines():
                r = json.loads(x)
                r["at"] = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
                out.append(json.dumps(r))
            p.write_text("\n".join(out) + "\n")

    def pending(self, *extra: str) -> list[str]:
        r = run(self.root, "pending", "--key", "e1", *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_pending_names_unacked_past_the_window_and_nothing_acked(self):  # DW-2
        line = self.fmt("ASSIGN", "123", "brief.md", "--from", "orchestrator", "--id", "aaa111")
        self.send("worker-a", line, "--inbox")
        self.assertEqual(self.pending(), [])                       # inside the window
        self.age(25)
        out = self.pending()
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("aaa111 ASSIGN 123 to=worker-a") and out[0].endswith("resend"), out)
        self.age(45)
        self.assertTrue(self.pending()[0].endswith("unreachable"))
        self.assertEqual(self.pending("--minutes", "120"), [])     # the window is a parameter
        r = run(self.root, "ack", "--key", "e1", "--as", "worker-a", "aaa111")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.pending(), [])                       # acked: nothing listed

    def test_pending_window_comes_from_the_config_default(self):  # DW-2
        self.assertEqual(sm._minutes(), 20)

    def test_send_appends_to_an_outbox_and_a_resend_changes_nothing(self):  # DW-4
        line = self.fmt("READY", "123", SHA, "--from", "worker-a", "--id", "bbb222")
        self.send("worker-a", line)
        out = self.send("worker-a", line)
        self.assertIn("no change", out.stdout)
        self.assertEqual([r["id"] for r in self.box("worker-a", "out")], ["bbb222"])
        self.assertEqual(self.box("worker-a", "out")[0]["to"], "orchestrator")
        r = run(self.root, "send", "--key", "e1", "worker-a", "chatty free text")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.box("worker-a", "in"), [])

    def test_drain_prints_unread_lines_and_marks_them_read(self):  # DW-4
        self.send("worker-a", self.fmt("ASSIGN", "1", "b.md", "--from", "orchestrator", "--id", "ccc333"), "--inbox")
        r = run(self.root, "drain", "--key", "e1", "worker-a")
        self.assertEqual(r.stdout.splitlines(), ["ASSIGN 1 b.md v=1 id=ccc333 from=orchestrator"])
        self.assertEqual(run(self.root, "drain", "--key", "e1", "worker-a").stdout, "")
        base = self.root / "state" / "swarm" / "e1" / "worker-a"
        self.assertEqual((base / "in.read").read_text().strip(), "1")  # the mark lives beside the file
        self.assertEqual(len((base / "in.ndjson").read_text().splitlines()), 1)
        self.send("worker-a", self.fmt("STOP", "1", "--from", "orchestrator", "--reason", "owner"), "--inbox")
        out = run(self.root, "drain", "--key", "e1", "worker-a", "--json").stdout.splitlines()
        self.assertEqual([json.loads(x)["word"] for x in out], ["STOP"])
        # a text line is drained with its id and sender beside it
        self.send("worker-a", self.fmt("NEEDS", "NEEDS tracker.repo x", "--from", "worker-a", "--id", "ddd444"))
        out = run(self.root, "drain", "--key", "e1", "worker-a", "--outbox").stdout
        self.assertIn("NEEDS tracker.repo x\t[v=1 id=ddd444 from=worker-a]", out)

    def test_ack_lands_in_the_senders_inbox(self):  # DW-5
        self.send("worker-a", self.fmt("READY", "1", SHA, "--from", "worker-a", "--id", "eee555"))
        r = run(self.root, "ack", "--key", "e1", "--as", "orchestrator", "eee555")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = self.box("worker-a", "in")
        self.assertEqual([(x["word"], x["re"], x["from"]) for x in got], [("ACK", "eee555", "orchestrator")])
        self.assertEqual(sm.parse_line(got[0]["line"])["scope"], "1")
        # the orchestrator's inbox, for a worker's ack of an orchestrator line
        self.send("worker-a", self.fmt("ASSIGN", "1", "b.md", "--from", "orchestrator", "--id", "fff666"), "--inbox")
        run(self.root, "ack", "--key", "e1", "--as", "worker-a", "fff666")
        inbox = self.root / "state" / "swarm" / "e1" / "orchestrator" / "in.ndjson"
        self.assertEqual(json.loads(inbox.read_text())["re"], "fff666")
        self.assertEqual(run(self.root, "ack", "--key", "e1", "--as", "worker-a", "fff666").stdout.split(":")[0], "no change")
        self.assertEqual(len(inbox.read_text().splitlines()), 1)
        self.assertEqual(run(self.root, "ack", "--key", "e1", "--as", "worker-a", "nope00").returncode, 1)
        self.assertEqual(run(self.root, "ack", "--key", "e1", "--as", "orchestrator", "fff666").returncode, 1)  # not sent to it

    def test_rebrief_is_answered_by_ack_alone(self):  # DW-5
        line = self.fmt("ASSIGN", "1", "b.md", "--from", "orchestrator", "--rebrief", "1", "--note", "fix: one two")
        self.assertIn("rebrief=1", line)
        self.assertEqual(sm.parse_line(line)["note"], "fix: one two")
        self.send("worker-a", line, "--inbox")
        mid = sm.parse_line(line)["id"]
        run(self.root, "ack", "--key", "e1", "--as", "worker-a", mid)
        self.age(60)
        self.assertEqual(self.pending(), [])

    def test_pending_skips_a_line_to_a_background_worker_whose_run_ended(self):  # DW-5
        self.send("worker-a", self.fmt("MERGED", "1", "9", SHA, "--from", "orchestrator", "--id", "ggg777"), "--inbox")
        self.send("worker-a", self.fmt("DONE", "1", "--from", "orchestrator", "--id", "hhh888"), "--inbox")
        self.age(90)
        self.assertEqual(self.pending(), [])
        self.send("worker-a", self.fmt("ASSIGN", "1", "b.md", "--from", "orchestrator", "--rebrief", "1", "--id", "iii999"), "--inbox")
        self.age(90)
        self.assertEqual([x.split()[0] for x in self.pending()], ["iii999"])  # a line after the DONE is owed again

    def test_the_registry_role_names_the_orchestrator_dir(self):  # DW-5
        (self.root / "sessions").mkdir()
        (self.root / "sessions" / "kit-swarm-2.md").write_text("---\nsession: kit-swarm-2\nrole: orchestrator\n---\n\nbody\n")
        self.send("worker-a", self.fmt("READY", "1", SHA, "--from", "worker-a", "--id", "jjj000"))
        run(self.root, "ack", "--key", "e1", "--as", "kit-swarm-2", "jjj000")
        self.assertEqual(self.box("worker-a", "in")[0]["re"], "jjj000")
        self.send("worker-a", self.fmt("ASSIGN", "1", "b.md", "--from", "kit-swarm-2", "--id", "kkk111"), "--inbox")
        run(self.root, "ack", "--key", "e1", "--as", "worker-a", "kkk111")
        self.assertEqual(self.box("orchestrator", "in")[0]["re"], "kkk111")


class Registry(unittest.TestCase):
    def test_session_index_shows_role_and_state(self):  # DW-3
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / ".context"
            env = {k: v for k, v in os.environ.items() if k not in ("WORKSPACE_TZ", "CLAUDE_CODE_SESSION_ID")}
            env["CONTEXT_ROOT"] = str(root)

            def sess(*a):
                return subprocess.run([sys.executable, str(BIN / "session.py"), *a, "--no-stats"], env=env, cwd=BIN,
                                      capture_output=True, text=True)
            r = sess("register", "--name", "kit-swarm-2", "--role", "orchestrator", "--state", "running")
            self.assertEqual(r.returncode, 0, r.stderr)
            idx = (root / "SESSION_INDEX.md").read_text()
            self.assertIn("| Role | State |", idx)
            self.assertIn("| orchestrator | running |", idx)
            self.assertEqual(sess("touch", "--name", "kit-swarm-2").returncode, 0)
            sess("register", "--name", "kit-swarm-2", "--state", "standby")
            doc = (root / "sessions" / "kit-swarm-2.md").read_text()
            self.assertIn("role: orchestrator", doc)  # a re-register without --role keeps it
            self.assertIn("state: standby", doc)
            r = sess("register", "--name", "kit-swarm-2", "--state", "asleep")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--state must be one of", r.stderr)


if __name__ == "__main__":
    unittest.main()
