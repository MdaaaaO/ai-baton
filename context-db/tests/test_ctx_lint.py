"""ctx_lint.py on a throw-away fixture store: one finding of each kind, the history exemption, the key cap, an
UNCHECKED claim on a failed or skipped gh read, one batched query per repository, the fork plan. gh is always a
stand-in (an injected runner, or a fake executable behind --gh): no test touches the network or a real store.
Stdlib unittest. Run: make -C $BATON/context-db test."""
from __future__ import annotations
import contextlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))

import ctx_lint  # noqa: E402
import gen_sessions  # noqa: E402

TODAY = "2026-10-23"


def doc(title: str, body: str, *, type_: str = "epic", status: str = "active", updated: str = TODAY,
        domain: str = "proj") -> str:
    return (f"---\ntitle: {title}\ntype: {type_}\ndomain: {domain}\nstatus: {status}\nupdated: {updated}\n---\n\n"
            f"# {title}\n\n{body}\n")


ALPHA = """## Tracker & links

- Epic: https://github.com/acme/widgets/issues/1 is open
- Shipped: acme/widgets#2 merged on the main branch
- 2026-09-01 — https://github.com/acme/widgets/issues/3 open (history, a dated bullet)
- See [beta](beta.md) and [gone](missing.md)

## Goal

Ship it.

## Remaining work

- 2026-10-01 — acme/widgets#4 still open
- 2026-10-20 — acme/widgets#5 open, review pending

## Session log

- 2026-10-02 — https://github.com/acme/widgets/issues/6 open (the log records history)
"""


def fixture(root: Path, extra: dict[str, str] | None = None) -> Path:
    files = {
        "proj/alpha.md": doc("Alpha", ALPHA),
        "proj/beta.md": doc("Beta", "Linked from alpha. See `other/gamma` and [old](old.md) for the rest."),
        "other/gamma.md": doc("Gamma", "Mentioned by path in beta. Back to [alpha](../proj/alpha.md).", domain="other"),
        "proj/orphan.md": doc("Orphan", "Nothing links here."),
        "proj/old.md": doc("Old", "Done long ago, linked from beta.", status="closed",
                           updated="2026-01-01"),
        "archive/alpha-log.md": doc("Alpha log", "- 2026-09-01 — https://github.com/acme/widgets/issues/7 open", type_="log"),
        "INDEX.md": "# INDEX\n\n" + "\n".join(f"- [{p}]({p})" for p in
                                               ("proj/orphan.md", "proj/old.md", "proj/alpha.md")) + "\n",
    }
    files.update(extra or {})
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


class FakeGh:
    """A runner that answers `gh api graphql` from a {repo: {number: STATE}} table and records every call."""

    def __init__(self, states: dict[str, dict[int, str]], rc: int = 0, out: str | None = None):
        self.states, self.rc, self.out, self.calls = states, rc, out, []

    def __call__(self, args: list[str]) -> tuple[int, str, str]:
        self.calls.append(args)
        if self.out is not None:
            return self.rc, self.out, "stub failure"
        q = next(a for a in args if a.startswith("query="))
        repo = "/".join(re.search(r'owner: "([^"]+)", name: "([^"]+)"', q).groups())
        repository = {}
        for n in re.findall(r"n(\d+): issueOrPullRequest", q):
            state = self.states.get(repo, {}).get(int(n))
            repository[f"n{n}"] = None if state is None else {"__typename": "Issue", "state": state}
        return 0, json.dumps({"data": {"repository": repository}}), ""


def lines_of(report: "ctx_lint.Report") -> list[str]:
    return report.render()


class Lint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = fixture(Path(self.tmp.name) / "store")
        self.today = date.fromisoformat(TODAY)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, gh=None, max_keys=100, offline=False):
        return ctx_lint.run_check(self.root, None if offline else gh, max_keys, self.today)

    def test_one_finding_of_each_kind(self):
        gh = FakeGh({"acme/widgets": {1: "CLOSED", 2: "MERGED", 5: "OPEN"}})
        out = lines_of(self.check(gh))
        self.assertIn("ORPHAN proj/orphan.md", out)
        self.assertIn("DEAD proj/alpha.md missing.md", out)
        self.assertIn("STALE proj/alpha.md acme/widgets#1 stated open · github acme/widgets#1 is CLOSED", out)
        self.assertTrue(any(l.startswith("AGED proj/old.md status closed, untouched 295 days (> 7)") for l in out), out)
        self.assertEqual(len([l for l in out if l.split()[0] == "STALE"]), 1, out)  # #2 merged is right, #5 open is right

    def test_the_generated_index_does_not_rescue_an_orphan_but_a_path_mention_does(self):
        out = lines_of(self.check(FakeGh({})))
        self.assertIn("ORPHAN proj/orphan.md", out)            # INDEX.md links it
        self.assertNotIn("ORPHAN other/gamma.md", out)         # beta names `other/gamma` in prose
        self.assertNotIn("ORPHAN proj/beta.md", out)           # alpha links it
        self.assertNotIn("ORPHAN archive/alpha-log.md", out)   # history is never an orphan
        self.assertNotIn("ORPHAN proj/alpha.md", out)          # gamma links it

    def test_history_is_never_a_claim(self):
        gh = FakeGh({"acme/widgets": {n: "CLOSED" for n in range(1, 10)}})
        stale = [l for l in lines_of(self.check(gh)) if l.startswith(("STALE", "UNCHECKED"))]
        keys = {re.search(r"acme/widgets#(\d+)", l).group(1) for l in stale}
        # #1 and #2 (Tracker), #5 (newest Remaining work bullet) are claims; #3 dated bullet, #4 older bullet,
        # #6 Session log and #7 archive/ log are history
        self.assertEqual(keys, {"1", "2", "5"})
        asked = {int(n) for call in gh.calls for n in re.findall(r"n(\d+):", call[-1])}
        self.assertEqual(asked, {1, 2, 5})

    def test_a_dated_bullet_in_a_current_section_is_history(self):
        gh = FakeGh({"acme/widgets": {3: "CLOSED"}})
        self.check(gh)
        self.assertNotIn(3, {int(n) for call in gh.calls for n in re.findall(r"n(\d+):", call[-1])})

    def test_one_batched_query_per_repository(self):
        extra = {"proj/beta.md": doc("Beta", "## Tracker & links\n\n- https://github.com/other/repo/pull/9 open\n"
                                              "- https://github.com/acme/widgets/issues/8 is closed\n"
                                              "Mentions [old](old.md) and `other/gamma`.")}
        fixture(self.root, extra)
        gh = FakeGh({"acme/widgets": {1: "OPEN", 2: "MERGED", 5: "OPEN", 8: "CLOSED"}, "other/repo": {9: "OPEN"}})
        self.check(gh)
        repos = sorted(re.search(r'name: "([^"]+)"', c[-1]).group(1) for c in gh.calls)
        self.assertEqual(repos, ["repo", "widgets"])
        self.assertTrue(all(c[:3] == ["api", "graphql", "-f"] for c in gh.calls))

    def test_state_rules(self):
        cases = [("open", "OPEN", False), ("open", "CLOSED", True), ("open", "MERGED", True),
                 ("merged", "MERGED", False), ("merged", "CLOSED", True), ("merged", "OPEN", True),
                 ("closed", "OPEN", True), ("closed", "CLOSED", False), ("closed", "MERGED", False)]
        for said, actual, stale in cases:
            with self.subTest(said=said, actual=actual):
                self.assertEqual(ctx_lint.contradicts(said, actual), stale)

    def test_negated_or_future_state_words_are_not_claims(self):
        text = ("## Tracker & links\n\n- https://github.com/acme/widgets/issues/1 was open until Friday\n"
                "- https://github.com/acme/widgets/pull/2 not merged yet\n- acme/widgets#3 to be closed later\n"
                "- acme/widgets#4 reopened last week\n")
        claims = ctx_lint.doc_claims(ctx_lint.Doc("p/x.md", Path("x"), doc("X", text), {}, "p"))
        self.assertEqual(claims, [])

    def test_two_keys_on_one_line_each_take_their_own_clause(self):
        unit = "https://github.com/acme/widgets/issues/25 closed as passed; acme/widgets#188 owner-triggered publish."
        got = [(c.number, c.state) for c in ctx_lint.unit_claims("d.md", unit)]
        self.assertEqual(got, [(25, "closed")])

    def test_the_key_cap_marks_the_rest_unchecked(self):
        items = "\n".join(f"- acme/widgets#{n} open" for n in range(1, 106))
        fixture(self.root, {"proj/many.md": doc("Many", f"## Tracker & links\n\n{items}\n"),
                            "proj/alpha.md": doc("Alpha", "Links [many](many.md) [b](beta.md).")})
        gh = FakeGh({"acme/widgets": {n: "OPEN" for n in range(1, 106)}})
        out = lines_of(self.check(gh))
        asked = [int(n) for call in gh.calls for n in re.findall(r"n(\d+):", call[-1])]
        self.assertEqual(len(gh.calls), 1)
        self.assertEqual(sorted(asked), list(range(1, 101)))
        unchecked = [l for l in out if l.startswith("UNCHECKED")]
        self.assertEqual(len(unchecked), 5)
        self.assertTrue(all("over the 100-key cap" in l for l in unchecked))
        self.assertFalse([l for l in out if l.startswith("STALE")])

    def test_a_smaller_cap_is_honoured(self):
        gh = FakeGh({"acme/widgets": {1: "CLOSED", 2: "MERGED", 5: "OPEN"}})
        out = lines_of(self.check(gh, max_keys=1))
        self.assertEqual([int(n) for n in re.findall(r"n(\d+):", gh.calls[0][-1])], [1])
        self.assertIn("UNCHECKED proj/alpha.md acme/widgets#2 stated merged (over the 1-key cap)", out)

    def test_a_failed_gh_read_is_unchecked_never_guessed(self):
        report = self.check(FakeGh({}, rc=1, out="not json"))
        out = lines_of(report)
        self.assertTrue(report.gh_failed)
        self.assertIn("UNCHECKED proj/alpha.md acme/widgets#1 stated open (gh read failed)", out)
        self.assertFalse([l for l in out if l.startswith("STALE")])

    def test_an_unknown_number_is_unchecked(self):
        out = lines_of(self.check(FakeGh({"acme/widgets": {1: "OPEN", 5: "OPEN"}})))
        self.assertIn("UNCHECKED proj/alpha.md acme/widgets#2 stated merged (not found on GitHub)", out)

    def test_offline_never_calls_gh(self):
        out = lines_of(self.check(offline=True))
        self.assertIn("UNCHECKED proj/alpha.md acme/widgets#1 stated open (offline)", out)
        self.assertIn("ORPHAN proj/orphan.md", out)  # the mechanical checks still run

    def test_clean_store(self):
        root = Path(self.tmp.name) / "clean"
        (root / "a").mkdir(parents=True)
        (root / "a" / "one.md").write_text(doc("One", "See [two](two.md).", domain="a"), encoding="utf-8")
        (root / "a" / "two.md").write_text(doc("Two", "See [one](one.md).", domain="a"), encoding="utf-8")
        self.assertEqual(lines_of(ctx_lint.run_check(root, FakeGh({}), 100, self.today)), [])

    def test_aged_uses_the_engines_archive_constant(self):
        self.assertEqual(ctx_lint.gen_sessions.ARCHIVE_DAYS, gen_sessions.ARCHIVE_DAYS)
        fresh = date.fromisoformat("2026-01-01").toordinal() + gen_sessions.ARCHIVE_DAYS
        report = ctx_lint.Report()
        ctx_lint.check_aged([ctx_lint.Doc("p/x.md", Path("x"), "", {"status": "closed", "updated": "2026-01-01"}, "p")],
                            date.fromordinal(fresh), report)
        self.assertEqual(report.lines, [])  # exactly ARCHIVE_DAYS old is not past the rule
        ctx_lint.check_aged([ctx_lint.Doc("p/x.md", Path("x"), "", {"status": "closed", "updated": "2026-01-01"}, "p")],
                            date.fromordinal(fresh + 1), report)
        self.assertEqual(len(report.lines), 1)

    def test_active_and_archived_docs_are_never_aged(self):
        out = lines_of(self.check(FakeGh({})))
        self.assertFalse([l for l in out if l.startswith("AGED") and "alpha" in l])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = fixture(Path(self.tmp.name) / "store")
        self.gh = Path(self.tmp.name) / "fake-gh"
        self.gh.write_text("#!/bin/sh\nprintf 'x' >> \"$0.calls\"\n"
                           "printf '%s' \"$FAKE_GH_OUT\"\nexit \"${FAKE_GH_RC:-0}\"\n", encoding="utf-8")
        self.gh.chmod(self.gh.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        e = {**os.environ, "CONTEXT_ROOT": str(self.root), **(env or {})}
        return subprocess.run([sys.executable, str(BIN / "ctx_lint.py"), *args], capture_output=True, text=True, env=e)

    def test_findings_exit_3_and_print_the_grammar(self):
        p = self.run_cli("check", "--root", str(self.root), "--offline", "--today", TODAY)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("ORPHAN proj/orphan.md", p.stdout)
        self.assertNotIn("Traceback", p.stderr)

    def test_offline_env_never_runs_gh(self):
        self.run_cli("check", "--root", str(self.root), "--gh", str(self.gh), "--today", TODAY,
                     env={"CTX_LINT_OFFLINE": "1"})
        self.assertFalse(Path(f"{self.gh}.calls").exists())

    def test_a_failing_gh_executable_is_unchecked_and_exit_1_when_nothing_else_is_found(self):
        clean = Path(self.tmp.name) / "c"
        (clean / "a").mkdir(parents=True)
        (clean / "a" / "one.md").write_text(doc("One", "## Tracker & links\n\n- acme/widgets#1 open\n", domain="a"),
                                            encoding="utf-8")
        (clean / "a" / "two.md").write_text(doc("Two", "See [one](one.md).", domain="a"), encoding="utf-8")
        (clean / "a" / "one.md").write_text(doc("One", "## Tracker & links\n\n- acme/widgets#1 open\n[two](two.md)",
                                                domain="a"), encoding="utf-8")
        p = self.run_cli("check", "--root", str(clean), "--gh", str(self.gh), "--today", TODAY,
                         env={"FAKE_GH_RC": "1", "FAKE_GH_OUT": ""})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("UNCHECKED a/one.md acme/widgets#1 stated open (gh read failed)", p.stdout)
        self.assertIn("gh read failed for acme/widgets", p.stderr)

    def test_a_clean_store_prints_clean_and_exits_0(self):
        clean = Path(self.tmp.name) / "c"
        (clean / "a").mkdir(parents=True)
        (clean / "a" / "one.md").write_text(doc("One", "See [two](two.md).", domain="a"), encoding="utf-8")
        (clean / "a" / "two.md").write_text(doc("Two", "See [one](one.md).", domain="a"), encoding="utf-8")
        p = self.run_cli("check", "--root", str(clean), "--offline")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "CLEAN"))

    def test_bad_usage_is_exit_2(self):
        for args in (["check", "--root", "/nonexistent-store-dir"], ["bundle", "no-such-domain", "--root", str(self.root)],
                     ["check", "--root", str(self.root), "--offline", "--today", "yesterday"],
                     ["plan", "--root", str(self.root), "--since", "soon"]):
            with self.subTest(args=args):
                p = self.run_cli(*args)
                self.assertEqual(p.returncode, 2, p.stdout)
                self.assertTrue(p.stderr.strip())
                self.assertNotIn("Traceback", p.stderr)
        self.assertEqual(self.run_cli().returncode, 2)  # argparse: no subcommand


class Plan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "store"
        for d in "abcdefgh":
            p = self.root / d / f"{d}.md"
            p.parent.mkdir(parents=True)
            p.write_text(doc(d.upper(), "text", domain=d, updated=f"2026-10-{ord(d) - 96:02d}"), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, **kw):
        docs, _ = ctx_lint.load_docs(self.root)
        args = {"since": "", "unread": set(), "max_forks": 6, "sonnet_over": 60 * 1024, **kw}
        return ctx_lint.plan(docs, args["since"], args["unread"], args["max_forks"], args["sonnet_over"])

    def test_no_stamp_means_every_domain_with_the_fork_cap_and_the_rest_not_read(self):
        out = self.plan()
        self.assertEqual([l.rsplit(" ", 2)[0].split()[0] for l in out], ["FORK"] * 6 + ["NOT"] * 2)
        self.assertTrue(all(l.startswith("NOT READ ") for l in out[6:]))
        self.assertEqual([re.match(r"(?:FORK|NOT READ) (\w+) ", l).group(1) for l in out], list("hgfedcba"))  # newest first

    def test_only_domains_changed_since_the_stamp(self):
        out = self.plan(since="2026-10-06")
        self.assertEqual([l.split()[1] for l in out], ["h", "g", "f"])

    def test_the_stamps_not_read_domains_come_back(self):
        out = self.plan(since="2026-10-08", unread={"a"})
        self.assertEqual({l.split()[1] for l in out}, {"a", "h"})

    def test_nothing_changed(self):
        self.assertEqual(self.plan(since="2026-12-01"), ["NOTHING CHANGED since 2026-12-01"])

    def test_haiku_by_default_sonnet_only_past_the_size(self):
        big = self.root / "b" / "big.md"
        big.write_text(doc("Big", "x" * (61 * 1024), domain="b", updated="2026-10-30"), encoding="utf-8")
        out = {l.split()[1]: l.split()[2] for l in self.plan(since="2026-10-01", max_forks=8)}
        self.assertEqual(out["b"], "sonnet")
        self.assertEqual(out["a"], "haiku")
        self.assertEqual(self.plan(since="2026-10-01", sonnet_over=10 ** 9)[0].split()[2], "haiku")

    def test_the_default_stamp_is_a_doc_new_sh_can_scaffold(self):
        """the session step creates the stamp with `make new TYPE=log DOMAIN=kit-health SLUG=<stem>`; new.sh refuses a
        slug outside [a-z0-9._-], so the default name must pass it and land exactly where `plan` reads it."""
        from tests import hermetic_env
        stamp = ctx_lint.stamp_file(self.root)
        env = hermetic_env(self.tmp.name)
        env.update(CONTEXT_ROOT=str(self.root), TYPE="log", DOMAIN="kit-health", SLUG=stamp.stem)
        r = subprocess.run(["sh", str(BIN / "new.sh")], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(stamp.is_file(), r.stdout)

    def test_the_stamp_file_is_read_never_written(self):
        stamp = self.root / "kit-health" / "lint-x.md"
        stamp.parent.mkdir()
        stamp.write_text("---\ntitle: lint stamp\ntype: log\ndomain: kit-health\nstatus: reference\nupdated: 2026-10-10\n"
                         "last_lint: 2026-10-10T08:00Z\nnot_read: [a, b]\n---\n", encoding="utf-8")
        before = stamp.read_text(encoding="utf-8")
        self.assertEqual(ctx_lint.read_stamp(stamp), ("2026-10-10", {"a", "b"}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ctx_lint.main(["plan", "--root", str(self.root), "--stamp-file", str(stamp)]), 0)
        self.assertEqual(sorted(l.split()[1] for l in out.getvalue().splitlines()), ["a", "b"])
        self.assertEqual(stamp.read_text(encoding="utf-8"), before)

    def test_a_missing_or_malformed_stamp_reads_as_never_linted(self):
        self.assertEqual(ctx_lint.read_stamp(self.root / "nope.md"), ("", set()))
        bad = self.root / "bad.md"
        bad.write_text("---\nlast_lint: sometime\n---\n", encoding="utf-8")
        self.assertEqual(ctx_lint.read_stamp(bad), ("", set()))

    def test_bundle_holds_live_docs_only(self):
        (self.root / "a" / "old.md").write_text(doc("Old", "gone", status="archived", domain="a"), encoding="utf-8")
        (self.root / "a" / "log.md").write_text(doc("Log", "history", type_="log", domain="a"), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ctx_lint.main(["bundle", "a", "--root", str(self.root)]), 0)
        self.assertIn("=== a/a.md\n", out.getvalue())
        self.assertNotIn("old.md", out.getvalue())
        self.assertNotIn("log.md", out.getvalue())


class ReadOnly(unittest.TestCase):
    def test_check_plan_and_bundle_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture(Path(tmp) / "store")
            before = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(io.StringIO()):
                ctx_lint.main(["check", "--root", str(root), "--offline", "--today", TODAY])
                ctx_lint.main(["plan", "--root", str(root)])
                ctx_lint.main(["bundle", "proj", "--root", str(root)])
            after = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
