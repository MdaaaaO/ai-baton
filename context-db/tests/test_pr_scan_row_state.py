"""`skills/pr-scan/row-state.py`: derives one `state` (+ `section`, `state_label`) per pr-scan
candidate row from the row's own `kind` / `mine` and the pr-review ledger's entries for that PR.
No `gh` call — the brief (`pr-scan/SKILL.md` § Answer) renders by `.section` straight off `queue.json`
instead of re-deriving the grouping itself.

A fixture candidate set (`queue.json` shape, one row per rendered section, plus one extra row showing
an auto-policy follow-up landing in "New — not started" rather than "Follow-up — manual") and a ledger
fixture drive the script directly — no `gh` stub needed, this is a pure function of its two input
files. Every fixture row has a shape `pr-scan.sh` produces: a head we already reviewed arrives as
`kind=done` (or `follow_up` once the author replied) with our review in `mine`, never as `kind=new`;
`test_pr_scan_auto_comment.py` § ReviewedHeadRows proves that end to end against a stub `gh`.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROW_STATE = Path(__file__).resolve().parents[2] / "skills" / "pr-scan" / "row-state.py"
REPO = "acme/widgets"

# one made-up 40-char head sha per row, for readability keyed by row number
HEADS = {
    601: "a" * 40, 602: "b" * 40, 603: "c" * 40, 604: "d" * 40,
    605: "e" * 40, 606: "f" * 40, 607: "1" * 40, 608: "2" * 40,
    609: "3" * 40, 610: "4" * 40, 611: "5" * 40,
}
OLD_HEAD_607 = "0" * 40  # the head our prior review on #607 was posted against
OLD_HEAD_608 = "9" * 40  # ditto for #608


def row(pr: int, kind: str = "new", eligible: bool = False, mine: dict | None = None) -> dict:
    return {"repo": REPO, "pr": pr, "head": HEADS[pr], "kind": kind,
            "auto_comment": {"eligible": eligible}, "mine": mine}


QUEUE = [
    row(601),                                   # no ledger entry at all
    row(602),                                   # held on this head
    row(603, kind="follow_up"),                 # author replied in a thread we opened
    row(604, kind="done", mine={"state": "CHANGES_REQUESTED", "review_id": 400004}),  # our REQUEST_CHANGES stands
    row(605, kind="done", mine={"state": "APPROVED", "review_id": 500005}),           # our APPROVE stands
    row(606, kind="done", mine={"state": "COMMENTED", "review_id": 600006}),          # a COMMENT the kit posted
    row(607, kind="re_review", eligible=False),  # review on an older head, manual policy
    row(608, kind="re_review", eligible=True),   # review on an older head, auto policy
    row(609, kind="done"),                       # reviews fetch degraded: the ledger's APPROVE stands in
    row(610, kind="follow_up", mine={"state": "CHANGES_REQUESTED", "review_id": 100010}),  # verdict + a reply
    row(611, kind="done", mine={"state": "COMMENTED", "review_id": 110011}),  # held, then reviewed on that head
]

LEDGER = [
    {"repo": REPO, "pr": 602, "head": HEADS[602], "status": "held", "ts": "2026-09-01T00:00:00Z"},
    {"repo": REPO, "pr": 604, "head": HEADS[604], "status": "reviewed", "event": "REQUEST_CHANGES",
     "review_id": 400004, "ts": "2026-09-01T00:00:00Z"},
    {"repo": REPO, "pr": 605, "head": HEADS[605], "status": "reviewed", "event": "APPROVE",
     "review_id": 500005, "ts": "2026-09-01T00:00:00Z"},
    {"repo": REPO, "pr": 606, "head": HEADS[606], "status": "reviewed", "event": "COMMENT",
     "review_id": 600006, "ts": "2026-09-01T00:00:00Z"},
    {"repo": REPO, "pr": 607, "head": OLD_HEAD_607, "status": "reviewed", "event": "COMMENT",
     "review_id": 700007, "ts": "2026-08-01T00:00:00Z"},
    {"repo": REPO, "pr": 608, "head": OLD_HEAD_608, "status": "reviewed", "event": "COMMENT",
     "review_id": 800008, "ts": "2026-08-01T00:00:00Z"},
    {"repo": REPO, "pr": 609, "head": HEADS[609], "status": "reviewed", "event": "APPROVE",
     "review_id": 900009, "ts": "2026-09-01T00:00:00Z"},
    {"repo": REPO, "pr": 610, "head": HEADS[610], "status": "reviewed", "event": "REQUEST_CHANGES",
     "review_id": 100010, "ts": "2026-09-01T00:00:00Z"},
    {"repo": REPO, "pr": 611, "head": HEADS[611], "status": "held", "ts": "2026-09-01T00:00:00Z"},
    {"repo": REPO, "pr": 611, "head": HEADS[611], "status": "auto_commented", "event": "COMMENT",
     "review_id": 110011, "ts": "2026-09-02T00:00:00Z"},
]

EXPECTED = {
    601: ("new", "New — not started"),
    602: ("needs_you", "Needs you"),
    603: ("needs_you", "Needs you"),
    604: ("watching", "Watching"),            # the ball is with the author until they push or reply
    605: ("watching", "Watching"),
    606: ("handled", "Handled this tick"),
    607: ("follow_up", "Follow-up — manual"),
    608: ("follow_up", "New — not started"),
    609: ("watching", "Watching"),
    610: ("needs_you", "Needs you"),          # a reply needs an answer whatever verdict we left
    611: ("handled", "Handled this tick"),    # the review resolved the hold
}


class RowStateSections(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prscan-rowstate-test."))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.queue_path = self.tmp / "queue.json"
        self.ledger_path = self.tmp / "ledger.jsonl"
        self.queue_path.write_text(json.dumps(QUEUE))
        self.ledger_path.write_text("".join(json.dumps(r) + "\n" for r in LEDGER))

    def run_row_state(self) -> list[dict]:
        r = subprocess.run(
            [sys.executable, str(ROW_STATE), "--queue", str(self.queue_path), "--ledger", str(self.ledger_path)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_each_row_lands_in_the_expected_section(self):
        out = self.run_row_state()
        by_pr = {r["pr"]: r for r in out}
        self.assertEqual(set(by_pr), set(EXPECTED))
        for pr, (state, section) in EXPECTED.items():
            got = by_pr[pr]
            self.assertEqual(got["state"], state, f"pr {pr}: {got}")
            self.assertEqual(got["section"], section, f"pr {pr}: {got}")

    def test_handled_row_links_the_review_id_in_its_label(self):
        out = self.run_row_state()
        row606 = next(r for r in out if r["pr"] == 606)
        self.assertIn("600006", row606["state_label"])

    def test_watching_label_names_the_verdict_that_stands(self):
        by_pr = {r["pr"]: r for r in self.run_row_state()}
        self.assertEqual(by_pr[604]["state_label"], "watching (you requested changes)")
        self.assertEqual(by_pr[605]["state_label"], "watching (you approved)")
        self.assertEqual(by_pr[610]["state_label"], "needs you (author replied)")

    def test_missing_ledger_file_leaves_only_what_the_rows_carry(self):
        missing = self.tmp / "no-such-ledger.jsonl"
        r = subprocess.run(
            [sys.executable, str(ROW_STATE), "--queue", str(self.queue_path), "--ledger", str(missing)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        by_pr = {row["pr"]: row for row in out}
        # #603 is still "needs_you" on an empty ledger — `kind=follow_up` alone drives that state
        self.assertEqual(by_pr[603]["section"], "Needs you")
        self.assertEqual(by_pr[601]["section"], "New — not started")
        self.assertEqual(by_pr[602]["section"], "New — not started")  # no ledger entry -> no hold to find
        self.assertEqual(by_pr[605]["section"], "Watching")           # the row's own review carries the APPROVE
        self.assertEqual(by_pr[609]["section"], "Handled this tick")  # no ledger event to stand in for it
        self.assertIn("600006", by_pr[606]["state_label"])            # review id off the row when the ledger has none


if __name__ == "__main__":
    unittest.main()
