"""cost_report.py: local-day PR/ticket dates, ingest against a warehouse that dropped a column, and
torn-write-free JSON output. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import argparse
import contextlib
import io
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


class GhTimeout(unittest.TestCase):
    """A hung `gh` call used to block cost_report.py forever (no `timeout=`); it must now fail closed
    with a distinct exit code and a clear reason instead of hanging the report."""

    def test_gh_timeout_is_bounded_and_exits_2(self):
        with unittest.mock.patch.object(cost_report.subprocess, "run",
                                         side_effect=cost_report.subprocess.TimeoutExpired(cmd=["gh"], timeout=60)):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                with self.assertRaises(SystemExit) as cm:
                    cost_report._gh(["repos/acme/widgets/pulls", "--jq", ".[]"])
            self.assertEqual(cm.exception.code, 2)
        self.assertIn("timed out after 60s", buf.getvalue())


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

    def test_null_and_non_object_rows_are_skipped_not_an_attributeerror(self):
        rc, daily, _, out = self._run([None, "header", {"D": "2026-09-01", "Cost": 1}])
        self.assertEqual(rc, 0)
        self.assertEqual(len(daily), 1)
        self.assertIn("2 row(s) skipped", out)

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


def _org_profile_get(path: str, default=None):
    """A minimal org-mode `kit_profile.get` stand-in: just enough `cost.*` + `systems.datalake` for
    `cmd_sql` to build both queries, without touching any real env store."""
    values = {
        "systems.datalake": True,
        "cost": {
            "spend_table": "spend.usage",
            "columns": {
                "date": "usage_date", "email": "user_email", "model": "model_id", "cost": "cost_usd",
                "input": "input_tokens", "output": "output_tokens",
                "cache_read": "cache_read_tokens", "cache_write": "cache_write_tokens",
            },
            "filters": "",
            "email": "me@example.com",
        },
        "tracker.kind": "none",
        "github.org": "",
    }
    return values.get(path, default)


class PortableControlSql(unittest.TestCase):
    """The control query used to bucket weeks with Snowflake-only `TO_CHAR(DATE_TRUNC(...), 'IYYY"W"IW')`,
    which Snowflake accepts but does not implement (the format elements are taken literally), so every row
    silently lands under the same week key instead of erroring. The query must now be a plain per-day
    GROUP BY that works the same on any engine; ISO-week bucketing moves into `ingest` (Python)."""

    def _sql(self) -> tuple[str, str]:
        buf = io.StringIO()
        with unittest.mock.patch.object(cost_report.profile, "get", side_effect=_org_profile_get):
            with contextlib.redirect_stdout(buf):
                rc = cost_report.cmd_sql(ns(since=None, until=None))
        self.assertEqual(rc, 0)
        own, control = buf.getvalue().split("\n\n", 1)
        return own, control

    def test_control_query_has_no_engine_specific_week_format(self):
        # only the executable SQL, not the comment explaining the bug this query used to have
        _own, control = self._sql()
        sql = "\n".join(ln for ln in control.splitlines() if not ln.lstrip().startswith("--"))
        self.assertNotIn("TO_CHAR", sql)
        self.assertNotIn("IYYY", sql)
        self.assertNotIn("DATE_TRUNC", sql)
        self.assertNotIn(" AS wk", sql)

    def test_control_query_groups_by_plain_date(self):
        _own, control = self._sql()
        self.assertIn("CAST(usage_date AS DATE) AS d,", control)
        self.assertIn("GROUP BY 1 ORDER BY 1;", control)
        self.assertIn("COUNT(DISTINCT user_email) AS users,", control)


class ControlWeekBucketing(unittest.TestCase):
    """`ingest` now does the ISO-week bucketing the SQL used to (see PortableControlSql): daily control
    rows (`d`) are grouped into `YYYY-Wnn` buckets with Python's own `date.isocalendar()`, and any `wk`
    value — whether it arrived pre-bucketed or was computed here — is checked against that shape before
    it can become part of a control file `report` will treat as real weeks."""

    def _ingest_control(self, control_rows: list) -> tuple[int, list | None, str]:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "rows.json").write_text(json.dumps([{"d": "2026-09-28", "usd": 1}]), encoding="utf-8")
            (d / "control_rows.json").write_text(json.dumps(control_rows), encoding="utf-8")
            out, control_out = d / "daily.json", d / "control.json"
            args = ns(rows=str(d / "rows.json"), out=str(out), control_out=str(control_out),
                      control_rows=str(d / "control_rows.json"))
            buf = []
            with unittest.mock.patch("builtins.print", lambda *a, **k: buf.append(" ".join(str(x) for x in a))):
                rc = cost_report.cmd_ingest(args)
            ctl = json.loads(control_out.read_text(encoding="utf-8")) if control_out.exists() else None
            return rc, ctl, "\n".join(buf)

    def test_daily_control_rows_bucket_into_the_right_iso_week(self):
        # 2026-09-28 is a Monday (ISO week 40); 2026-10-04 is the Sunday closing that same ISO week.
        rows = [
            {"d": "2026-09-28", "users": 10, "usd": 100, "inp": 10, "cached": 1, "cwrite": 1, "outp": 50, "cheap_outp": 20},
            {"d": "2026-10-04", "users": 6, "usd": 40, "inp": 4, "cached": 0, "cwrite": 0, "outp": 20, "cheap_outp": 5},
            {"d": "2026-10-05", "users": 3, "usd": 10, "inp": 1, "cached": 0, "cwrite": 0, "outp": 5, "cheap_outp": 1},  # ISO week 41
        ]
        rc, ctl, _printed = self._ingest_control(rows)
        self.assertEqual(rc, 0)
        weeks = {c["wk"]: c for c in ctl}
        self.assertEqual(set(weeks), {"2026-W40", "2026-W41"})
        w40 = weeks["2026-W40"]
        self.assertEqual(w40["usd"], 140)                 # 100 + 40, summed across the two week-40 days
        self.assertEqual(w40["user_days"], 16)             # 10 + 6, exact (each row is a single day)
        self.assertEqual(w40["users"], 10)                 # max of the daily counts, not their sum
        self.assertEqual(weeks["2026-W41"]["usd"], 10)

    def test_malformed_week_key_is_rejected_not_silently_ingested(self):
        # the exact shape the Snowflake TO_CHAR('IYYY"W"IW') bug produced: every row collapsed to one
        # literal, non-ISO key. A control file built from that query must never pass ingest.
        rc, ctl, printed = self._ingest_control([{"wk": "I2626-WIW", "usd": 10, "outp": 100}])
        self.assertEqual(rc, 2)
        self.assertIsNone(ctl)
        self.assertIn("YYYY-Wnn", printed)

    def test_wk_column_still_accepted_when_already_valid(self):
        # an engine-specific week query (e.g. Snowflake YEAROFWEEKISO/WEEKISO) may still emit `wk` directly.
        rc, ctl, _printed = self._ingest_control([{"wk": "2026-W39", "usd": 10, "outp": 100}])
        self.assertEqual(rc, 0)
        self.assertEqual([c["wk"] for c in ctl], ["2026-W39"])


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
