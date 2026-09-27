#!/usr/bin/env python3
"""transcripts.py — the one reader of Claude Code session transcripts (`~/.claude/projects/<project>/*.jsonl`).

A transcript is JSONL; every API request the session made appears as an `assistant` line whose
`message.usage` carries the token counts. A streamed request is written as several assistant lines
under the same request id (`requestId`, else `message.id`) as the response grows, and only the
last of them carries the final `output_tokens` — so usage is de-duplicated per request id by
keeping the LAST line seen, not the first. `session_stats.py` (one session's stats) and the
cost-report skill (every transcript in a window) both read usage this way — this module is the
shared path, so the two never drift.

  usage_records(path, seen)   → (record, message, usage, request_id) per new API request in one file
  tokens(usage)               → (input, cache_write, cache_read, output)
  subagent_dir(path)          → <project>/<session-id>/subagents/ — the session's Agent/fork children
  subagent_files(path)        → their transcripts, sorted
  find_transcript(session_id) → the session's transcript path, or None
  all_transcripts()           → every main and subagent transcript under ~/.claude/projects
Stdlib only.
"""
from __future__ import annotations
import glob
import json
import os
from collections.abc import Iterator


def tokens(u: dict) -> tuple[int, int, int, int]:
    """(input, cache_write, cache_read, output) from a usage block; a missing field counts 0."""
    return (u.get("input_tokens") or 0, u.get("cache_creation_input_tokens") or 0,
            u.get("cache_read_input_tokens") or 0, u.get("output_tokens") or 0)


def usage_records(path: str, seen: set[str] | None = None) -> Iterator[tuple[dict, dict, dict, str]]:
    """One record per distinct request id in `path` — its LAST assistant line, so a streamed
    response's final `output_tokens` wins over the smaller counts its earlier chunks carried.
    Requests already in `seen` are skipped (`seen` is updated, so one set de-duplicates across
    files). Unreadable files and unparsable lines are skipped."""
    seen = set() if seen is None else seen
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    order: list[str] = []
    last: dict[str, tuple[dict, dict, dict]] = {}
    with fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") != "assistant":
                continue
            m = o.get("message") or {}
            u = m.get("usage") or {}
            rid = o.get("requestId") or m.get("id")
            if not u or not rid:
                continue
            if rid not in last:
                order.append(rid)
            last[rid] = (o, m, u)  # later lines for the same rid overwrite: last one wins
    for rid in order:
        if rid in seen:
            continue
        seen.add(rid)
        o, m, u = last[rid]
        yield o, m, u, rid


def subagent_dir(path: str) -> str:
    """<project>/<session-id>/subagents/ — one JSONL per Agent/fork child of this session."""
    return os.path.join(os.path.dirname(path), os.path.splitext(os.path.basename(path))[0], "subagents")


def subagent_files(path: str) -> list[str]:
    return sorted(glob.glob(os.path.join(subagent_dir(path), "*.jsonl")))


def projects_dir(home: str | None = None) -> str:
    return os.path.join(home or os.path.expanduser("~"), ".claude", "projects")


def find_transcript(session_id: str, home: str | None = None) -> str | None:
    hits = glob.glob(os.path.join(projects_dir(home), "*", f"{session_id}.jsonl"))
    return hits[0] if hits else None


def all_transcripts(home: str | None = None) -> list[str]:
    """Every main-session and subagent transcript on this machine, sorted."""
    base = projects_dir(home)
    files = glob.glob(os.path.join(base, "*", "*.jsonl")) + glob.glob(os.path.join(base, "*", "*", "subagents", "*.jsonl"))
    return sorted(set(files))
