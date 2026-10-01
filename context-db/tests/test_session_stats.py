"""session_stats.py + transcripts.py on a synthetic transcript (usage de-dup, tool mix, PRs, subagents, pricing,
the rendered line/block). Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import session_stats as ss  # noqa: E402
import transcripts  # noqa: E402


def usage(i=1000, cw=0, cr=0, out=100, cw_1h=None):
    """`cw_1h`, when given, splits the `cw` cache-write tokens into a 1h-TTL part and a 5m-TTL
    remainder, mirroring the real API's `cache_creation` breakdown (whose two fields sum to the
    top-level `cache_creation_input_tokens`)."""
    u = {"input_tokens": i, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr, "output_tokens": out}
    if cw_1h is not None:
        u["cache_creation"] = {"ephemeral_5m_input_tokens": cw - cw_1h, "ephemeral_1h_input_tokens": cw_1h}
    return u


def write_transcript(dirpath: Path, name: str, lines: list) -> Path:
    """A standalone `<name>.jsonl` under `dirpath` (no subagents dir) — for tests that need their
    own isolated transcript rather than the shared `LINES` fixture."""
    path = dirpath / f"{name}.jsonl"
    path.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    return path


def assistant(rid: str, ts: str, content=None, model="claude-opus-x", u=None):
    return {"type": "assistant", "requestId": rid, "timestamp": ts,
            "message": {"id": f"msg_{rid}", "model": model, "usage": u or usage(), "content": content or [{"type": "text", "text": "ok"}]}}


def tool(name: str, **inp):
    return {"type": "tool_use", "name": name, "input": inp}


LINES = [
    {"type": "user", "timestamp": "2026-09-26T10:00:00Z", "message": {"content": "start the work"}},
    assistant("r1", "2026-09-26T10:00:05Z", [tool("Bash", command="gh pr create --title x"), tool("Read", file_path="a")]),
    assistant("r1", "2026-09-26T10:00:05Z"),  # the same request written twice: one turn, not two
    {"type": "user", "timestamp": "2026-09-26T10:01:00Z", "message": {"content": [{"type": "tool_result", "content": "done"}]}},
    assistant("r2", "2026-09-26T10:30:00Z", [tool("Agent", prompt="x"), tool("Bash", command="see https://github.com/acme/repo/pull/42")],
              model="claude-sonnet-x", u=usage(i=0, cr=10_000, cw=2_000, out=50)),
    {"type": "pr-link", "prRepository": "acme/repo", "prNumber": 7, "timestamp": "2026-09-26T10:31:00Z"},
    {"type": "assistant", "isCompactSummary": True, "timestamp": "2026-09-26T11:00:00Z", "message": {}},
    "this line is not json",
    {"type": "user", "timestamp": "2026-09-26T12:00:00Z", "message": {"content": "<system-reminder>ignored</system-reminder>"}},
]


class Transcript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        proj = Path(self.tmp.name) / ".claude" / "projects" / "-home-x"
        proj.mkdir(parents=True)
        self.path = proj / "sess-1.jsonl"
        self.path.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in LINES) + "\n", encoding="utf-8")
        sub = proj / "sess-1" / "subagents"
        sub.mkdir(parents=True)
        # the same request written twice (a multi-content-block message splits into several identical-usage
        # lines) — one turn, not two, whichever of the two lines de-dup keeps.
        (sub / "agent-a.jsonl").write_text(json.dumps(assistant("s1", "2026-09-26T10:31:00Z", model="claude-haiku-x", u=usage(i=1_000_000))) + "\n"
                                           + json.dumps(assistant("s1", "2026-09-26T10:31:00Z", model="claude-haiku-x", u=usage(i=1_000_000))) + "\n",
                                           encoding="utf-8")
        (sub / "agent-b.jsonl").write_text("garbage\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_usage_records_dedupe_across_files_and_skip_junk(self):
        seen: set[str] = set()
        recs = list(transcripts.usage_records(str(self.path), seen))
        self.assertEqual([r[3] for r in recs], ["r1", "r2"])
        self.assertEqual(transcripts.tokens(recs[1][2]), (0, 2_000, 10_000, 50))
        self.assertEqual(list(transcripts.usage_records(str(self.path), seen)), [])  # already seen
        self.assertEqual(list(transcripts.usage_records("/nonexistent.jsonl")), [])
        self.assertEqual(len(transcripts.subagent_files(str(self.path))), 2)
        home = self.tmp.name
        self.assertEqual(transcripts.find_transcript("sess-1", home), str(self.path))
        self.assertIsNone(transcripts.find_transcript("nope", home))
        self.assertEqual(len(transcripts.all_transcripts(home)), 3)

    def test_collect(self):
        s = ss.collect(str(self.path))
        self.assertEqual(s["turns"], 2)
        self.assertEqual(s["prompts"], 1)  # the tool_result and the <system-reminder> lines are not prompts
        self.assertEqual(s["tokens"], {"input": 1000, "cache_write": 2000, "cache_read": 10000, "output": 150, "thinking": 0})
        self.assertEqual(s["context"], {"peak": 12000, "avg": 6500})
        self.assertEqual(s["compactions"], 1)
        self.assertEqual(s["tool_calls"], 4)
        self.assertEqual(dict(s["tools_top"])["Bash"], 2)
        self.assertEqual(s["subagents"], 1)
        self.assertEqual(s["prs_opened"], 1)
        self.assertEqual(s["prs_touched"], [("acme/repo", "42"), ("acme/repo", "7")])
        self.assertEqual(s["models"], {"claude-opus-x": 1, "claude-sonnet-x": 1})
        self.assertEqual(s["wall_hours"], 2.0)  # 10:00 → 12:00 (the last timestamped line, whatever its type)
        # spend: opus 1000 in + 100 out = 0.015 + 0.0075; sonnet 2000 cw + 10000 cr + 50 out = 0.0075 + 0.003 + 0.00075
        self.assertAlmostEqual(s["spend_usd_est"], 0.03, places=2)
        sub = s["subagents_cost"]
        self.assertEqual((sub["files"], sub["turns"]), (2, 1))
        self.assertEqual(sub["models"], {"claude-haiku-x": 1})
        self.assertAlmostEqual(sub["spend_usd_est"], 1.0 + 0.0005, places=3)  # haiku: 1M in at $1 + 100 out
        self.assertAlmostEqual(s["spend_total_usd_est"], s["spend_usd_est"] + sub["spend_usd_est"], places=2)

    def test_render_line_and_block(self):
        s = ss.collect(str(self.path))
        line = ss.fmt_line(s)
        self.assertNotIn("|", line)
        self.assertNotIn("\n", line)
        self.assertIn("2 turns · 2.0h · ctx peak 12k avg 6k", line)
        self.assertIn("2 PRs (1 opened)", line)
        block = ss.fmt_block(s)
        self.assertIn("| Stat | Value |", block)
        self.assertIn("| Subagents | 2 transcripts · 1 API turns", block)

    def test_prices(self):
        self.assertEqual(ss.price_for("claude-sonnet-4"), (3.0, 3.75, 0.3, 15.0, 6.0))
        self.assertEqual(ss.price_for("claude-haiku-4"), (1.0, 1.25, 0.1, 5.0, 2.0))
        self.assertEqual(ss.price_for("claude-opus-4"), ss.DEFAULT_PRICES)
        self.assertEqual(ss.price_for(None), ss.DEFAULT_PRICES)
        os.environ["SESSION_STATS_PRICES"] = "1,2,3,4,9"
        try:
            self.assertEqual(ss.prices(), (1.0, 2.0, 3.0, 4.0, 9.0))
            self.assertEqual(ss.price_for("mythos"), (1.0, 2.0, 3.0, 4.0, 9.0))
            # a 4-number override (the pre-1h-TTL format) still works: the 1h rate defaults to 2x input
            os.environ["SESSION_STATS_PRICES"] = "1,2,3,4"
            self.assertEqual(ss.prices(), (1.0, 2.0, 3.0, 4.0, 2.0))
            os.environ["SESSION_STATS_PRICES"] = "bad"
            self.assertEqual(ss.prices(), ss.DEFAULT_PRICES)
        finally:
            del os.environ["SESSION_STATS_PRICES"]

    def test_cache_write_1h_priced_separately_from_5m(self):
        """A cache write against the 1-hour TTL must cost the 1h rate, not the 5-minute one — the bug
        (#133) billed every cache write at the 5m rate regardless of `cache_creation`'s TTL breakdown."""
        # opus-class default: in 15, cache-write-5m 18.75, cache-write-1h 30 ($/Mtok)
        u_5m_only = usage(i=0, cw=1_000_000, cr=0, out=0)  # no cache_creation breakdown: legacy shape
        self.assertAlmostEqual(ss._cost(u_5m_only, "claude-opus-x"), 18.75, places=6)
        u_1h_only = usage(i=0, cw=1_000_000, cr=0, out=0, cw_1h=1_000_000)  # all of it against the 1h TTL
        self.assertAlmostEqual(ss._cost(u_1h_only, "claude-opus-x"), 30.0, places=6)
        u_split = usage(i=0, cw=1_000_000, cr=0, out=0, cw_1h=400_000)  # 600k at 5m + 400k at 1h
        self.assertAlmostEqual(ss._cost(u_split, "claude-opus-x"), 0.6 * 18.75 + 0.4 * 30.0, places=6)

    def test_usage_records_last_wins_on_streamed_chunks(self):
        """A streamed request writes several assistant lines under one requestId as the response
        grows; only the LAST carries the final output_tokens (#133 — the first used to win, so
        output tokens were undercounted for every streamed turn)."""
        with tempfile.TemporaryDirectory() as d:
            path = write_transcript(Path(d), "stream", [
                assistant("rs", "2026-09-26T09:00:00Z", u=usage(i=500, out=20)),
                assistant("rs", "2026-09-26T09:00:01Z", u=usage(i=500, out=55)),
                assistant("rs", "2026-09-26T09:00:02Z", u=usage(i=500, out=90)),
            ])
            recs = list(transcripts.usage_records(str(path)))
            self.assertEqual([r[3] for r in recs], ["rs"])
            self.assertEqual(transcripts.tokens(recs[0][2]), (500, 0, 0, 90))

    def test_collect_streamed_usage_takes_last_chunk(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_transcript(Path(d), "stream2", [
                {"type": "user", "timestamp": "2026-09-26T09:00:00Z", "message": {"content": "go"}},
                assistant("rs2", "2026-09-26T09:00:00Z", u=usage(i=500, out=20)),
                assistant("rs2", "2026-09-26T09:00:01Z", u=usage(i=500, out=55)),
                assistant("rs2", "2026-09-26T09:00:02Z", u=usage(i=500, out=90)),
            ])
            s = ss.collect(str(path))
            self.assertEqual(s["turns"], 1)  # one API request, however many chunks it streamed as
            self.assertEqual(s["tokens"]["output"], 90)  # the final chunk's count, not the first or the sum

    def test_collect_streamed_chunks_do_not_double_count_running_totals(self):
        """Each new line for an already-seen request id must back out that request's previous
        contribution before adding the new one — summing every chunk instead (the bug a naive
        single-pass rewrite could reintroduce) would inflate tokens/spend/context by chunk count."""
        with tempfile.TemporaryDirectory() as d:
            path = write_transcript(Path(d), "stream3", [
                assistant("rs3", "2026-09-26T09:00:00Z", u=usage(i=200, out=10)),
                assistant("rs3", "2026-09-26T09:00:01Z", u=usage(i=200, out=40)),
            ])
            s = ss.collect(str(path))
            self.assertEqual(s["turns"], 1)
            self.assertEqual(s["tokens"], {"input": 200, "cache_write": 0, "cache_read": 0, "output": 40, "thinking": 0})
            self.assertEqual(s["context"], {"peak": 200, "avg": 200})  # not 400: the first chunk's ctx was backed out

    def test_split_hint_fires_over_threshold_with_a_full_window(self):
        """#398: the hint fires once the avg context over the configured window reaches the
        configured threshold, with a full window of turns behind it."""
        with tempfile.TemporaryDirectory() as d:
            path = write_transcript(Path(d), "split-over", [
                assistant("h1", "2026-09-26T09:00:00Z", u=usage(i=200_000, out=10)),
                assistant("h2", "2026-09-26T09:05:00Z", u=usage(i=200_000, out=10)),
            ])
            with unittest.mock.patch.dict(os.environ, {"SESSION_STATS_SPLIT_WINDOW": "2",
                                                         "SESSION_STATS_SPLIT_THRESHOLD": "100000"}):
                s = ss.collect(str(path))
                self.assertEqual(s["recent_prefix"], {"avg": 200_000, "window": 2, "turns": 2, "threshold": 100_000})
                hint = ss.split_hint(s)
                self.assertIsNotNone(hint)
                self.assertIn("split hint", hint)
                self.assertIn("200k", hint)
                self.assertIn(hint, ss.fmt_line(s))
                self.assertIn(hint, ss.fmt_block(s))

    def test_split_hint_absent_under_threshold(self):
        """A light prefix over the same full window never gets the hint."""
        with tempfile.TemporaryDirectory() as d:
            path = write_transcript(Path(d), "split-under", [
                assistant("l1", "2026-09-26T09:00:00Z", u=usage(i=1_000, out=10)),
                assistant("l2", "2026-09-26T09:05:00Z", u=usage(i=1_000, out=10)),
            ])
            with unittest.mock.patch.dict(os.environ, {"SESSION_STATS_SPLIT_WINDOW": "2",
                                                         "SESSION_STATS_SPLIT_THRESHOLD": "100000"}):
                s = ss.collect(str(path))
                self.assertIsNone(ss.split_hint(s))
                self.assertNotIn("split hint", ss.fmt_line(s))
                self.assertNotIn("split hint", ss.fmt_block(s))

    def test_split_hint_absent_before_a_full_window(self):
        """A fat prefix that hasn't lasted a full window yet does not fire — one busy turn is not
        a trend. Window 5, only 2 turns recorded, both already over threshold."""
        with tempfile.TemporaryDirectory() as d:
            path = write_transcript(Path(d), "split-short", [
                assistant("w1", "2026-09-26T09:00:00Z", u=usage(i=200_000, out=10)),
                assistant("w2", "2026-09-26T09:05:00Z", u=usage(i=200_000, out=10)),
            ])
            with unittest.mock.patch.dict(os.environ, {"SESSION_STATS_SPLIT_WINDOW": "5",
                                                         "SESSION_STATS_SPLIT_THRESHOLD": "100000"}):
                s = ss.collect(str(path))
                self.assertEqual(s["recent_prefix"]["turns"], 2)
                self.assertIsNone(ss.split_hint(s))

    def test_split_threshold_and_window_env_overrides(self):
        with unittest.mock.patch.dict(os.environ, {"SESSION_STATS_SPLIT_THRESHOLD": "9000", "SESSION_STATS_SPLIT_WINDOW": "7"}):
            self.assertEqual(ss.split_threshold(), 9000)
            self.assertEqual(ss.split_window(), 7)
        with unittest.mock.patch.dict(os.environ, {"SESSION_STATS_SPLIT_THRESHOLD": "not-a-number"}):
            self.assertEqual(ss.split_threshold(), ss.SPLIT_THRESHOLD_DEFAULT)
        self.assertEqual(ss.split_window(), ss.SPLIT_WINDOW_DEFAULT)

    def test_collect_reads_transcript_in_one_pass(self):
        """collect() must open the transcript exactly once (#133 — the previous fix read it twice:
        `transcripts.usage_records(path)` up front to get each request's last usage line, then a
        second `with open(path)` loop for turns/tools; a request appended between the two reads had
        its tool_use counted from the second read but was absent from the first read's usage snapshot,
        so heartbeat.sh — which runs this against a still-growing transcript — silently dropped that
        turn's tokens/spend from the count). A single open means no such window exists."""
        opens = []
        real_open = open

        def counting_open(file, *a, **k):
            if str(file) == str(self.path):
                opens.append(1)
            return real_open(file, *a, **k)

        with unittest.mock.patch("builtins.open", counting_open):
            ss.collect(str(self.path))
        self.assertEqual(len(opens), 1)

    def test_gh_write_regex_table(self):
        """One anchored regex covers every `gh` write verb (#133 — the old GH_WRITE_RE/PR_CREATE_RE
        pair missed `gh issue`, a subshell, and a wrapped line, while also false-hitting a word that
        merely ends in "gh")."""
        cases = [
            ("gh api \\\n  -X POST /repos/o/r/issues", True, False),   # -X on a continuation line (`\` + newline)
            ("gh api repos/o/r/issues\ncurl -X POST http://x", False, False),  # a newline ends the gh command
            ('$(gh pr create --title "x")', True, True),            # inside a subshell
            ("gh issue comment 5 --body hi", True, False),          # `gh issue` was not covered at all
            ("gh pr view 12", False, False),                        # read-only: not a write
            ("weigh pr create everything", False, False),           # "gh" ending a longer word: no false hit
            ("gh pr ready 12 --undo", True, False),               # ready / close / reopen change a PR: writes
            ("gh pr close 12", True, False),
            # #133 — the endpoint (or other flags) between `api` and `-X POST`, the form GitHub CLI's
            # own docs use, must still count as a write.
            ("gh api repos/o/r/issues -f title=x -X POST", True, False),
            # a `-X POST` in an unrelated command chained after a read-only `gh api` call must not
            # be miscounted as that call's write.
            ("gh api repos/o/r/issues; curl -X POST http://example.com", False, False),
        ]
        for cmd, want_write, want_open in cases:
            with self.subTest(cmd=cmd):
                m = ss.GH_WRITE_RE.search(cmd)
                self.assertEqual(m is not None, want_write)
                if m:
                    self.assertEqual(m.group("pr_verb") == "create", want_open)

    def test_git_commit_push_regex_table(self):
        """GIT_COMMIT_RE / GIT_PUSH_RE are segment-anchored (after `^`, `&&`, `;`, `||`, `|`), not
        `cmd.startswith(...)` — so a chained `cd <dir> && git commit` or a `git -C <dir> commit` still
        counts, same as the plain form (#53 nit); `echo git commit …` or `git status` must not."""
        cases = [
            ("git commit -m 'msg'", True, False),
            ("cd /tmp/work && git commit -m 'msg'", True, False),
            ("git -C /tmp/work commit -m 'msg'", True, False),
            ("git push -u origin feat/x", False, True),
            ("cd /tmp/work && git push -u origin feat/x", False, True),
            ("git -C /tmp/work push -u origin feat/x", False, True),
            ("echo git commit is great", False, False),      # "git commit" as plain text, not invoked
            ("git status", False, False),
            ("gh pr create --body \"$(cat <<'EOF'\nfirst line\ngit commit is not run here\nEOF\n)\"", False, False),  # a body line, not a segment
        ]
        for cmd, want_commit, want_push in cases:
            with self.subTest(cmd=cmd):
                self.assertEqual(ss.GIT_COMMIT_RE.search(cmd) is not None, want_commit)
                self.assertEqual(ss.GIT_PUSH_RE.search(cmd) is not None, want_push)

    def test_k_and_stats_for(self):
        self.assertEqual([ss._k(n) for n in (5, 1500, 2_500_000)], ["5", "2k", "2.5M"])
        saved = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)  # the test may itself run inside a Claude session
        try:
            self.assertIsNone(ss.stats_for(""))
            self.assertIsNone(ss.stats_for("no-such-session"))
        finally:
            if saved is not None:
                os.environ["CLAUDE_CODE_SESSION_ID"] = saved


class Activity(unittest.TestCase):
    """session_stats.py --activity / collect_activity + fmt_activity: what a session did, grouped
    (files/commits/PRs/tickets/drafts/compactions), derived from the transcript at flush time — never
    hooks, never a staging file, never command arguments or tool output (#53)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo_a = base / "repo-a"
        self.repo_b = base / "repo-b"
        (self.repo_a / ".git").mkdir(parents=True)
        (self.repo_b / ".git").mkdir(parents=True)
        self.proj = base / ".claude" / "projects" / "-home-x"
        self.proj.mkdir(parents=True)
        self.secret = "zzz-not-a-real-argument-should-never-appear-99887766"

    def tearDown(self):
        self.tmp.cleanup()

    def _path(self, name: str) -> Path:
        return self.proj / f"{name}.jsonl"

    def test_collect_activity_groups_files_commits_prs_tickets_and_compactions(self):
        lines = [
            {"type": "user", "timestamp": "2026-09-28T09:00:00Z", "message": {"content": "go"}},
            assistant("a1", "2026-09-28T09:00:01Z", [
                tool("Edit", file_path=str(self.repo_a / "src" / "one.py"), old_string="x", new_string="y"),
                tool("Edit", file_path=str(self.repo_b / "two.py"), old_string="x", new_string="y"),
                tool("Bash", command="git commit -m 'wip'"),
                tool("Bash", command=f"git push -u origin feat/53-x --token {self.secret}"),
                tool("Bash", command=f"gh pr create --title x --body {self.secret}"),
                tool("Bash", command=f"gh issue comment 9 --body {self.secret}"),
            ]),
            {"type": "assistant", "isCompactSummary": True, "timestamp": "2026-09-28T10:15:00Z", "message": {}},
        ]
        path = write_transcript(self.proj, "sess-act", lines)
        groups = ss.collect_activity(str(path))
        rendered = "\n".join(ss.fmt_activity(groups))
        self.assertNotIn(self.secret, rendered)  # never command arguments, even a secret-shaped one
        self.assertIn("Files edited (2)", rendered)
        self.assertIn(f"- repo-a: {os.path.join('src', 'one.py')}", rendered)
        self.assertIn("- repo-b: two.py", rendered)
        self.assertIn("Commits & pushes (2)", rendered)
        self.assertIn("- commit", rendered)
        self.assertIn("- push", rendered)
        self.assertIn("PRs (1)", rendered)
        self.assertIn("- created", rendered)
        self.assertIn("Tickets (1)", rendered)
        self.assertIn("- commented #9", rendered)
        self.assertIn("Compactions (1)", rendered)
        self.assertLessEqual(len(rendered.splitlines()), ss.ACTIVITY_LIMIT)

    def test_no_transcript_line_and_cli_exit_zero(self):
        self.assertIsNone(ss.activity_for("no-such-session-at-all"))
        self.assertEqual(ss.no_transcript_line("abc123"),
                          "session-activity: no transcript for abc123 — flush from memory")
        r = subprocess.run([sys.executable, str(BIN / "session_stats.py"), "--activity",
                             "--session-id", "no-such-session-at-all"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(),
                          "session-activity: no transcript for no-such-session-at-all — flush from memory")

    def test_over_the_limit_truncates_only_the_longest_group_but_keeps_the_true_count(self):
        edits = [tool("Edit", file_path=str(self.repo_a / f"f{i}.py"), old_string="x", new_string="y")
                 for i in range(50)]
        lines = [assistant("a1", "2026-09-28T09:00:01Z", edits)]
        path = write_transcript(self.proj, "sess-act-big", lines)
        groups = ss.collect_activity(str(path))
        self.assertEqual(groups[0]["count"], 50)
        out = ss.fmt_activity(groups)
        self.assertLessEqual(len(out), ss.ACTIVITY_LIMIT)
        self.assertEqual(out[0], "Files edited (50)")  # the header keeps the true count
        self.assertTrue(any(ln.startswith("- … (+") for ln in out))

    def test_over_the_limit_trims_the_next_largest_group_until_it_fits(self):
        """Five equal groups of 10 lines (55 lines total, headers included) used to cap only the single
        largest group, printing 46 lines even though `limit` is 40 — the trim must keep moving to the
        next-largest group until the total actually fits (#53 nit)."""
        groups = [{"header": f"Group {g}", "count": 10, "lines": [f"g{g}-item{i}" for i in range(10)]}
                  for g in range(5)]
        out = ss.fmt_activity(groups)
        self.assertLessEqual(len(out), ss.ACTIVITY_LIMIT)
        blocks: dict[str, list[str]] = {}
        header = None
        for ln in out:
            if ln.startswith("Group "):
                header = ln
                blocks[header] = []
            else:
                blocks[header].append(ln)
        trimmed_any = False
        for g in range(5):
            header = f"Group {g} (10)"
            self.assertIn(header, blocks)  # the header count stays the true 10, trimmed or not
            lines = blocks[header]
            full = [f"- g{g}-item{i}" for i in range(10)]
            if lines != full:
                trimmed_any = True
                self.assertTrue(lines[-1].startswith("- … (+"))
        self.assertTrue(trimmed_any)

    def test_drafts_are_counted_only_never_detailed(self):
        lines = [assistant("a1", "2026-09-28T09:00:01Z", [
            tool("mcp__slack__slack_send_message_draft", channel="c", text="t"),
            tool("mcp__slack__slack_send_message_draft", channel="c", text="t2"),
        ])]
        path = write_transcript(self.proj, "sess-act-drafts", lines)
        groups = ss.collect_activity(str(path))
        out = ss.fmt_activity(groups)
        self.assertEqual(out, ["Drafts (2)"])


if __name__ == "__main__":
    unittest.main()
