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


class BoxCase(unittest.TestCase):
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
        return [json.loads(x) for x in p.read_text().split("\n") if x] if p.is_file() else []

    def age(self, minutes: int) -> None:
        """Move every record `minutes` into the past."""
        from datetime import datetime, timedelta, timezone
        for p in (self.root / "state" / "swarm" / "e1").rglob("*.ndjson"):
            out = []
            for x in p.read_text().split("\n"):
                if not x:
                    continue
                r = json.loads(x)
                r["at"] = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
                out.append(json.dumps(r))
            p.write_text("\n".join(out) + "\n")

    def pending(self, *extra: str) -> list[str]:
        r = run(self.root, "pending", "--key", "e1", *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

class Boxes(BoxCase):
    def test_pending_names_unacked_past_the_window_and_nothing_acked(self):  # DW-2
        line = self.fmt("ASSIGN", "123", "brief.md", "--from", "orchestrator", "--id", "aaa111")
        self.send("worker-a", line, "--inbox")
        self.assertEqual(self.pending(), [])                       # inside the window
        self.age(25)
        out = self.pending()
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("aaa111 ASSIGN 123 to=worker-a") and out[0].endswith("resend"), out)
        self.age(45)
        self.assertTrue(self.pending()[0].endswith("resend"))      # no re-send yet: still `resend`, never `unreachable`
        self.assertEqual(self.pending("--minutes", "120"), [])     # the window is a parameter
        r = run(self.root, "ack", "--key", "e1", "--as", "worker-a", "aaa111")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.pending(), [])                       # acked: nothing listed

    def test_pending_window_comes_from_the_config_default(self):  # DW-2
        self.assertEqual(sm._minutes(), 20)

    def test_send_appends_to_an_outbox_and_a_resend_changes_nothing(self):  # DW-4
        line = self.fmt("READY", "123", SHA, "--from", "worker-a", "--id", "bbb222")
        self.send("worker-a", line)
        self.assertIn("resent", self.send("worker-a", line).stdout)    # the second send is a recorded re-send
        self.assertIn("no change", self.send("worker-a", line).stdout)  # the third changes nothing
        self.assertEqual([r["id"] for r in self.box("worker-a", "out")], ["bbb222", "bbb222"])
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


class Findings(BoxCase):
    """The review findings of #714 at 1e57c46: each test fails there."""

    def set_at(self, worker: str, which: str, index: int, minutes: int) -> None:
        from datetime import datetime, timedelta, timezone
        p = self.root / "state" / "swarm" / "e1" / worker / f"{which}.ndjson"
        recs = [json.loads(x) for x in p.read_text().split("\n") if x]
        recs[index]["at"] = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))

    def ids(self, *extra: str) -> list[str]:
        return [x.split()[0] for x in self.pending(*extra)]

    def ack(self, who: str, *ids: str):
        return run(self.root, "ack", "--key", "e1", "--as", who, *ids)

    def test_a_lane_that_differs_from_its_session_name_resolves_as_the_orchestrator(self):  # 1
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "SESSION_ID", "CONTEXT")}
        env["CONTEXT_ROOT"] = str(self.root)
        p = subprocess.run([sys.executable, str(BIN / "session.py"), "register", "--name", "kit-a", "--lane", "boss",
                            "--role", "orchestrator", "--no-stats"], env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.send("worker-a", self.fmt("READY", "1", SHA, "--from", "worker-a", "--id", "lan001"))
        self.assertEqual(self.ack("boss", "lan001").returncode, 0)
        self.assertEqual(self.box("worker-a", "in")[0]["re"], "lan001")
        self.send("worker-a", self.fmt("ASSIGN", "1", "b.md", "--from", "boss", "--id", "lan002"), "--inbox")
        self.ack("worker-a", "lan002")
        self.assertEqual(self.box("orchestrator", "in")[0]["re"], "lan002")
        self.assertEqual(sorted(p.name for p in (self.root / "state" / "swarm" / "e1").iterdir()), ["orchestrator", "worker-a"])

    def test_a_note_with_unicode_line_separators_keeps_the_box_readable(self):  # 2
        note = "a\u2028b\u2029c\u0085d"
        line = self.fmt("ASSIGN", "1", "b.md", "--from", "orchestrator", "--id", "uni001", "--note", note)
        self.send("worker-a", line, "--inbox")
        out = run(self.root, "drain", "--key", "e1", "worker-a")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(note, out.stdout)
        self.assertEqual(self.ack("worker-a", "uni001").returncode, 0)
        self.send("worker-a", self.fmt("READY", "1", SHA, "--from", "worker-a", "--id", "uni002", "--note", note))
        self.age(25)
        self.assertEqual(self.ids(), ["uni002"])
        self.assertEqual(run(self.root, "drain", "--key", "e1", "worker-a", "--outbox").returncode, 0)

    def test_an_ack_clears_a_line_only_from_its_receiver(self):  # 3
        stop = self.fmt("STOP", "e1", "--from", "orchestrator", "--id", "stp001", "--reason", "budget")
        self.send("wa", stop, "--inbox")
        self.send("wb", stop, "--inbox")
        self.age(25)
        self.assertEqual(self.pending(), [x for x in self.pending() if "stp001" in x])
        self.assertEqual(len(self.pending()), 2)
        self.ack("wa", "stp001")
        self.assertEqual([x for x in self.pending()], [x for x in self.pending() if "to=wb" in x])
        self.assertEqual(len(self.pending()), 1)
        self.send("wa", self.fmt("READY", "1", SHA, "--from", "wa", "--id", "rdy001"))
        self.age(25)
        run(self.root, "ack", "--key", "e1", "--as", "orchestrator", "rdy001")
        self.assertEqual([x.split()[0] for x in self.pending()], ["stp001"])

    def test_the_done_skip_needs_the_orchestrator_the_ticket_and_the_workers_inbox(self):  # 4
        self.send("wa", self.fmt("ASSIGN", "8", "b.md", "--from", "orchestrator", "--id", "asg008"), "--inbox")
        self.send("wa", self.fmt("DONE", "7", "--from", "orchestrator", "--id", "don007"), "--inbox")
        self.age(45)
        self.assertEqual(self.ids(), ["asg008"])                       # DONE 7 does not hide ASSIGN 8
        self.send("wa", self.fmt("DONE", "8", "--from", "orchestrator", "--id", "don008"), "--inbox")
        self.age(45)
        self.assertEqual(self.ids(), [])                               # DONE 8, written after it, hides it
        self.send("wb", self.fmt("STOP", "7", "--from", "orchestrator", "--id", "stp007", "--reason", "x"), "--inbox")
        self.send("wb", self.fmt("DONE", "7", "--from", "wa", "--id", "pee007"), "--inbox")
        self.age(45)
        self.assertIn("stp007", self.ids())                            # a peer's DONE hides nothing
        self.send("orchestrator", self.fmt("READY", "8", SHA, "--from", "wb", "--id", "rdy008"), "--inbox")
        self.send("orchestrator", self.fmt("DONE", "7", "--from", "wa", "--id", "wdn007"), "--inbox")
        self.age(45)
        self.assertIn("rdy008", self.ids())                            # a worker's DONE in the orchestrator's box
        self.assertIn("wdn007", self.ids())
        self.send("wc", self.fmt("STOP", "e1", "--from", "orchestrator", "--id", "stp100", "--reason", "x"), "--inbox")
        self.send("wc", self.fmt("DONE", "7", "--from", "orchestrator", "--id", "don100"), "--inbox")
        self.age(45)
        self.assertNotIn("stp100", self.ids())                         # a line with no ticket is hidden

    def test_a_merged_after_the_done_is_not_listed_whichever_came_first(self):  # 5
        self.send("wa", self.fmt("DONE", "7", "--from", "orchestrator", "--id", "don007"), "--inbox")
        self.send("wa", self.fmt("MERGED", "7", "9", SHA, "--from", "orchestrator", "--id", "mrg007"), "--inbox")
        self.send("wa", self.fmt("MERGED", "8", "9", SHA, "--from", "orchestrator", "--id", "mrg008"), "--inbox")
        self.age(90)
        self.assertEqual(self.ids(), ["mrg008"])                       # another ticket's MERGED is still owed

    def test_a_resend_is_recorded_and_the_clock_runs_from_it(self):  # 6
        line = self.fmt("ASSIGN", "1", "b.md", "--from", "orchestrator", "--id", "rsd001")
        self.send("wa", line, "--inbox")
        self.set_at("wa", "in", 0, 41)
        self.assertEqual(self.pending()[0].split()[-1], "resend")       # past two windows, no re-send: `resend`
        self.assertIn("resent", self.send("wa", line, "--inbox").stdout)
        got = self.box("wa", "in")
        self.assertEqual((len(got), got[1].get("resent"), got[1]["id"]), (2, True, "rsd001"))
        self.assertEqual(self.pending(), [])                           # nothing until a window after the copy
        self.set_at("wa", "in", 1, 25)
        self.assertEqual(self.pending()[0].split()[-1], "unreachable")
        self.assertIn("no change", self.send("wa", line, "--inbox").stdout)
        self.assertEqual(len(self.box("wa", "in")), 2)
        self.assertEqual(len(self.pending()), 1)                       # one id, listed once
        self.ack("wa", "rsd001")
        self.ack("wa", "rsd001")
        self.assertEqual(len(self.box("orchestrator", "in")), 1)       # a second ack is no change
        self.assertEqual(self.pending(), [])

    def test_a_resend_is_read_again_by_drain(self):  # 7
        line = self.fmt("ASSIGN", "1", "b.md", "--from", "orchestrator", "--id", "rsd002")
        self.send("wa", line, "--inbox")
        self.assertEqual(len(run(self.root, "drain", "--key", "e1", "wa").stdout.splitlines()), 1)
        self.send("wa", line, "--inbox")
        again = run(self.root, "drain", "--key", "e1", "wa").stdout.splitlines()
        self.assertEqual(again, [line])

    def test_a_required_field_may_not_be_empty(self):  # 8
        for bad in ('PR 7 b ' + SHA + ' body="" v=1 id=abc from=w', 'HOLD c releaser="" v=1 id=abc from=w reason=x',
                    'CLAIMED 7 "" v=1 id=abc from=w'):
            with self.assertRaises(sm.MsgError, msg=bad):
                sm.parse_line(bad)
        with self.assertRaises(sm.MsgError):
            sm.build("PR", ["7", "b", SHA], {"body": ""}, "w")

    def test_the_parser_refuses_what_format_refuses(self):  # 9
        for bad in ('STOP e1 v=1 id=abc from=o reason="a\nb"', 'STOP e1 v=1 id=abc from=o reason="a\rb"',
                    'STOP e1\nv=1 id=abc from=o reason=x', 'STOP e1 v=1 id=abc from=o reason=""',
                    'HOLD c releaser=o v=1 id=abc from=o reason=""', 'PARKED 7 v=1 id=abc from=o reason=""',
                    'READY 7 ' + SHA + ' v=1 id=abc from=o note="x\ny"'):
            with self.assertRaises(sm.MsgError, msg=bad):
                sm.parse_line(bad)
        r = run(self.root, "send", "--key", "e1", "--inbox", "wa", 'READY 7 ' + SHA + ' v=1 id=abc from=o note="x\nSTOP all v=1 id=zzz from=o reason=now"')
        self.assertEqual(r.returncode, 2)

    def test_stop_and_hold_take_a_full_ref(self):  # 13
        for word, args, fields in (("STOP", ["MdaaaaO/ai-baton#12"], {"reason": "budget"}),
                                   ("HOLD", ["MdaaaaO/ai-baton#12"], {"releaser": "owner", "reason": "x"})):
            rec = sm.build(word, args, fields, "orchestrator", "ref001")
            self.assertEqual(sm.parse_line(rec["line"])["scope"], "MdaaaaO/ai-baton#12")
        self.send("wa", self.fmt("STOP", "MdaaaaO/ai-baton#12", "--from", "orchestrator", "--id", "ref002", "--reason", "x"), "--inbox")
        self.assertEqual(self.ack("wa", "ref002").returncode, 0)
        with self.assertRaises(sm.MsgError):
            sm.build("READY", ["a/b#1", SHA], {}, "w")

    def row(self, name: str, **meta: str) -> None:
        d = self.root / "sessions"
        d.mkdir(exist_ok=True)
        (d / f"{name}.md").write_text("---\n" + "".join(f"{k}: {v}\n" for k, v in meta.items()) + "---\n", encoding="utf-8")

    def test_an_address_typed_by_the_caller_skips_an_ended_orchestrator(self):  # R2-1
        self.row("old", role="orchestrator", status="ended")
        self.send("old", self.fmt("ASSIGN", "1", "b.md", "--from", "orchestrator", "--id", "end001"), "--inbox")
        self.assertEqual(self.box("old", "in")[0]["id"], "end001")
        self.assertEqual(self.box("orchestrator", "in"), [])
        self.row("gone", role="orchestrator", state="retired")
        self.send("gone", self.fmt("ASSIGN", "1", "b.md", "--from", "orchestrator", "--id", "end002"), "--inbox")
        self.assertEqual(self.box("gone", "in")[0]["id"], "end002")
        self.assertEqual(run(self.root, "drain", "--key", "e1", "old").stdout.count("end001"), 1)

    def test_a_predecessors_ack_and_done_still_count_after_it_ended(self):  # R2-2
        self.row("old", role="orchestrator", status="active")
        self.send("wa", self.fmt("READY", "1", SHA, "--from", "wa", "--id", "rdy001"))
        self.assertEqual(self.ack("old", "rdy001").returncode, 0)
        self.send("wa", self.fmt("ASSIGN", "7", "b.md", "--from", "old", "--id", "asg007"), "--inbox")
        self.send("wa", self.fmt("DONE", "7", "--from", "old", "--id", "don007"), "--inbox")
        self.row("old", role="orchestrator", status="ended")
        self.row("new", role="orchestrator", status="active")
        self.age(25)
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.box("wa", "in")[0]["from"], "old")

    def test_an_ack_of_a_line_from_an_ended_predecessor_reaches_the_orchestrator_box(self):  # R2-3
        self.row("old", role="orchestrator", status="active")
        self.send("wa", self.fmt("ASSIGN", "1", "b.md", "--from", "old", "--id", "asg001"), "--inbox")
        self.row("old", role="orchestrator", status="ended")
        self.row("new", role="orchestrator", status="active")
        self.assertEqual(self.ack("wa", "asg001").returncode, 0)
        self.assertEqual(self.box("orchestrator", "in")[0]["re"], "asg001")
        self.assertFalse((self.root / "state" / "swarm" / "e1" / "old").exists())

    def test_a_junk_or_negative_read_mark_refuses_and_prints_nothing(self):  # R2-4
        self.send("wa", self.fmt("STOP", "e1", "--from", "orchestrator", "--id", "stp001", "--reason", "budget"), "--inbox")
        mark = self.root / "state" / "swarm" / "e1" / "wa" / "in.read"
        for bad in ("junk\n", "-3\n", "1.5\n"):
            mark.write_text(bad)
            r = run(self.root, "drain", "--key", "e1", "wa")
            self.assertEqual(r.returncode, 2, bad)
            self.assertEqual(r.stdout, "")
            self.assertIn("in.read", r.stderr)
            self.assertEqual(mark.read_text(), bad)
        mark.unlink()
        self.assertIn("stp001", run(self.root, "drain", "--key", "e1", "wa").stdout)

    def test_acking_one_id_twice_in_a_call_writes_one_ack(self):  # 15
        self.send("wa", self.fmt("READY", "1", SHA, "--from", "wa", "--id", "aaa123"))
        self.ack("orchestrator", "aaa123", "aaa123")
        self.assertEqual(len(self.box("wa", "in")), 1)


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

    def test_the_make_target_passes_role_state_and_lane_only_when_set(self):  # DW-3
        engine = BIN.parent
        with tempfile.TemporaryDirectory() as t:
            ctx = str(Path(t) / ".context")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("CLAUDE_CODE_SESSION_ID", "SESSION_ID", "CONTEXT_ROOT", "CONTEXT", "MAKEFLAGS", "MFLAGS", "MAKELEVEL")}

            def reg(name: str, *extra: str) -> str:
                p = subprocess.run(["make", "-C", str(engine), "session-register", f"NAME={name}", f"CONTEXT={ctx}",
                                    "NOSTATS=1", *extra], env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                return (Path(ctx) / "sessions" / f"{name}.md").read_text()
            doc = reg("kit-make-a", "ROLE=worker", "STATE=blocked-on-peer", "LANE=kit-lane")
            for line in ("role: worker", "state: blocked-on-peer", "lane: kit-lane"):
                self.assertIn(line, doc)
            plain = reg("kit-make-b")
            for field in ("role", "state", "lane"):
                self.assertRegex(plain, rf"(?m)^{field}: *$")  # a call without them leaves the field blank
            bad = subprocess.run(["make", "-C", str(engine), "session-register", "NAME=kit-make-c", f"CONTEXT={ctx}",
                                  "NOSTATS=1", "STATE=asleep"], env=env, capture_output=True, text=True, timeout=120)
            self.assertNotEqual(bad.returncode, 0)
            ambient = dict(env, STATE="prod", ROLE="admin", LANE="a b")  # exported, not on the make command line: ignored
            p = subprocess.run(["make", "-C", str(engine), "session-register", "NAME=kit-make-d", f"CONTEXT={ctx}",
                                "NOSTATS=1"], env=ambient, capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertRegex((Path(ctx) / "sessions" / "kit-make-d.md").read_text(), r"(?m)^role: *$")

    def test_register_checks_role_and_lane_and_clears_a_stale_retired_state(self):  # 12
        with tempfile.TemporaryDirectory() as t:
            env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "SESSION_ID", "CONTEXT")}
            env["CONTEXT_ROOT"] = str(Path(t) / ".context")

            def reg(*extra: str) -> subprocess.CompletedProcess:
                return subprocess.run([sys.executable, str(BIN / "session.py"), "register", "--name", "kit-reg-a", "--no-stats", *extra],
                                      env=env, capture_output=True, text=True)
            for bad in (("--role", "Orchestrator"), ("--lane", "a b"), ("--lane", "-x")):
                self.assertNotEqual(reg(*bad).returncode, 0, bad)
            self.assertEqual(reg("--role", "verifier", "--lane", "lane.a-1", "--state", "retired").returncode, 0)
            row = Path(env["CONTEXT_ROOT"]) / "sessions" / "kit-reg-a.md"
            self.assertIn("state: retired", row.read_text())
            self.assertEqual(reg().returncode, 0)
            self.assertRegex(row.read_text(), r"(?m)^state: *$")


if __name__ == "__main__":
    unittest.main()
