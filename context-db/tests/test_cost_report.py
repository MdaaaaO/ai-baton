"""cost_report.py: local-day PR/ticket dates, ingest against a warehouse that dropped a column, and
torn-write-free JSON output. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
import unittest
import unittest.mock
from datetime import timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(KIT / "skills" / "cost-report"))

import cost_report  # noqa: E402


def ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


class LocalDayForWorkUnits(unittest.TestCase):
    """`work-prs`/`work-tickets` used to slice GitHub's UTC `createdAt`/`closedAt` naively (the same day
    boundary as `ingest`'s date-only rows), while `collect-private` already buckets transcript spend by the
    owner's calendar day. A PR merged or ticket closed late in the evening then landed on a different day
    than the spend it corresponds to. `_pr_row`/`_ticket_row` must use the same zone-aware day both use."""

    def setUp(self):
        # a fixed UTC offset, not a real zone name, so the result never depends on DST or on which
        # real place the test happens to name (`_local_day` only needs something `astimezone()` accepts)
        self.zone = timezone(timedelta(hours=-10))

    def test_pr_row_buckets_by_owner_local_day_not_utc(self):
        # 2026-09-28T09:59:30Z is 2026-09-27 23:59:30 in Honolulu — one calendar day earlier, local
        row = cost_report._pr_row("acme/repo", 42, "2026-09-20T12:00:00Z", "2026-09-28T09:59:30Z", "merged", True, self.zone)
        self.assertEqual(row, ["acme/repo", 42, "2026-09-20", "2026-09-27", "2026-09-27", "merged"])
        self.assertNotEqual(row[4], "2026-09-28")  # the UTC-slice day the old naive `_day()` would have produced

    def test_pr_row_never_merged_sentinel(self):
        row = cost_report._pr_row("acme/repo", 7, "2026-09-20T12:00:00Z", "0001-01-01T00:00:00Z", "open", False, self.zone)
        self.assertEqual(row[3], "")  # merged
        self.assertEqual(row[4], "")  # closed — gh's "never" sentinel, not a bogus year-0001 day

    def test_ticket_row_buckets_by_owner_local_day_not_utc(self):
        row = cost_report._ticket_row("acme/repo", 9, "2026-09-20T12:00:00Z", "2026-09-28T09:59:30Z", ["bug", "prio:2"], self.zone)
        self.assertEqual(row, ["acme/repo#9", "2026-09-20", "2026-09-27", "bug,prio:2", ""])

    def test_local_day_and_naive_day_disagree_near_midnight(self):
        """The exact scenario the bug produced: the same instant buckets to two different calendar days
        depending on which of the file's two day functions handles it."""
        ts = "2026-09-28T09:59:30Z"
        self.assertEqual(cost_report._day(ts), "2026-09-28")           # old behaviour: naive UTC slice
        self.assertEqual(cost_report._local_day(ts, self.zone), "2026-09-27")  # correct: owner's local day
        self.assertNotEqual(cost_report._day(ts), cost_report._local_day(ts, self.zone))


class IngestRobustness(unittest.TestCase):
    """`ingest` normalises rows from a warehouse whose query the operator wrote, not the kit's — a row
    missing the date (or week) column must be skipped and counted, never a KeyError that discards every
    other row already parsed."""

    def _run(self, rows: list, control_rows: list | None = None) -> tuple[int, list, list | None, str]:
        """Runs cmd_ingest in its own throw-away directory and reads both output files back out
        BEFORE the directory is removed, returning parsed JSON (not paths into a directory that no
        longer exists once this helper returns)."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
            out, control_out = d / "daily.json", d / "control.json"
            args = {"rows": str(d / "rows.json"), "out": str(out), "control_out": str(control_out), "control_rows": None}
            if control_rows is not None:
                (d / "control_rows.json").write_text(json.dumps(control_rows), encoding="utf-8")
                args["control_rows"] = str(d / "control_rows.json")
            buf = []
            with unittest.mock.patch("builtins.print", lambda *a, **k: buf.append(" ".join(str(x) for x in a))):
                rc = cost_report.cmd_ingest(ns(**args))
            daily = json.loads(out.read_text(encoding="utf-8"))
            ctl = json.loads(control_out.read_text(encoding="utf-8")) if control_rows is not None else None
            return rc, daily, ctl, "\n".join(buf)

    def test_row_missing_date_column_is_skipped_not_a_keyerror(self):
        rows = [
            {"D": "2026-09-01", "usd": 1, "inp": 10, "outp": 5},  # fine (case-insensitive key)
            {"model": "sonnet", "usd": 2},                        # no date column at all — must not KeyError
            {"d": "2026-09-02", "usd": 3, "cwrite": 1},
        ]
        rc, daily, _ctl, printed = self._run(rows)
        self.assertEqual(rc, 0)
        self.assertEqual([r["d"] for r in daily], ["2026-09-01", "2026-09-02"])
        self.assertIn("1 row(s) skipped", printed)

    def test_control_row_missing_week_column_is_skipped_not_a_keyerror(self):
        rows = [{"d": "2026-09-01", "usd": 1}]
        control_rows = [{"wk": "2026-W39", "usd": 10, "outp": 100}, {"usd": 5, "outp": 50}]  # second has no "wk"
        rc, _daily, ctl, printed = self._run(rows, control_rows)
        self.assertEqual(rc, 0)
        self.assertEqual([c["wk"] for c in ctl], ["2026-W39"])
        self.assertIn("1 row(s) skipped", printed)

    def test_all_rows_valid_reports_no_skip(self):
        rows = [{"d": "2026-09-01", "usd": 1}]
        _rc, _daily, _ctl, printed = self._run(rows)
        self.assertNotIn("skipped", printed)


class TornWriteFree(unittest.TestCase):
    """A write that failed partway used to leave a truncated `daily.json` (`open(path, "w")` truncates the
    instant it is opened, before `json.dump` gets to run) — the reader's next `report` would then see an
    empty or corrupt file instead of the last good one. `ingest` must build the JSON text first and replace
    the file as one atomic step, so a downstream write failure never touches an existing good file."""

    def test_ingest_never_hands_the_output_file_to_json_dump_directly(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "rows.json").write_text(json.dumps([{"d": "2026-09-01", "usd": 1, "inp": 2, "outp": 3}]), encoding="utf-8")
            out = d / "daily.json"
            out.write_text("PRE-EXISTING-GOOD-REPORT-DATA", encoding="utf-8")
            args = ns(rows=str(d / "rows.json"), out=str(out), control_out=str(d / "unused.json"), control_rows=None)
            # the old code wrote via `json.dump(daily, open(a.out, "w"), ...)`: forcing json.dump to explode
            # proves the fix no longer routes the write through it (it uses json.dumps + the atomic writer)
            with unittest.mock.patch("json.dump", side_effect=AssertionError("must not write ingest output via json.dump")):
                cost_report.cmd_ingest(args)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data[0]["d"], "2026-09-01")


class DocstringLooseCoupling(unittest.TestCase):
    def test_module_docstring_lists_every_subcommand(self):
        # `sub.add_parser(...)` in main() is the one place subcommands are registered; the docstring
        # drifted from it before (missing `propose-columns` entirely) because the two were kept in
        # sync by hand. Read main()'s source rather than re-typing the command list here, so a future
        # subcommand added to one and not the other still fails this test.
        import inspect
        src = inspect.getsource(cost_report.main)
        cmds = set(cost_report.re.findall(r'sub\.add_parser\("([a-z-]+)"\)', src))
        self.assertTrue(cmds, "could not find any sub.add_parser(...) calls in main()")
        for cmd in cmds:
            self.assertIn(cmd, cost_report.__doc__, cmd)


if __name__ == "__main__":
    unittest.main()
