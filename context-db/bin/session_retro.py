#!/usr/bin/env python3
"""session_retro.py — what ONE session did and where it may have slipped, from its transcript (the session-retro skill).

Deterministic extraction, no model call: the `session-retro` fork reads this output and judges each candidate against
WORKSPACE.md § Rules and the skills the session invoked. It extracts
  - every user message, with the assistant turn before it (its last text, the tools it called) and two flags:
    `interrupt` (the user stopped a tool call) and `correction_hint` (the wording reads like a correction);
  - every tool call with its key argument (the command, the file, the skill, the agent and its model);
  - denied or blocked tool calls (a permission prompt answered no, a rule or hook that blocked it);
  - failed commands (any other tool result marked as an error);
and runs the rule checks a script can decide (`rule_hits`), each a CANDIDATE the fork confirms against the rule text:
  pr-no-labels        a `gh pr create` without `--label`, and no label added later in the session
  pr-no-footer        a PR body (inline or a body file the session wrote) without the attribution footer line
  title-style         a PR title or commit subject that `commit_style.py` refuses (the repo's resolved style);
                      a `git commit` under a temp dir or alongside a pytest invocation is fixture setup, not a hit
  agent-no-model      an `Agent` call without `model` (it inherits the main session's, the most expensive one)
  workspace-path      a `.context/`, `.worktrees/` or home-directory path in a title or body posted to GitHub
  store-write-no-root a writing `kb.py` / `session.py` call from a worktree without an explicit `CONTEXT_ROOT=`
  merge-no-registry   a PR merge the session never followed with a registry touch (register / touch / end)
Calls that errored or were denied do not count for the rule checks: they did not happen.

    session_retro.py [--session-id <uuid> | --transcript <path>] [--json]

The session id defaults to $CLAUDE_CODE_SESSION_ID; the transcript is found the way session_stats.py finds it. Exit 0
with the report (text by default, every field with --json — `tool_calls` only there); exit 3, one stderr line and no
output when there is no session id or transcript. Stdlib only.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path

import commit_style  # same dir — the title / subject check pr-open uses
import transcripts  # same dir — the shared transcript reader

TEXT_MAX, PREV_MAX, KEY_MAX, ERR_MAX = 500, 300, 160, 240

CORRECTION_RE = re.compile(
    r"^\s*(?:no|nope|stop|wait|don'?t|do not|never|wrong|undo|revert)\b"
    r"|\b(?:you (?:forgot|missed|skipped|didn'?t|did not|should(?:n'?t)? have|were supposed to)"
    r"|i (?:said|told you|asked(?: you)? (?:for|to|not))|why did you|not what i|that'?s (?:wrong|not)"
    r"|as i said|instead of|please don'?t|again\?)", re.I)
INTERRUPT_RE = re.compile(r"^\[Request interrupted by user")
DENIAL_RE = re.compile(
    r"doesn'?t want to (?:proceed|take this action)|tool use was rejected|permission (?:to use|for) .{0,80}?(?:has been|was|is) denied"
    r"|denied by (?:the )?(?:user|policy|rule|hook)|blocked by (?:a |the )?(?:hook|policy|rule|permission)"
    r"|hook (?:error|blocked)|pretooluse\S* .*?(?:denied|blocked)", re.I | re.S)
FOOTER_RE = re.compile(r"Generated with \[Claude Code\]")
LEAK_RE = re.compile(r"(?<![\w.])\.context/|(?<![\w.])\.worktrees/|(?<![\w])/(?:home|Users)/[^/\s`'\"]+/|(?<![\w/])~/")
TEMP_DIR_RE = re.compile(r"(?:^|/)(?:pytest-of-[^/]+|ai-baton-kit(?:-\d+)?)(?:/|$)")
PYTEST_RUN_RE = re.compile(r"(?:^|[;&|]\s*)(?:\S*/)?(?:python3?\s+-m\s+)?pytest\b")
STORE_WRITES = {"kb.py": ("set", "rm", "config-set", "init", "migrate"), "session.py": ("register", "touch", "end")}
MAKE_WRITES = ("session-register", "session-touch", "session-end")
REGISTRY_SKILLS = ("session-register", "session-handoff")
HEREDOC_WRITE_RE = re.compile(r"cat\s*>\s*(\S+)\s*<<-?\s*['\"]?(\w+)['\"]?\n(.*?)\n\2\b", re.S)
SEP = {"&&", "||", ";", "|", "&", "(", ")", ";;"}


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tail(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else "…" + s[-(n - 1):]


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _when(ts: str | None) -> str:
    """`MM-DD HH:MM` (UTC, as the transcript writes it) — the evidence's time stamp."""
    return (ts or "")[5:16].replace("T", " ")


def _key(name: str, inp: dict) -> str:
    """The one argument that says what a tool call did."""
    if name == "Bash":
        return _clip(inp.get("command", ""), KEY_MAX)
    if name in ("Agent", "Task"):
        return _clip(f"{inp.get('subagent_type') or 'general'} model={inp.get('model') or '-'} {inp.get('description') or ''}", KEY_MAX)
    if name == "Skill":
        return _clip(f"{inp.get('skill', '')} {inp.get('args') or ''}", KEY_MAX)
    for k in ("file_path", "path", "command", "pattern", "url", "query", "skill"):
        if inp.get(k):
            return _clip(inp[k], KEY_MAX)
    return _clip(json.dumps(inp, ensure_ascii=False, sort_keys=True), KEY_MAX)


def _tokens(cmd: str) -> list[str]:
    try:
        lx = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        return list(lx)
    except ValueError:  # unbalanced quotes: no argument-level check for this command
        return []


def _segments(tokens: list[str]) -> list[list[str]]:
    out, cur = [], []
    for t in tokens:
        if t in SEP:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def _opts(args: list[str]) -> dict[str, list[str]]:
    """`--flag value`, `--flag=value` and `-f value` → {flag: [values]}; a flag followed by another flag is boolean."""
    o: dict[str, list[str]] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-") and len(a) > 1:
            if "=" in a and a.startswith("--"):
                k, v = a.split("=", 1)
                o.setdefault(k, []).append(v)
            elif i + 1 < len(args) and not args[i + 1].startswith("-"):
                o.setdefault(a, []).append(args[i + 1])
                i += 1
            else:
                o.setdefault(a, []).append("")
        i += 1
    return o


def _in_temp_dir(path: str) -> bool:
    """True when `path` sits under `$TMPDIR` (default `/tmp`), `$KIT_SCRATCH`, the kit scratch root
    (`ai-baton-kit[-<uid>]`) or a pytest temp dir (`tmp_path` / `tmpdir` land under `pytest-of-<user>/pytest-<n>/…`,
    itself under the system temp root) — a `git commit` there is test-fixture setup, not one the session means to
    ship, so it is not a commit-style candidate."""
    if not path:
        return False
    p = os.path.normpath(path)
    for var, default in (("TMPDIR", "/tmp"), ("KIT_SCRATCH", None)):
        base = os.environ.get(var) or default
        if base:
            base = os.path.normpath(base)
            if p == base or p.startswith(base + os.sep):
                return True
    return bool(TEMP_DIR_RE.search(p))


def _unheredoc(v: str) -> str:
    """`$(cat <<'EOF'\\n…\\nEOF\\n)` (the usual commit / body idiom) → the text between the markers."""
    m = re.match(r"\s*\$\(\s*cat\s*<<-?\s*['\"]?(\w+)['\"]?\n(.*?)\n\s*\1\s*\n?\s*\)\s*$", v, re.S)
    return m.group(2) if m else v


def _gh(seg: list[str]) -> tuple[str, str, list[str]] | None:
    """(`pr`|`issue`|`api`, verb, rest) when the segment runs `gh`."""
    for i, t in enumerate(seg):
        if os.path.basename(t) == "gh" and i + 1 < len(seg):
            if seg[i + 1] == "api":
                return "api", "", seg[i + 2:]
            if seg[i + 1] in ("pr", "issue") and i + 2 < len(seg):
                return seg[i + 1], seg[i + 2], seg[i + 3:]
            return None
    return None


def _store_call(seg: list[str]) -> tuple[str, str] | None:
    """(`kb.py set` / `make session-end` …, the script path or `make -C` dir) when the segment writes the env store or
    the session registry — a mention of the script inside `sed`, `grep` or a heredoc is not a call."""
    toks = [t for t in seg if not re.match(r"^[A-Za-z_]\w*=", t)]  # drop `VAR=value` prefixes
    if toks and toks[0] == "env":
        toks = toks[1:]
    if not toks:
        return None
    if os.path.basename(toks[0]).startswith("python") and len(toks) > 1:
        toks = toks[1:]
    base = os.path.basename(toks[0])
    if base in STORE_WRITES and len(toks) > 1 and toks[1] in STORE_WRITES[base]:
        return f"{base} {toks[1]}", toks[0]
    if base == "make":
        target = next((t for t in toks[1:] if t in MAKE_WRITES), None)
        if target:
            d = toks[toks.index("-C") + 1] if "-C" in toks[:-1] else "."
            return f"make {target}", d
    return None


class Retro:
    def __init__(self, path: str):
        self.path = path
        self.prompts: list[dict] = []
        self.calls: dict[str, dict] = {}  # tool_use_id → call
        self.order: list[dict] = []
        self.denials: list[dict] = []
        self.failures: list[dict] = []
        self.errored: set[str] = set()
        self.skills: list[str] = []
        self.writes: dict[str, str] = {}  # file path → content the session wrote (Write tool, `cat > f <<EOF`)
        self.first = self.last = ""
        self.turn = 0
        self.prev_text = ""
        self.prev_tools: list[str] = []

    # ---- pass 1: the record stream
    def read(self) -> "Retro":
        for o in transcripts.records(self.path):
            ts = o.get("timestamp") or ""
            if ts:
                self.first = self.first or ts
                self.last = ts
            m = o.get("message") or {}
            if o.get("type") == "user" and not o.get("isCompactSummary"):
                self._user(o, m, ts)
            elif o.get("type") == "assistant":
                self._assistant(o, m, ts)
        return self

    def _user(self, o: dict, m: dict, ts: str) -> None:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self._result(b)
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                return
        text = _text(c).strip()
        if not text or o.get("isMeta"):
            return
        cmd = re.search(r"<command-name>\s*/?([\w:.-]+)\s*</command-name>", text)
        if cmd:
            args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
            text = f"/{cmd.group(1)} {args.group(1).strip() if args else ''}".strip()
            self.skills.append(cmd.group(1).split(":")[-1])
        elif text.startswith(("<", "[SYSTEM")):
            return
        self.turn += 1
        interrupt = bool(INTERRUPT_RE.match(text))
        self.prompts.append({
            "turn": self.turn, "time": _when(ts), "text": _clip(text, TEXT_MAX),
            "prev_assistant": _tail(self.prev_text, PREV_MAX), "prev_tools": sorted(set(self.prev_tools)),
            "interrupt": interrupt, "correction_hint": interrupt or bool(CORRECTION_RE.search(text)),
        })
        self.prev_text, self.prev_tools = "", []

    def _assistant(self, o: dict, m: dict, ts: str) -> None:
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                self.prev_text = b["text"]
            elif b.get("type") == "tool_use":
                name, inp = b.get("name", "?"), b.get("input") or {}
                if b.get("id") in self.calls:  # a streamed message written twice: one call
                    continue
                call = {"id": b.get("id") or f"_{len(self.order)}", "turn": self.turn, "time": _when(ts), "tool": name,
                        "key": _key(name, inp), "input": inp, "cwd": o.get("cwd") or ""}
                self.calls[call["id"]] = call
                self.order.append(call)
                self.prev_tools.append(name)
                if name == "Skill" and inp.get("skill"):
                    self.skills.append(str(inp["skill"]).split(":")[-1])
                elif name == "Write" and inp.get("file_path"):
                    self.writes[inp["file_path"]] = inp.get("content") or ""
                elif name == "Bash":
                    for path, _tag, body in HEREDOC_WRITE_RE.findall(inp.get("command", "")):
                        self.writes[path.strip("'\"")] = body

    def _result(self, b: dict) -> None:
        call = self.calls.get(b.get("tool_use_id") or "")
        body = _text(b.get("content")) if not isinstance(b.get("content"), str) else b["content"]
        if not b.get("is_error") or call is None:
            return
        # denial wording counts only on an error result: a successful Read/Grep of text that quotes it is not a denial
        denied = bool(DENIAL_RE.search(body or ""))
        self.errored.add(call["id"])
        row = {"turn": call["turn"], "time": call["time"], "tool": call["tool"], "key": call["key"]}
        if denied:
            self.denials.append({**row, "reason": _clip(body, ERR_MAX)})
        else:
            self.failures.append({**row, "error": _clip(body, ERR_MAX)})

    # ---- pass 2: the rule checks, over the calls that went through
    def checks(self) -> list[dict]:
        hits: list[dict] = []
        ok = [c for c in self.order if c["id"] not in self.errored]
        home_cwd = next((c["cwd"] for c in self.order if c["cwd"]), "")

        def hit(rule: str, c: dict, evidence: str) -> None:
            hits.append({"rule": rule, "turn": c["turn"], "time": c["time"], "tool": c["tool"], "evidence": _clip(evidence, KEY_MAX)})

        def leak(c: dict, where: str, text: str) -> None:
            m = LEAK_RE.search(text or "")
            if m:
                hit("workspace-path", c, f"{where} contains `{m.group(0)}`")
            elif home_cwd and len(home_cwd) > 1 and home_cwd in (text or ""):
                hit("workspace-path", c, f"{where} contains the session's working directory")

        def body_of(o: dict[str, list[str]], inline=("--body", "-b"), files=("--body-file", "-F")) -> str | None:
            for k in inline:
                if o.get(k):
                    return _unheredoc(o[k][-1])
            for k in files:
                if o.get(k):
                    p = o[k][-1]
                    if p in self.writes:
                        return self.writes[p]
                    try:
                        return Path(p).read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        return None
            return None

        def style(cwd: str) -> str:
            try:
                d = Path(cwd) if cwd and Path(cwd).is_dir() else None
                return commit_style.resolve(d)[0]
            except (SystemExit, Exception):  # a broken store must not end the retro: fall back to the kit default
                return "conventional"

        unlabelled: list[dict] = []
        merges: list[dict] = []
        for c in ok:
            inp = c["input"]
            if c["tool"] in ("Agent", "Task") and not inp.get("model"):
                hit("agent-no-model", c, f"Agent `{inp.get('subagent_type') or 'general'}` without model: {inp.get('description') or ''}")
            if c["tool"] == "Skill" and str(inp.get("skill", "")).split(":")[-1] in REGISTRY_SKILLS:
                merges = []
            if "github" in c["tool"].lower() and c["tool"].startswith("mcp__"):
                for k in ("title", "body", "comment"):
                    if isinstance(inp.get(k), str):
                        leak(c, f"{c['tool'].split('__')[-1]} {k}", inp[k])
            if c["tool"] != "Bash":
                continue
            cmd = inp.get("command", "")
            cwd = c["cwd"]
            for seg in _segments(_tokens(cmd)):
                if seg[0] == "cd" and len(seg) > 1:  # later segments of this command run there
                    cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(seg[1])))
                w = _store_call(seg)
                if w:
                    if w[0].startswith(("session", "make")):
                        merges = []
                    loc = w[1]
                    in_kit = re.match(r"\$\{?BATON\}?(?:/|$)", loc)
                    where = loc if os.path.isabs(loc) else os.path.join(cwd, loc)
                    if not in_kit and ".worktrees/" in where + "/" and "CONTEXT_ROOT=" not in cmd:
                        hit("store-write-no-root", c, f"`{w[0]}` run from a worktree without CONTEXT_ROOT= (writes the live store)")
                if any(os.path.basename(t) == "heartbeat.sh" for t in seg[:2]):
                    merges = []
                g = _gh(seg)
                is_merge = (g is not None and (g[:2] == ("pr", "merge") or (g[0] == "api" and any(
                    re.search(r"/pulls/\d+/merge$", t) for t in g[2])))) or (
                    seg and (os.path.basename(seg[0]) == "pr-merge.sh"
                             or (seg[0] in ("bash", "sh") and len(seg) > 1 and os.path.basename(seg[1]) == "pr-merge.sh")))
                if is_merge:
                    merges.append(c)
                if seg and seg[0] == "git" and "commit" in seg:
                    pre, o = seg[1: seg.index("commit")], _opts(seg[seg.index("commit") + 1:])
                    m = o.get("--message") or next((v for k, v in o.items() if re.fullmatch(r"-[a-zA-Z]*m", k)), None)
                    msg = _unheredoc(m[0]) if m else (body_of(o, inline=(), files=("-F", "--file")) or "")
                    subject = msg.strip().splitlines()[0] if msg.strip() else ""
                    wd = pre[pre.index("-C") + 1] if "-C" in pre[:-1] else cwd
                    # a fixture/test-setup commit (temp dir, or the same Bash call also runs pytest) is not a
                    # candidate: it never ships, so the repo's title style does not apply to it
                    fixture = _in_temp_dir(wd) or _in_temp_dir(cwd) or bool(PYTEST_RUN_RE.search(cmd))
                    if subject and "$" not in subject and not fixture:  # an unexpanded variable: the real subject is unknown
                        probs = commit_style.check_subject(subject, style(wd))
                        if probs:
                            hit("title-style", c, f"commit subject `{subject}`: {probs[0]}")
                if g is None:
                    continue
                kind, verb, rest = g
                o = _opts(rest)
                if kind == "api":
                    fields = [v for k in ("-f", "-F", "--field", "--raw-field") for v in o.get(k, [])]
                    if any(re.search(r"/(?:issues|pulls)/\d+/labels/?$", p) for p in rest if not p.startswith("-")):
                        unlabelled = []  # labels added to an issue/PR (pr-open's way); a label list or create does not count
                    for v in fields:
                        k, _, val = v.partition("=")
                        if k in ("body", "title"):
                            if val.startswith("@"):
                                val = self.writes.get(val[1:], "")
                            leak(c, f"gh api {k}", val)
                    continue
                if verb == "edit" and (o.get("--add-label") or o.get("--label")):
                    unlabelled = []
                if verb not in ("create", "comment", "edit", "review"):
                    continue
                title = (o.get("--title") or o.get("-t") or [""])[-1]
                body = body_of(o)
                leak(c, f"gh {kind} {verb} title", title)
                leak(c, f"gh {kind} {verb} body", body or "")
                if kind == "pr" and verb in ("create", "edit") and title and "$" not in title:
                    probs = commit_style.check_subject(title, style(c["cwd"]))
                    if probs:
                        hit("title-style", c, f"PR title `{title}`: {probs[0]}")
                if kind == "pr" and verb == "create":
                    if not (o.get("--label") or o.get("-l")):
                        unlabelled.append(c)
                    if body is not None and not FOOTER_RE.search(body):
                        hit("pr-no-footer", c, "PR body has no `Generated with [Claude Code]` footer line")
        for c in unlabelled:
            hit("pr-no-labels", c, "gh pr create without --label and no label added later in the session")
        for c in merges:
            hit("merge-no-registry", c, "PR merged; no registry touch (session.py register/touch/end) afterwards")
        hits.sort(key=lambda h: (h["turn"], h["time"]))
        return hits

    def report(self) -> dict:
        hits = self.checks()
        return {
            "session_id": os.path.splitext(os.path.basename(self.path))[0],
            "transcript": self.path,
            "started": self.first, "last": self.last,
            "counts": {"prompts": len(self.prompts), "tool_calls": len(self.order), "denials": len(self.denials),
                       "failures": len(self.failures), "corrections": sum(p["correction_hint"] for p in self.prompts),
                       "rule_hits": len(hits)},
            "skills_invoked": sorted(set(self.skills)),
            "user_messages": self.prompts,
            "denials": self.denials,
            "failures": self.failures,
            "rule_hits": hits,
            "tool_calls": [{k: c[k] for k in ("turn", "time", "tool", "key")} for c in self.order],
        }


def fmt_text(r: dict) -> str:
    n = r["counts"]
    out = [f"session {r['session_id']} · {r['started'][:16]} → {r['last'][:16]} UTC · {n['prompts']} prompts · "
           f"{n['tool_calls']} tool calls · {n['denials']} denials · {n['failures']} failures · "
           f"{n['corrections']} correction hints · {n['rule_hits']} rule hits",
           "skills invoked: " + (", ".join(r["skills_invoked"]) or "none"), "", "## rule hits (candidates)"]
    out += [f"- t{h['turn']} {h['time']} {h['rule']} · {h['evidence']}" for h in r["rule_hits"]] or ["- none"]
    out += ["", "## denials"]
    out += [f"- t{d['turn']} {d['time']} {d['tool']} `{d['key']}` → {d['reason']}" for d in r["denials"]] or ["- none"]
    out += ["", "## failures"]
    out += [f"- t{f['turn']} {f['time']} {f['tool']} `{f['key']}` → {f['error']}" for f in r["failures"]] or ["- none"]
    out += ["", "## user messages (* = correction hint; prev = the assistant turn before)"]
    for p in r["user_messages"]:
        out.append(f"- t{p['turn']} {p['time']}{' *' if p['correction_hint'] else ''} {p['text']}")
        if p["correction_hint"] and (p["prev_assistant"] or p["prev_tools"]):
            out.append(f"    prev: {p['prev_assistant'] or '-'} [tools: {', '.join(p['prev_tools']) or '-'}]")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="session_retro.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--session-id", default="", help="the session to read (default $CLAUDE_CODE_SESSION_ID)")
    g.add_argument("--transcript", default="", help="a transcript .jsonl path instead of a session id")
    ap.add_argument("--json", action="store_true", help="every field as JSON, tool_calls included")
    a = ap.parse_args(argv)
    path = a.transcript
    if not path:
        sid = a.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        path = transcripts.find_transcript(sid) if sid else ""
    if not path or not os.path.isfile(path):
        print("session_retro: no session id / transcript found (set CLAUDE_CODE_SESSION_ID, --session-id or --transcript)", file=sys.stderr)
        return 3
    try:
        r = Retro(path).read().report()
    except OSError as e:
        print(f"session_retro: transcript unreadable ({e})", file=sys.stderr)
        return 3
    print(json.dumps(r, indent=1, ensure_ascii=False) if a.json else fmt_text(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
