"""cost_report.py: local-day PR/ticket dates, ingest against a warehouse that dropped a column, and
torn-write-free JSON output. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import argparse
import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
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
        # 2026-09-28 is a Monday (ISO week 40); 2026-10-01 is the Thursday in that same ISO week — both
        # weekdays, so the weekday filter (see ControlWeekBucketing's own weekend test) leaves both in.
        rows = [
            {"d": "2026-09-28", "users": 10, "usd": 100, "inp": 10, "cached": 1, "cwrite": 1, "outp": 50, "cheap_outp": 20},
            {"d": "2026-10-01", "users": 6, "usd": 40, "inp": 4, "cached": 0, "cwrite": 0, "outp": 20, "cheap_outp": 5},
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

    def test_weekend_daily_control_row_excluded_no_sql_predicate_needed(self):
        # the control query has no weekday predicate of its own (nothing engine-specific to swap): a daily
        # row (`d`, not a pre-bucketed `wk`) landing on a Saturday/Sunday must be filtered here, in Python,
        # the same `_is_wday` test `report`'s buckets use — never left in to drag down a user-day average.
        rows = [
            {"d": "2026-09-28", "users": 10, "usd": 100, "inp": 10, "cached": 1, "cwrite": 1, "outp": 50, "cheap_outp": 20},  # Monday
            {"d": "2026-10-03", "users": 4, "usd": 40, "inp": 4, "cached": 0, "cwrite": 0, "outp": 20, "cheap_outp": 5},      # Saturday, same ISO week
        ]
        rc, ctl, _printed = self._ingest_control(rows)
        self.assertEqual(rc, 0)
        self.assertEqual({c["wk"] for c in ctl}, {"2026-W40"})
        self.assertEqual(ctl[0]["usd"], 100)  # only the Monday row — the Saturday row never joined the sum

    def test_wk_row_has_no_weekday_to_check_and_stays_as_is(self):
        # a row that already arrived weekly (an engine-specific week query the user wrote) carries no single
        # day to test — it must not be dropped for "not being a weekday".
        rc, ctl, _printed = self._ingest_control([{"wk": "2026-W39", "usd": 10, "outp": 100}])
        self.assertEqual(rc, 0)
        self.assertEqual(ctl[0]["usd"], 10)


class StrictNumParsing(unittest.TestCase):
    """`_num` used to fall back to 0.0 on anything it could not parse, so a mistyped amount reported a
    real cost as free instead of failing. None/"" still mean 0.0 (an absent column); a currency sign and/or
    thousands separators are explicitly accepted; anything else is a named error, never a silent zero."""

    def test_none_and_empty_string_are_still_zero(self):
        self.assertEqual(cost_report._num(None), 0.0)
        self.assertEqual(cost_report._num(""), 0.0)

    def test_plain_number_and_numeric_string(self):
        self.assertEqual(cost_report._num(3), 3.0)
        self.assertEqual(cost_report._num("3.5"), 3.5)

    def test_currency_and_thousands_separator_forms_accepted(self):
        self.assertEqual(cost_report._num("$1,234.50"), 1234.5)
        self.assertEqual(cost_report._num("1,234"), 1234.0)
        self.assertEqual(cost_report._num("-$12.00"), -12.0)

    def test_unparseable_value_names_row_and_column_not_a_silent_zero(self):
        with self.assertRaises(cost_report.StrictParseError) as cm:
            cost_report._num("not-a-number", row=4, col="usd")
        msg = str(cm.exception)
        self.assertIn("row 4", msg)
        self.assertIn("usd", msg)


class NonFiniteNumbersRefused(unittest.TestCase):
    """`float()` reads "nan", "inf" and "Infinity", and JSON rows can carry a bare NaN; none of them is an
    amount, and one NaN turns every sum it joins into NaN."""

    def test_nan_and_inf_strings_are_refused(self):
        for raw in ("nan", "NaN", "inf", "-inf", "Infinity"):
            with self.subTest(raw=raw):
                with self.assertRaises(cost_report.StrictParseError) as cm:
                    cost_report._num(raw, row=3, col="usd")
                self.assertIn("row 3", str(cm.exception))
                self.assertIn("a finite number", str(cm.exception))

    def test_nan_and_inf_floats_are_refused(self):
        for raw in (float("nan"), float("inf")):
            with self.subTest(raw=raw):
                with self.assertRaises(cost_report.StrictParseError):
                    cost_report._num(raw, row=1, col="usd")


class StrictDayParsing(unittest.TestCase):
    def test_bad_date_names_row_and_column(self):
        with self.assertRaises(cost_report.StrictParseError) as cm:
            cost_report._day("not-a-date", row=2, col="d")
        msg = str(cm.exception)
        self.assertIn("row 2", msg)
        self.assertIn("'d'", msg)

    def test_good_date_passes_through_unchanged(self):
        self.assertEqual(cost_report._day("2026-09-01"), "2026-09-01")

    def test_impossible_calendar_date_still_rejected(self):
        # shape matches YYYY-MM-DD but the date does not exist — date.fromisoformat catches it
        with self.assertRaises(cost_report.StrictParseError):
            cost_report._day("2026-02-30", row=1, col="d")


class StrictErrorReachesMainAsOneLine(unittest.TestCase):
    """A row's unparseable value must become one clean stderr line and exit code 2 from `main()` — never
    an uncaught traceback. `StrictParseError` is a `ValueError` so `_control_week_key`'s own pre-existing
    soft catch still works, but every other path (here: `ingest`'s daily rows) must reach `main`'s handler."""

    def test_main_catches_strict_parse_error_exit_2_no_traceback(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "rows.json").write_text(json.dumps([{"d": "2026-09-01", "usd": "garbage"}]), encoding="utf-8")
            out, control_out = d / "daily.json", d / "control.json"
            argv = ["ingest", "--rows", str(d / "rows.json"), "--out", str(out), "--control-out", str(control_out)]
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = cost_report.main(argv)
            self.assertEqual(rc, 2)
            self.assertIn("usd", buf.getvalue())
            self.assertIn("row 1", buf.getvalue())  # rows are counted from 1, the way a person counts them
            self.assertNotIn("Traceback", buf.getvalue())


class CsvReaderStrictNum(unittest.TestCase):
    """The private-mode CSV reader fed raw cell values straight to `_num` with no row/column context; a
    bad cell must fail the same named way `ingest`'s rows do, not with a bare ValueError."""

    def test_bad_csv_cell_names_row_and_the_csv_header(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "export.csv"
            path.write_text("date,cost\n2026-09-01,12.00\n2026-09-02,not-a-number\n", encoding="utf-8")
            with self.assertRaises(cost_report.StrictParseError) as cm:
                cost_report.read_csv(str(path))
            msg = str(cm.exception)
            self.assertIn("row 2", msg)  # the second data row; the header is not counted
            self.assertIn("cost", msg)


class SinceUntilValidatedEverywhere(unittest.TestCase):
    """`--since`/`--until` used to be checked only by `sql`; every subcommand that takes either flag must
    reject a non-`YYYY-MM-DD` value the same way, through the one shared helper."""

    def test_check_since_until_rejects_bad_shape(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cost_report._check_since_until(ns(since="09/01/2026", until=None))
        self.assertEqual(rc, 2)
        self.assertIn("--since wants YYYY-MM-DD, got '09/01/2026'", buf.getvalue())

    def test_check_since_until_rejects_other_iso_spellings(self):
        # Newer Pythons read these with `date.fromisoformat`; the value goes into SQL text and string
        # compares as typed, so only the dashed calendar form is accepted.
        for raw in ("20260901", "2026-W36-1", "2026-09-01T00:00:00", "2026-9-1"):
            with self.subTest(raw=raw):
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    rc = cost_report._check_since_until(ns(since=None, until=raw))
                self.assertEqual(rc, 2)
                self.assertIn(f"--until wants YYYY-MM-DD, got {raw!r}", buf.getvalue())

    def test_check_since_until_rejects_an_impossible_calendar_date(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cost_report._check_since_until(ns(since="2026-02-30", until=None)), 2)

    def test_check_since_until_accepts_good_dates_and_missing_flags(self):
        self.assertIsNone(cost_report._check_since_until(ns(since="2026-09-01", until="2026-09-30")))
        self.assertIsNone(cost_report._check_since_until(ns()))  # a subcommand without either flag

    def test_collect_private_rejects_bad_since_before_touching_transcripts(self):
        buf = io.StringIO()
        with unittest.mock.patch.object(cost_report, "collect_private", side_effect=AssertionError("must not run")):
            with contextlib.redirect_stderr(buf):
                rc = cost_report.cmd_collect_private(ns(since="bogus", until=None, csv=None, out="unused.json"))
        self.assertEqual(rc, 2)
        self.assertIn("--since wants YYYY-MM-DD", buf.getvalue())

    def test_work_tickets_rejects_bad_until_before_touching_gh(self):
        buf = io.StringIO()
        with unittest.mock.patch.object(cost_report, "_gh", side_effect=AssertionError("must not call gh")):
            with contextlib.redirect_stderr(buf):
                rc = cost_report.cmd_work_tickets(ns(since=None, until="not-a-date", out="unused.tsv"))
        self.assertEqual(rc, 2)
        self.assertIn("--until wants YYYY-MM-DD", buf.getvalue())

    def test_report_rejects_bad_until(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cost_report.cmd_report(ns(daily="unused.json", control=None, prs=None, tickets=None,
                                            phase=None, view="all", until="not-a-date", out=None, json=None))
        self.assertEqual(rc, 2)
        self.assertIn("--until wants YYYY-MM-DD", buf.getvalue())


class FixedPricesHonourOverride(unittest.TestCase):
    """FIXED used to be `session_stats.DEFAULT_PRICES` verbatim, ignoring `SESSION_STATS_PRICES`. It must
    track whatever `prices()` resolves — the same override `session_stats.py`'s own CLI honours."""

    def test_fixed_follows_session_stats_prices_override(self):
        with unittest.mock.patch.dict(os.environ, {"SESSION_STATS_PRICES": "11,22,33,44,55"}):
            importlib.reload(cost_report)
        try:
            self.assertEqual(cost_report.FIXED, (11.0, 22.0, 33.0, 44.0))
        finally:
            importlib.reload(cost_report)  # restore the default basis for every other test in this process


class SinceCutoffTs(unittest.TestCase):
    def test_cutoff_is_since_day_minus_one_at_utc_midnight(self):
        ts = cost_report._since_cutoff_ts("2026-09-02")
        self.assertEqual(datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(), "2026-09-01")

    def test_no_since_means_no_cutoff(self):
        self.assertIsNone(cost_report._since_cutoff_ts(None))


class CollectPrivateMtimeSkip(unittest.TestCase):
    """A transcript last written well before `--since` cannot hold a record on or after it in any
    timezone; `collect_private` must skip it by mtime before ever opening it, not just filter its
    (never-existing) matching records after the fact."""

    def test_old_transcript_never_opened_new_one_is(self):
        calls = []

        def fake_usage_records(path, seen):
            calls.append(path)
            return iter(())

        mtimes = {"/fake/old.jsonl": 1_000.0, "/fake/new.jsonl": 9_999_999_999.0}
        with unittest.mock.patch.object(cost_report, "_iter_transcripts", return_value=list(mtimes)), \
             unittest.mock.patch.object(cost_report.os.path, "getmtime", side_effect=lambda p: mtimes[p]), \
             unittest.mock.patch.object(cost_report.transcripts, "usage_records", side_effect=fake_usage_records):
            cost_report.collect_private("2026-09-01", None)
        self.assertNotIn("/fake/old.jsonl", calls)
        self.assertIn("/fake/new.jsonl", calls)


class GhRateLimitRetry(unittest.TestCase):
    """A `gh search` call used to fail outright on a rate limit; it must now retry a small, bounded number
    of times with a doubling delay, each wait announced on stderr, before giving up."""

    def test_retries_on_rate_limit_then_succeeds(self):
        calls = []
        responses = iter([
            unittest.mock.Mock(returncode=1, stdout="", stderr="API rate limit exceeded for user"),
            unittest.mock.Mock(returncode=1, stdout="", stderr="you have exceeded a secondary rate limit"),
            unittest.mock.Mock(returncode=0, stdout="[]", stderr=""),
        ])

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return next(responses)

        with unittest.mock.patch.dict(os.environ, {"COST_REPORT_RETRY_DELAY": "1"}), \
             unittest.mock.patch.object(cost_report.subprocess, "run", side_effect=fake_run), \
             unittest.mock.patch.object(cost_report.time, "sleep") as sleep_mock, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            out = cost_report._gh(["search", "prs"], context="org:acme 2026-01-01..2026-01-31")
        self.assertEqual(out, "[]")
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in sleep_mock.call_args_list], [1, 2])  # doubling, not flat
        notices = err.getvalue().splitlines()
        self.assertEqual(len(notices), 2)  # one line per wait, so a silent minute never looks like a hang
        self.assertIn("org:acme 2026-01-01..2026-01-31", notices[0])
        self.assertIn("waiting 1s, attempt 1 of 4", notices[0])
        self.assertIn("waiting 2s, attempt 2 of 4", notices[1])

    def test_default_waits_outlast_the_one_minute_search_window(self):
        def fake_run(cmd, **kw):
            return unittest.mock.Mock(returncode=1, stdout="", stderr="API rate limit exceeded")

        with unittest.mock.patch.dict(os.environ, {}, clear=False), \
             unittest.mock.patch.object(cost_report.subprocess, "run", side_effect=fake_run), \
             unittest.mock.patch.object(cost_report.time, "sleep") as sleep_mock, \
             contextlib.redirect_stderr(io.StringIO()):
            os.environ.pop("COST_REPORT_RETRY_DELAY", None)
            with self.assertRaises(SystemExit):
                cost_report._gh(["search", "prs"])
        waits = [c.args[0] for c in sleep_mock.call_args_list]
        self.assertEqual(waits, [10, 20, 40])
        self.assertGreater(sum(waits), 60)

    def test_non_rate_limit_failure_is_not_retried(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return unittest.mock.Mock(returncode=1, stdout="", stderr="bad credentials")

        with unittest.mock.patch.object(cost_report.subprocess, "run", side_effect=fake_run), \
             unittest.mock.patch.object(cost_report.time, "sleep") as sleep_mock:
            with self.assertRaises(SystemExit):
                cost_report._gh(["search", "prs"])
        self.assertEqual(len(calls), 1)
        sleep_mock.assert_not_called()

    def test_exhausted_retries_name_the_context_and_exit_nonzero(self):
        def fake_run(cmd, **kw):
            return unittest.mock.Mock(returncode=1, stdout="", stderr="secondary rate limit exceeded")

        with unittest.mock.patch.dict(os.environ, {"COST_REPORT_RETRY_DELAY": "0"}), \
             unittest.mock.patch.object(cost_report.subprocess, "run", side_effect=fake_run), \
             unittest.mock.patch.object(cost_report.time, "sleep"), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                cost_report._gh(["search", "issues"], context="acme/repo 2026-01-01..2026-01-31")
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("acme/repo 2026-01-01..2026-01-31", str(cm.exception.code))


class CostReportRetryDelayEnvVar(unittest.TestCase):
    def test_env_var_overrides_default(self):
        with unittest.mock.patch.dict(os.environ, {"COST_REPORT_RETRY_DELAY": "5"}):
            self.assertEqual(cost_report._gh_retry_delay(), 5.0)

    def test_default_when_unset(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COST_REPORT_RETRY_DELAY", None)
            self.assertEqual(cost_report._gh_retry_delay(), 10.0)

    def test_a_negative_or_unreadable_value_falls_back_to_the_default(self):
        for raw in ("-5", "soon", "nan", "inf"):
            with self.subTest(raw=raw), unittest.mock.patch.dict(os.environ, {"COST_REPORT_RETRY_DELAY": raw}):
                self.assertEqual(cost_report._gh_retry_delay(), 10.0)


class WorkPrsDraftExclusion(unittest.TestCase):
    """A draft PR showed up in `work-prs` as opened work it is not — it cannot be merged without leaving
    draft first, so counting it inflates the open count against a unit that was never really shipped."""

    def test_draft_pr_excluded_non_draft_counted(self):
        hits = [
            {"repository": {"nameWithOwner": "acme/repo"}, "number": 1, "createdAt": "2026-01-05T10:00:00Z",
             "closedAt": "", "state": "open", "isDraft": True, "url": ""},
            {"repository": {"nameWithOwner": "acme/repo"}, "number": 2, "createdAt": "2026-01-06T10:00:00Z",
             "closedAt": "", "state": "open", "isDraft": False, "url": ""},
        ]

        def fake_gh(args, context=""):
            return "[]" if "is:merged" in args else json.dumps(hits)

        fixed_mode = {"github_login": "octocat", "github_org": "acme", "extra_repos": []}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "prs.tsv"
            with unittest.mock.patch.object(cost_report, "resolve_mode", return_value=fixed_mode), \
                 unittest.mock.patch.object(cost_report, "_gh", side_effect=fake_gh), \
                 unittest.mock.patch.object(cost_report, "_owner_zone", return_value=(timezone.utc, "UTC")):
                rc = cost_report.cmd_work_prs(ns(since="2026-01-01", until="2026-01-31", out=str(out)))
            self.assertEqual(rc, 0)
            lines = out.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)  # header + exactly one (non-draft) PR
        self.assertEqual(lines[1].split("\t")[1], "2")


class PrOverlayCappedBothEnds(unittest.TestCase):
    """The PR overlay used to cap only at the last spend day; a PR opened BEFORE the first spend day must
    be excluded the same way, not just one opened after the last."""

    def test_pr_before_first_spend_day_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            daily = d / "daily.json"
            daily.write_text(json.dumps([
                {"d": "2026-01-10", "model": "sonnet", "usd": 1.0, "inp": 1, "cached": 0, "cwrite": 0, "outp": 10, "billed": True},
                {"d": "2026-01-15", "model": "sonnet", "usd": 1.0, "inp": 1, "cached": 0, "cwrite": 0, "outp": 10, "billed": True},
            ]), encoding="utf-8")
            prs = d / "prs.tsv"
            prs.write_text(
                "repo\tnumber\tcreated\tmerged\tclosed\tstate\n"
                "acme/repo\t1\t2026-01-05\t\t\topen\n"   # before the first spend day — must not count
                "acme/repo\t2\t2026-01-20\t\t\topen\n"   # after the last spend day — already excluded before this fix
                "acme/repo\t3\t2026-01-12\t\t\topen\n",  # inside the window — the only one that should count
                encoding="utf-8")
            out_json = d / "report.json"
            args = ns(daily=str(daily), control=None, prs=str(prs), tickets=None,
                      phase=["wide=2026-01-01..2026-01-31"], view="phases", until=None, out=None, json=str(out_json))
            buf = []
            with unittest.mock.patch("builtins.print", lambda *a, **k: buf.append(" ".join(str(x) for x in a))):
                rc = cost_report.cmd_report(args)
            self.assertEqual(rc, 0)
            o = json.loads(out_json.read_text(encoding="utf-8"))
        wide = next(b for b in o["buckets"] if b["bucket"] == "wide")
        self.assertEqual(wide["pr_open"], 1)

    def test_ticket_resolved_before_first_spend_day_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            daily = d / "daily.json"
            daily.write_text(json.dumps([
                {"d": "2026-01-10", "model": "sonnet", "usd": 1.0, "inp": 1, "cached": 0, "cwrite": 0, "outp": 10, "billed": True},
                {"d": "2026-01-15", "model": "sonnet", "usd": 1.0, "inp": 1, "cached": 0, "cwrite": 0, "outp": 10, "billed": True},
            ]), encoding="utf-8")
            tickets = d / "tickets.tsv"
            tickets.write_text(
                "key\tcreated\tresolved\ttype\tparent\n"
                "KEY-456\t2026-01-02\t2026-01-05\ttask\t\n"   # resolved before the first spend day
                "KEY-123\t2026-01-02\t2026-01-12\ttask\t\n",  # inside the window — the only one that counts
                encoding="utf-8")
            out_json = d / "report.json"
            args = ns(daily=str(daily), control=None, prs=None, tickets=str(tickets),
                      phase=["wide=2026-01-01..2026-01-31"], view="phases", until=None, out=None, json=str(out_json))
            with unittest.mock.patch("builtins.print", lambda *a, **k: None):
                rc = cost_report.cmd_report(args)
            self.assertEqual(rc, 0)
            o = json.loads(out_json.read_text(encoding="utf-8"))
        wide = next(b for b in o["buckets"] if b["bucket"] == "wide")
        self.assertEqual(wide["tk_closed"], 1)
        self.assertIn("capped at 2026-01-10..2026-01-15", o["basis"]["tickets"])


class ReservedPhaseNameRefused(unittest.TestCase):
    """`report` always builds its own `prior-7d`/`rolling-7d` rolling pair; a `--phase`/`cost.phases` entry
    reusing one of those names must be refused, not silently merged into (or overwriting) the built-in one."""

    def test_phase_named_rolling_7d_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            daily = Path(d) / "daily.json"
            daily.write_text(json.dumps([{"d": "2026-01-10", "model": "sonnet", "usd": 1.0, "inp": 1,
                                           "cached": 0, "cwrite": 0, "outp": 10, "billed": True}]), encoding="utf-8")
            args = ns(daily=str(daily), control=None, prs=None, tickets=None,
                      phase=["rolling-7d=2026-01-01..2026-01-10"], view="phases", until=None, out=None, json=None)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = cost_report.cmd_report(args)
        self.assertEqual(rc, 2)
        self.assertIn("rolling-7d", buf.getvalue())
        self.assertIn("prior-7d", buf.getvalue())


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
