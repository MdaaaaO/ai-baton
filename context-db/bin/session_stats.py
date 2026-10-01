#!/usr/bin/env python3
"""session_stats.py — "stats for geeks" about ONE Claude Code session, from its transcript.

Reads the session's JSONL transcript (~/.claude/projects/<project>/<session-id>.jsonl — every
API request's `usage` block lives there) and derives cost + activity figures with no model
turn at all: turns, cached-prefix tokens, output tokens, peak/avg context, a rough list-price
spend, tool-call mix, compactions, subagents, and what the session did to the outside world
(PRs touched/opened, tickets touched/created/commented/transitioned, sign jobs enqueued,
Slack drafts/sends, pr-watch monitors armed).

Used by session.py (registry `stats:` field on every register/touch/end, `## Session stats`
block + ledger row on `end`) and by heartbeat.sh (so a LIVE session's row carries current
figures). Also a CLI:

    session_stats.py [--session-id <uuid>] [--format line|block|json]

The session id defaults to $CLAUDE_CODE_SESSION_ID (set inside every Claude Code session and
inherited by the detached heartbeat). Missing id or transcript => exit 3, empty output; callers
treat that as "no stats", never as an error.

Spend is a LIST-PRICE ESTIMATE priced per API request by the model that served it: Sonnet
3/3.75/0.3/15/6, Haiku 1/1.25/0.1/5/2, everything else (Opus, Fable/Mythos) at the Opus-class default
15/18.75/1.5/75/30 $/Mtok (in/cache_write_5m/cache_read/out/cache_write_1h) — a cache write against
the 1-hour TTL (`cache_creation.ephemeral_1h_input_tokens`) is billed at its own, higher rate, not
the 5-minute one. Override the default with SESSION_STATS_PRICES="in,cw,cr,out[,cw_1h]"; the 1h
rate defaults to 2x the input price when the list has only 4 numbers. Subagents are billed too: every Agent/fork child writes its
own transcript under <project>/<session-id>/subagents/*.jsonl, and those are summed into
`subagents_cost` and `spend_total_usd_est` (main + subagents). Before 2026-09-22 the figure was
main-session only; this session's review-runners alone cost ~3.4x the main prefix, so the total
is the number to quote. Discounts/batch are ignored. Stdlib only.
"""
from __future__ import annotations
import argparse
import functools
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

import kit_profile as profile  # same dir — tracker regex, MCP tool names, tz default from the active profile
import transcripts  # same dir — the shared transcript reader (usage de-dup, subagent dirs)

# Transcript timestamps are UTC; the "Window" row is rendered in the owner's local
# zone so a session's stats block reads against their wall clock.
LOCAL_TZ, LOCAL_TZ_NOTE = profile.zone()  # UTC + a note on the Window row when WORKSPACE_TZ is unknown (one stderr line, in kit_profile)


# One definition of "session spend", quoted verbatim wherever a number is shown (SESSION_INDEX.md's
# footer, session-handoff, session-register): the row is the TOTAL (main + subagents) — what the
# ledger and cost-report already use; the per-session block breaks the two back out.
SPEND_BASIS = "TOTAL — main session + every subagent transcript, list-price estimate"

DEFAULT_PRICES = (15.0, 18.75, 1.5, 75.0, 30.0)  # $/Mtok: input, cache write (5m TTL), cache read, output, cache write (1h TTL)
# (model-id substring, prices) — first match wins; anything else (opus, fable, mythos, unknown) = default
MODEL_PRICES = (
    ("haiku", (1.0, 1.25, 0.1, 5.0, 2.0)),
    ("sonnet", (3.0, 3.75, 0.3, 15.0, 6.0)),
)


def prices() -> tuple[float, float, float, float, float]:
    raw = os.environ.get("SESSION_STATS_PRICES", "")
    if raw:
        try:
            p = tuple(float(x) for x in raw.split(","))
            if len(p) == 5:
                return p  # type: ignore[return-value]
            if len(p) == 4:
                return p + (p[0] * 2,)  # 1h cache-write rate not given: 2x input, same ratio as the default table
        except ValueError:
            pass
    return DEFAULT_PRICES


def price_for(model: str | None) -> tuple[float, float, float, float, float]:
    m = (model or "").lower()
    for key, p in MODEL_PRICES:
        if key in m:
            return p
    return prices()


def _cost(u: dict, model: str | None) -> float:
    """Cache writes are split by TTL: `cache_creation.ephemeral_1h_input_tokens` costs the 1h rate,
    everything else in `cache_creation_input_tokens` costs the 5m rate — a transcript with no TTL
    breakdown (older format) prices its whole cache-write total at the 5m rate, as before."""
    p_in, p_cw, p_cr, p_out, p_cw1h = price_for(model)
    cc = u.get("cache_creation") or {}
    if cc:
        cw_5m = cc.get("ephemeral_5m_input_tokens") or 0
        cw_1h = cc.get("ephemeral_1h_input_tokens") or 0
    else:
        cw_5m = u.get("cache_creation_input_tokens") or 0
        cw_1h = 0
    return ((u.get("input_tokens") or 0) * p_in + cw_5m * p_cw + cw_1h * p_cw1h
            + (u.get("cache_read_input_tokens") or 0) * p_cr + (u.get("output_tokens") or 0) * p_out) / 1e6


def collect_subagents(path: str) -> dict:
    """Sum usage over every subagent transcript of the session (deduped per API request, priced
    per model). Cheap: usage lines only, no tool-call parsing."""
    files = transcripts.subagent_files(path)
    seen: set[str] = set()
    tok = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    models: Counter = Counter()
    spend = 0.0
    turns = 0
    for fp in files:
        for _o, m, u, _rid in transcripts.usage_records(fp, seen):
            turns += 1
            i, cw, cr, out = transcripts.tokens(u)
            tok["input"] += i
            tok["cache_write"] += cw
            tok["cache_read"] += cr
            tok["output"] += out
            spend += _cost(u, m.get("model"))
            if m.get("model"):
                models[m["model"]] += 1
    return {"files": len(files), "turns": turns, "tokens": tok, "spend_usd_est": round(spend, 2), "models": dict(models)}


def find_transcript(session_id: str) -> str | None:
    return transcripts.find_transcript(session_id)


def _ts(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# Ticket keys look different per tracker (Jira `KEY-123` vs GitHub `#123`); the env config's
# config carries the regex (one capture group). No regex → nothing counts as a ticket.
_NEVER = re.compile(r"(?!x)x")


@functools.lru_cache(maxsize=1)
def ticket_re() -> re.Pattern:
    """`tracker.key_regex`, compiled on first use — never at import: a bad regex in the store must not take every
    registry command (session-register / touch / end, the heartbeat) down with it. It is reported once on stderr and
    no ticket is counted; kit-verify names the bad regex too."""
    rx = profile.get("tracker.key_regex") or ""
    if not rx:
        return _NEVER
    try:
        if not isinstance(rx, str):  # config-set takes any JSON value: `true`, a number, a list
            raise re.error(f"not a string but {type(rx).__name__} {rx!r}")
        return re.compile(rx)
    except re.error as e:
        print(f"session_stats: tracker.key_regex invalid ({e}) — tickets are not counted until it is fixed "
              f"(`kb.py config-set tracker.key_regex …`)", file=sys.stderr)
        return _NEVER


def _ticket_key(k: str) -> tuple:
    """Natural sort for any tracker: `KEY-123` → ('KEY', 123); `456` / `#456` → ('', 456)."""
    m = re.match(r"^#?([A-Za-z]*)-?(\d+)$", k)
    return (m.group(1), int(m.group(2))) if m else (k, 0)
# MCP tool-name suffixes that count as tracker writes (create / comment / transition); Jira only today.
_T = profile.get("tracker.mcp_tools") or {}
TRACKER_CREATE, TRACKER_COMMENT, TRACKER_TRANSITION = (_T.get(k) or None for k in ("create", "comment", "transition"))
PR_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
# One anchored regex for every `gh` write verb: `-X POST` variants, `gh pr` and `gh issue` writes.
# `(?<![\w-])` (not `^`/`[;&|]`) so it matches after `&&`, inside `$(...)`, after a line continuation
# or a tab/newline between subcommand and verb — anywhere `gh` starts a fresh word — while still
# refusing a false hit inside a longer word (e.g. "weigh pr create"). `pr_verb` names the PR verb so
# `prs_opened` (a PR actually *opened*) can share this one match instead of a second regex.
# `api\b[^;|&]*?-X` (not `api\s+(?:-X\s+)?`) so an endpoint or other flags between `api` and the
# method flag don't hide it — `gh api repos/o/r/issues -f title=x -X POST`, the form GitHub CLI's
# own docs use, not just `gh api -X POST <endpoint>` — while `;`/`|`/`&` and a newline (unless the line ends in `\`,
# a continuation) still stop the match at a
# real command boundary, so a POST in an unrelated command chained after a read-only `gh api` call
# isn't miscounted as its write.
GH_WRITE_RE = re.compile(
    r"(?<![\w-])gh\s+(?:api\b(?:[^;|&\n]|\\\n)*?-X\s+(?:POST|PATCH|PUT|DELETE)"
    r"|pr\s+(?P<pr_verb>create|merge|review|comment|edit|ready|close|reopen)"
    r"|issue\s+(?:create|comment|edit|close|reopen))\b"
)
# a real enqueue: `enqueue.sh <topic> /abs/worktree …` — not `cat enqueue.sh` or a mention in a comment
ENQUEUE_RE = re.compile(r"enqueue\.sh\s+[a-z0-9][A-Za-z0-9._-]*\s+[/$\"']")

# --- session-activity: what the session DID, derived from the same transcript, at flush time -------------
# Identifiers only (paths, PR/issue numbers, command verbs) — never the rest of a command or any tool
# result, so the leak scan never has a second surface to cover (#53). Each regex below anchors on the verb
# itself, the same `(?<![\w-])` guard GH_WRITE_RE uses, and captures a trailing number only where the verb
# always carries one (merge/comment/close take the item as an argument; create does not — a freshly created
# PR/ticket's number is not yet known from the command that made it, so it is reported without one).
ACTIVITY_LIMIT = 40  # at most this many output lines, headers included (Done #53)
PR_CREATE_RE = re.compile(r"(?<![\w-])gh\s+pr\s+create\b")
PR_CREATE_API_RE = re.compile(r"(?<![\w-])gh\s+api\b(?:[^;|&\n]|\\\n)*?/pulls(?:[/?\s]|$)(?:[^;|&\n]|\\\n)*?-X\s+POST\b")
PR_MERGE_RE = re.compile(r"pr-merge\.sh\s+\S+\s+(\d+)")
PR_COMMENT_RE = re.compile(r"(?<![\w-])gh\s+pr\s+comment\s+(\d+)")
ISSUE_CREATE_RE = re.compile(r"(?<![\w-])gh\s+issue\s+create\b")
ISSUE_COMMENT_RE = re.compile(r"(?<![\w-])gh\s+issue\s+comment\s+(\d+)")
ISSUE_CLOSE_RE = re.compile(r"(?<![\w-])gh\s+issue\s+close\s+(\d+)")


def _nearest_repo(path: str) -> tuple[str, str]:
    """(repo name, path relative to it) for a file path touched by a Write/Edit/NotebookEdit call — the
    nearest ancestor directory carrying a `.git` entry (a plain repo's directory, or a worktree's `.git`
    FILE), its basename standing in for the repo. No such ancestor (a scratch file, a path outside any
    checkout): ("?", the path as given) rather than guessing."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return os.path.basename(d) or d, os.path.relpath(os.path.abspath(path), d)
        parent = os.path.dirname(d)
        if parent == d:
            return "?", path
        d = parent


def collect_activity(path: str) -> list[dict]:
    """What the session did, one pass over the raw transcript records (`transcripts.records` — the same
    reader `session_retro.py` walks; no second parser). Returns an ordered list of non-empty groups
    ({"header", "count", "lines"}) in the fixed order files / commits+pushes / PRs / tickets / drafts /
    compactions — a header with no matching activity is simply absent (Done #53)."""
    files: list[tuple[str, str]] = []
    seen_files: set[tuple[str, str]] = set()
    commits: list[str] = []
    prs: list[str] = []
    tickets: list[str] = []
    drafts = 0
    compactions: list[str] = []

    for o in transcripts.records(path):
        if o.get("isCompactSummary"):
            ts = o.get("timestamp")
            compactions.append(profile.local_str(ts, LOCAL_TZ) if ts else "?")
            continue
        if o.get("type") != "assistant":
            continue
        for b in (o.get("message") or {}).get("content") or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name = b.get("name", "")
            inp = b.get("input") or {}
            if name in ("Write", "Edit"):
                p = inp.get("file_path")
                if p:
                    key = _nearest_repo(p)
                    if key not in seen_files:
                        seen_files.add(key); files.append(key)
            elif name == "NotebookEdit":
                p = inp.get("notebook_path")
                if p:
                    key = _nearest_repo(p)
                    if key not in seen_files:
                        seen_files.add(key); files.append(key)
            elif name == "Bash":
                cmd = (inp.get("command") or "").strip()
                if cmd.startswith("git commit"):
                    commits.append("commit")
                elif cmd.startswith("git push"):
                    commits.append("push")
                if PR_CREATE_RE.search(cmd) or PR_CREATE_API_RE.search(cmd):
                    prs.append("created")
                m = PR_MERGE_RE.search(cmd)
                if m:
                    prs.append(f"merged #{m.group(1)}")
                m = PR_COMMENT_RE.search(cmd)
                if m:
                    prs.append(f"commented #{m.group(1)}")
                if ISSUE_CREATE_RE.search(cmd):
                    tickets.append("created")
                m = ISSUE_COMMENT_RE.search(cmd)
                if m:
                    tickets.append(f"commented #{m.group(1)}")
                m = ISSUE_CLOSE_RE.search(cmd)
                if m:
                    tickets.append(f"closed #{m.group(1)}")
            elif name.endswith("slack_send_message_draft"):
                drafts += 1

    groups = []
    if files:
        groups.append({"header": "Files edited", "count": len(files), "lines": [f"{r}: {p}" for r, p in files]})
    if commits:
        groups.append({"header": "Commits & pushes", "count": len(commits), "lines": list(commits)})
    if prs:
        groups.append({"header": "PRs", "count": len(prs), "lines": list(prs)})
    if tickets:
        groups.append({"header": "Tickets", "count": len(tickets), "lines": list(tickets)})
    if drafts:
        groups.append({"header": "Drafts", "count": drafts, "lines": []})
    if compactions:
        groups.append({"header": "Compactions", "count": len(compactions), "lines": list(compactions)})
    return groups


def fmt_activity(groups: list[dict], limit: int = ACTIVITY_LIMIT) -> list[str]:
    """Render `collect_activity`'s groups as `header (count)` + `- line` bullets, at most `limit` lines
    total. Over the limit: the header counts stay exact (the true total), and the single group
    contributing the most bullet lines is cut short with one `… (+N)` line standing in for the rest —
    never the other groups, and never a silent drop with no `(+N)` marker."""
    total = sum(1 + len(g["lines"]) for g in groups)
    over = total - limit
    if over > 0 and groups:
        gi = max(range(len(groups)), key=lambda i: len(groups[i]["lines"]))
        lines = groups[gi]["lines"]
        n = len(lines)
        keep = max(0, n - over - 1)
        hidden = n - keep
        if hidden > 0:
            groups[gi] = {**groups[gi], "lines": lines[:keep] + [f"… (+{hidden})"]}
    out = []
    for g in groups:
        out.append(f"{g['header']} ({g['count']})")
        out.extend(f"- {ln}" for ln in g["lines"])
    return out


def activity_for(session_id: str | None) -> list[dict] | None:
    """`collect_activity` for this session's transcript (session_id, else $CLAUDE_CODE_SESSION_ID); None
    when no id or no transcript is found — the caller decides how to say so."""
    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not sid:
        return None
    path = find_transcript(sid)
    if not path:
        return None
    try:
        return collect_activity(path)
    except OSError as e:
        print(f"session_stats: transcript {path} unreadable ({e}) — no activity this time", file=sys.stderr)
        return None


def no_transcript_line(session_id: str | None) -> str:
    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    return f"session-activity: no transcript for {sid or 'unknown'} — flush from memory"


def collect(path: str) -> dict:
    """One pass over the transcript for turns/tools/timestamps/usage: a streamed request's usage
    is written on several assistant lines under the same request id, and only the LAST of them
    carries the real `output_tokens` (an earlier chunk always undercounts it), so each new line for
    an already-seen request id backs out that request's previous (smaller) contribution to the
    running totals before adding its own — the running totals always reflect the latest line seen
    for every request, without a second read of the file (a second read would race a transcript
    that's still growing, e.g. heartbeat.sh reading a live session: a request appended between the
    two reads would have its tool_use counted from the second but its usage missing from the
    first)."""
    contrib: dict[str, tuple[int, int, int, int, int, float, int]] = {}  # rid -> (i, cw, cr, out, think, cost, ctx) last added to the running totals
    n_turns = 0
    tok_in = tok_cw = tok_cr = tok_out = tok_think = 0
    peak_ctx = 0
    ctx_sum = 0
    tools: Counter = Counter()
    models: Counter = Counter()
    compactions = 0
    api_errors = 0
    prompts = 0
    first_ts = last_ts = None
    prs_touched: set[tuple[str, str]] = set()
    prs_opened = 0
    tickets: set[str] = set()
    jira_created = jira_comments = jira_transitions = 0
    sign_jobs = 0
    slack_drafts = slack_sends = 0
    monitors = 0
    gh_writes = 0
    subagents = 0
    spend = 0.0

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            t = o.get("type")
            ts = _ts(o.get("timestamp"))
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            if o.get("isCompactSummary"):
                compactions += 1
            if o.get("isApiErrorMessage"):
                api_errors += 1
            if t == "pr-link":
                if o.get("prRepository") and o.get("prNumber"):
                    prs_touched.add((str(o["prRepository"]), str(o["prNumber"])))
                continue
            m = o.get("message") or {}
            if t == "user":
                c = m.get("content")
                text = c if isinstance(c, str) else " ".join(
                    b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
                ) if isinstance(c, list) else ""
                has_result = isinstance(c, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in c
                )
                if text and not has_result and not o.get("isMeta") and not text.lstrip().startswith(("<", "[SYSTEM")):
                    prompts += 1
                continue
            if t != "assistant":
                continue
            rid = o.get("requestId") or m.get("id")
            u = m.get("usage") or {}
            if rid and u:
                i, cw, cr, out = transcripts.tokens(u)
                think = (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0
                cost = _cost(u, m.get("model"))
                ctx = i + cw + cr
                if rid in contrib:
                    pi, pcw, pcr, pout, pthink, pcost, pctx = contrib[rid]
                    tok_in -= pi; tok_cw -= pcw; tok_cr -= pcr; tok_out -= pout
                    tok_think -= pthink; spend -= pcost; ctx_sum -= pctx
                else:
                    n_turns += 1
                    if m.get("model"):
                        models[m["model"]] += 1
                contrib[rid] = (i, cw, cr, out, think, cost, ctx)
                tok_in += i; tok_cw += cw; tok_cr += cr; tok_out += out
                tok_think += think; spend += cost
                ctx_sum += ctx
                peak_ctx = max(peak_ctx, ctx)
            for b in m.get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name = b.get("name", "?")
                tools[name] += 1
                inp = b.get("input") or {}
                blob = json.dumps(inp, ensure_ascii=False)
                for repo, num in PR_RE.findall(blob):
                    prs_touched.add((repo, num))
                tickets.update(ticket_re().findall(blob))
                if name == "Agent":
                    subagents += 1
                elif name == "Monitor":
                    if "pr-watch" in inp.get("command", ""):
                        monitors += 1
                elif name == "Bash":
                    cmd = inp.get("command", "")
                    if ENQUEUE_RE.search(cmd):
                        sign_jobs += 1
                    gw = GH_WRITE_RE.search(cmd)
                    if gw:
                        gh_writes += 1
                        if gw.group("pr_verb") == "create":
                            prs_opened += 1
                elif TRACKER_CREATE and name.endswith(TRACKER_CREATE):
                    jira_created += 1
                elif TRACKER_COMMENT and name.endswith(TRACKER_COMMENT):
                    jira_comments += 1
                elif TRACKER_TRANSITION and name.endswith(TRACKER_TRANSITION):
                    jira_transitions += 1
                elif name.endswith("slack_send_message_draft"):
                    slack_drafts += 1
                elif name.endswith("slack_send_message"):
                    slack_sends += 1

    p_in, p_cw, p_cr, p_out, p_cw1h = prices()
    sub = collect_subagents(path)
    hours = ((last_ts - first_ts).total_seconds() / 3600.0) if first_ts and last_ts else 0.0
    return {
        "session_id": os.path.splitext(os.path.basename(path))[0],
        "transcript": path,
        "started": first_ts.strftime("%Y-%m-%dT%H:%M:%SZ") if first_ts else "",
        "last": last_ts.strftime("%Y-%m-%dT%H:%M:%SZ") if last_ts else "",
        "wall_hours": round(hours, 1),
        "turns": n_turns,
        "prompts": prompts,
        "tokens": {"input": tok_in, "cache_write": tok_cw, "cache_read": tok_cr,
                   "output": tok_out, "thinking": tok_think},
        "context": {"peak": peak_ctx, "avg": int(ctx_sum / n_turns) if n_turns else 0},
        "spend_usd_est": round(spend, 2),
        "subagents_cost": sub,
        "spend_total_usd_est": round(spend + sub["spend_usd_est"], 2),
        "prices_per_mtok": {"input": p_in, "cache_write": p_cw, "cache_read": p_cr, "output": p_out, "cache_write_1h": p_cw1h},
        "compactions": compactions,
        "api_errors": api_errors,
        "models": dict(models),
        "tool_calls": sum(tools.values()),
        "tools_top": [(n.replace("mcp__claude_ai_", ""), c) for n, c in tools.most_common(8)],
        "subagents": subagents,
        "monitors_armed": monitors,
        "prs_touched": sorted(prs_touched),
        "prs_opened": prs_opened,
        "gh_writes": gh_writes,
        "tickets_touched": sorted(tickets, key=_ticket_key),
        "jira": {"created": jira_created, "comments": jira_comments, "transitions": jira_transitions},
        "sign_jobs": sign_jobs,
        "slack": {"drafts": slack_drafts, "sends": slack_sends},
    }


def _k(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.0f}k"
    return str(n)


def fmt_line(s: dict) -> str:
    """One registry-cell line (no `|`, no newlines)."""
    tk = s["tokens"]
    return (f"{s['turns']} turns · {s['wall_hours']}h · ctx peak {_k(s['context']['peak'])} avg {_k(s['context']['avg'])} · "
            f"cache-read {_k(tk['cache_read'])} · out {_k(tk['output'])} · "
            f"~${s['spend_total_usd_est']:.0f} (main {s['spend_usd_est']:.0f} + {s['subagents_cost']['files']} subagents {s['subagents_cost']['spend_usd_est']:.0f}) · "
            f"{s['compactions']} compactions · {s['tool_calls']} tool calls · "
            f"{len(s['prs_touched'])} PRs ({s['prs_opened']} opened) · {len(s['tickets_touched'])} tickets "
            f"({s['jira']['created']} created, {s['jira']['comments']} comments, {s['jira']['transitions']} transitions) · "
            f"{s['sign_jobs']} sign jobs · {s['slack']['drafts']} Slack drafts")


def fmt_block(s: dict) -> str:
    """Markdown table for a session file / context doc."""
    tk, ctx, j, p, sub = s["tokens"], s["context"], s["jira"], s["prices_per_mtok"], s["subagents_cost"]
    stk = sub["tokens"]
    sub_models = ", ".join(f"{m.replace('claude-', '')} {c}" for m, c in sorted(sub["models"].items(), key=lambda x: -x[1])) or "—"
    prs = ", ".join(f"{r.split('/')[-1]}#{n}" for r, n in s["prs_touched"]) or "—"
    tickets = ", ".join(s["tickets_touched"]) or "—"
    tools = ", ".join(f"{n} {c}" for n, c in s["tools_top"]) or "—"
    rows = [
        ("Window", f"{profile.local_str(s['started'], LOCAL_TZ)} → {profile.local_str(s['last'], LOCAL_TZ)} ({s['wall_hours']}h){LOCAL_TZ_NOTE}"),
        ("Turns / prompts", f"{s['turns']} API turns · {s['prompts']} user prompts · {s['compactions']} compactions · {s['api_errors']} API errors"),
        ("Context", f"peak {_k(ctx['peak'])} · avg prefix {_k(ctx['avg'])}"),
        ("Tokens", f"cache-read {_k(tk['cache_read'])} · cache-write {_k(tk['cache_write'])} · uncached in {_k(tk['input'])} · out {_k(tk['output'])} (thinking {_k(tk['thinking'])})"),
        ("Rough spend", f"~${s['spend_total_usd_est']:.2f} TOTAL = main ~${s['spend_usd_est']:.2f} + subagents ~${sub['spend_usd_est']:.2f} — list price per model (opus-class default ${p['input']}/{p['cache_write']}/{p['cache_read']}/{p['output']} per Mtok in/cache-write/cache-read/out; sonnet 3/3.75/0.3/15; haiku 1/1.25/0.1/5)"),
        ("Subagents", f"{sub['files']} transcripts · {sub['turns']} API turns · cache-read {_k(stk['cache_read'])} · cache-write {_k(stk['cache_write'])} · out {_k(stk['output'])} · models: {sub_models}"),
        ("Tool calls", f"{s['tool_calls']} — {tools}"),
        ("Delegation", f"{s['subagents']} Agent calls · {s['monitors_armed']} pr-watch Monitor arms (incl. 30-min re-arms)"),
        ("PRs", f"{len(s['prs_touched'])} referenced · {s['prs_opened']} `gh pr create` calls · {s['gh_writes']} GitHub writes: {prs}"),
        ("Tickets", f"{len(s['tickets_touched'])} referenced · {j['created']} created · {j['comments']} comments · {j['transitions']} transitions: {tickets}"),
        ("Hand-offs", f"{s['sign_jobs']} sign-queue jobs · {s['slack']['drafts']} Slack drafts · {s['slack']['sends']} Slack sends"),
    ]
    out = ["| Stat | Value |", "|---|---|"]
    out += [f"| {k} | {v.replace('|', '/')} |" for k, v in rows]
    return "\n".join(out)


def stats_for(session_id: str | None) -> dict | None:
    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not sid:
        return None
    path = find_transcript(sid)
    if not path:
        return None
    try:
        return collect(path)
    except OSError as e:  # not "0 turns": say why there are no stats, then report none
        print(f"session_stats: transcript {path} unreadable ({e}) — no stats this time", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session-id", default="")
    ap.add_argument("--format", choices=("line", "block", "json"), default="block")
    ap.add_argument("--activity", action="store_true",
                    help="print the grouped session-activity block (files/commits/PRs/tickets/drafts/compactions) instead of the stats block")
    a = ap.parse_args()
    if a.activity:
        groups = activity_for(a.session_id or None)
        if groups is None:
            print(no_transcript_line(a.session_id or None))
            return 0
        for line in fmt_activity(groups):
            print(line)
        return 0
    s = stats_for(a.session_id or None)
    if s is None:
        print("session_stats: no session id / transcript found (set CLAUDE_CODE_SESSION_ID or --session-id)", file=sys.stderr)
        return 3
    if a.format == "json":
        print(json.dumps(s, indent=2))
    elif a.format == "line":
        print(fmt_line(s))
    else:
        print(fmt_block(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
