#!/usr/bin/env python3
"""eval_check.py — the static, token-free check of the eval suite under evals/ (#54).

`claude plugin eval` spends tokens; this spends none, so it runs on every PR (`make -C $BATON/context-db ci`). It
reads the eval dir the plugin manifest names (`experimental.evals`, else `evals/`) and fails on:

  - a case the runner would refuse to load: no `prompt.md` (or `case.yaml`), an empty prompt body, a prompt
    frontmatter key the runner does not know, no `graders/*.md`, a grader without a known `type`, an `llm` grader
    without criteria, a `tool_used` grader without a `tool`;
  - a trigger case (tag `trigger`) whose directory names no skill (`evals/<skill>-<case>/`), that is not tagged
    exactly one of `positive` / `near-miss`, or whose `tool_used: Skill` grader does not name the skill, is not scored
    under ablation (`arm: both`), or points the wrong way (a positive needs `min` >= 1, a near miss `min: 0` + `max: 0`);
  - a skill that HAS a trigger suite with fewer than MIN_CASES cases or fewer than MIN_EACH positives or near misses.

Skills without a suite yet, and descriptions without a "Use when" phrase, are printed as notes — not failures —
until every skill has one (`--require-all` turns both into failures; the eventual gate). Stdlib only; frontmatter
is read with the kit's one parser (`frontmatter.py`).

Usage: eval_check.py [--require-all] [--kit DIR]   exit 0 = OK, 1 = a failure above.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frontmatter  # noqa: E402

KIT = Path(__file__).resolve().parents[2]
# docs/authoring.md § 3 and evals/README.md: >= MIN_CASES trigger cases per skill, of which at least MIN_EACH positives
# and MIN_EACH near misses. EVAL_MIN_CASES / EVAL_MIN_EACH lower them while a new suite is being built up.
MIN_CASES = int(os.environ.get("EVAL_MIN_CASES") or 10)
MIN_EACH = int(os.environ.get("EVAL_MIN_EACH") or 3)
INT_KEYS = ("runs", "max_turns", "timeout_seconds", "schema_version")
# The keys `claude plugin eval` accepts in a prompt.md frontmatter (an unknown key fails the case load) and the grader
# types it knows — as the pinned CLI (ci.yml CLAUDE_CODE_VERSION) reports them.
PROMPT_KEYS = frozenset({
    "schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome",
    "model", "max_turns", "timeout_seconds", "allowed_tools", "artifact_publish", "growthbook_overrides",
    "append_system_prompt", "env",
})
GRADER_TYPES = frozenset({"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"})
KINDS = ("positive", "near-miss")
USE_WHEN = re.compile(r"\bUse when(?:ever)?\b")
SKIP_DIRS = frozenset({"results", "mocks"})


def eval_dir(kit: Path) -> Path:
    """The manifest's `experimental.evals` (a directory relative to the plugin root), else `evals/`."""
    try:
        manifest = json.loads((kit / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        rel = (manifest.get("experimental") or {}).get("evals")
    except (OSError, ValueError, AttributeError):
        rel = None
    return kit / (rel if isinstance(rel, str) and rel.strip() else "evals")


def skill_names(kit: Path) -> list[str]:
    return sorted(p.parent.name for p in kit.glob("skills/*/SKILL.md"))


def skill_of(case: str, skills: list[str]) -> str | None:
    """The skill a case directory belongs to: the longest skill name `<skill>-` prefixes it with."""
    hits = [s for s in skills if case.startswith(s + "-")]
    return max(hits, key=len) if hits else None


def as_int(raw, default: int | None) -> int | None:
    v = frontmatter.unquote(raw) if raw is not None else ""
    return int(v) if v.strip().lstrip("-").isdigit() else default


def read_md(p: Path) -> tuple[dict, str]:
    """(raw frontmatter mapping, body) — an empty mapping when the file has no block."""
    text = p.read_text(encoding="utf-8", errors="replace")
    parts = frontmatter.split(text)
    if parts is None:
        return {}, text
    return frontmatter.parse_lines(parts[0]), parts[1]


def names_skill(input_match: str, skill: str) -> str:
    """'' when a Skill grader's `input_match` regex selects exactly `skill`, else why not. It must match the skill as the
    Skill tool is called (`<skill>`, `<plugin>:<skill>`) and must NOT match a longer name that contains it — a substring
    test (`skill in input_match`) let `pr-open` pass for `pr-open-helper` and `session-handoff` inside another name."""
    try:
        rx = re.compile(input_match)
    except re.error as e:
        return f"`input_match` is not a valid regex ({e})"
    if not any(rx.search(c) for c in (skill, f"ai-baton:{skill}", f'"skill": "{skill}"')):
        return f"`input_match` {input_match!r} does not match the skill name {skill!r}"
    decoys = [d for d in (f"{skill}-helper", f"my{skill}", f"{skill}_x", f"x-{skill}") if rx.search(d)]
    if decoys:
        return (f"`input_match` {input_match!r} also matches {', '.join(repr(d) for d in decoys)} — bound it, "
                f"e.g. '(?<![\\w-]){re.escape(skill)}(?![\\w-])'")
    return ""


def check_case(case_dir: Path, skills: list[str]) -> tuple[list[str], dict | None]:
    """(errors, trigger info {skill, kind} or None) for one case directory."""
    name = case_dir.name
    errors: list[str] = []
    prompt = case_dir / "prompt.md"
    if not prompt.is_file():
        if (case_dir / "case.yaml").is_file():
            return [], None  # a case.yaml case: the runner validates its schema; nothing here reads YAML
        return [f"{name}: no prompt.md (or case.yaml)"], None
    fm, body = read_md(prompt)
    unknown = sorted(k for k in fm if k not in PROMPT_KEYS)
    if unknown:
        errors.append(f"{name}/prompt.md: frontmatter key(s) the runner rejects: {', '.join(unknown)}")
    if not body.strip():
        errors.append(f"{name}/prompt.md: empty prompt body")
    for k in INT_KEYS:
        if k in fm and as_int(fm.get(k), None) is None:
            errors.append(f"{name}/prompt.md: `{k}` must be a whole number, got {frontmatter.unquote(fm.get(k))!r}")
    # frontmatter.parse_lines never yields a Python list (only str/dict — frontmatter.py's docstring), so a real
    # inline list reads back as the raw string `[a, b]`; anything else (a block mapping, a bare scalar) is invalid.
    if "allowed_tools" in fm and not str(fm.get("allowed_tools")).strip().startswith("["):
        errors.append(f"{name}/prompt.md: `allowed_tools` must be a list")
    graders = sorted((case_dir / "graders").glob("*.md"))
    if not graders:
        errors.append(f"{name}: no graders/*.md")
    parsed = []
    for g in graders:
        gfm, gbody = read_md(g)
        gtype = frontmatter.unquote(gfm.get("type"))
        if gtype not in GRADER_TYPES:
            errors.append(f"{name}/graders/{g.name}: type {gtype or '(missing)'!r} is not one of {', '.join(sorted(GRADER_TYPES))}")
        elif gtype == "llm" and not gbody.strip():
            errors.append(f"{name}/graders/{g.name}: an llm grader needs its criteria as the body")
        elif gtype == "tool_used" and not frontmatter.unquote(gfm.get("tool")):
            errors.append(f"{name}/graders/{g.name}: a tool_used grader needs `tool:`")
        parsed.append((g.name, gtype, gfm))

    tags = frontmatter.parse_csv(fm.get("tags", ""))
    if "trigger" not in tags:
        return errors, None
    skill = skill_of(name, skills)
    kinds = [k for k in KINDS if k in tags]
    if skill is None:
        errors.append(f"{name}: a trigger case must be named evals/<skill>-<case>/ after an existing skill")
    if len(kinds) != 1:
        errors.append(f"{name}: a trigger case is tagged exactly one of {' / '.join(KINDS)} (has: {', '.join(kinds) or 'neither'})")
    if skill is None or len(kinds) != 1:
        return errors, None
    kind = kinds[0]
    skill_graders = [(gn, gfm) for gn, gt, gfm in parsed if gt == "tool_used" and frontmatter.unquote(gfm.get("tool")) == "Skill"]
    fired = []
    for gn, gfm in skill_graders:
        why = names_skill(frontmatter.unquote(gfm.get("input_match")) or "", skill)
        if why:
            errors.append(f"{name}/graders/{gn}: {why}")
        else:
            fired.append((gn, gfm))
    if not fired:
        errors.append(f"{name}: no `tool_used` grader with `tool: Skill` whose `input_match` names {skill}")
    for gn, gfm in fired:
        where = f"{name}/graders/{gn}"
        if frontmatter.unquote(gfm.get("arm")) != "both":
            errors.append(f"{where}: set `arm: both` — without it a Skill grader is display-only under ablation and never scored")
        lo, hi = as_int(gfm.get("min"), 1), as_int(gfm.get("max"), None)
        if kind == "positive" and ((lo or 0) < 1 or hi == 0):
            errors.append(f"{where}: a positive case must require the skill (`min` >= 1, no `max: 0`)")
        if kind == "near-miss" and not (lo == 0 and hi == 0):
            errors.append(f"{where}: a near miss must forbid the skill (`min: 0` and `max: 0`)")
    if not any(gt in ("llm", "regex", "file_exists", "baseline") for _, gt, _ in parsed):
        errors.append(f"{name}: a trigger case also needs an outcome grader (llm, regex, …), not only the Skill check")
    return errors, {"skill": skill, "kind": kind}


def check(kit: Path, require_all: bool = False) -> tuple[list[str], list[str], dict[str, dict[str, int]]]:
    """(errors, notes, per-skill counts {skill: {positive: n, near-miss: n}})."""
    skills = skill_names(kit)
    root = eval_dir(kit)
    errors: list[str] = []
    notes: list[str] = []
    suites: dict[str, dict[str, int]] = {}
    other: dict[str, int] = {}
    if not root.is_dir():
        errors.append(f"{root.relative_to(kit)}/: the eval dir does not exist")
        return errors, notes, suites
    for case_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".")):
        errs, trig = check_case(case_dir, skills)
        errors += errs
        if trig:
            suites.setdefault(trig["skill"], {k: 0 for k in KINDS})[trig["kind"]] += 1
        elif not errs:
            prefix = skill_of(case_dir.name, skills) or "-".join(case_dir.name.split("-")[:2])
            other[prefix] = other.get(prefix, 0) + 1
    for skill, n in sorted(suites.items()):
        total = sum(n.values())
        if total < MIN_CASES or any(n[k] < MIN_EACH for k in KINDS):
            errors.append(f"{skill}: {total} trigger case(s) ({n['positive']} positive, {n['near-miss']} near miss) — "
                          f"a suite needs >= {MIN_CASES}, with >= {MIN_EACH} of each")
    missing = [s for s in skills if s not in suites]
    if missing:
        (errors if require_all else notes).append(f"no trigger suite yet ({len(missing)}): {', '.join(missing)}")
    no_use_when = []
    for s in skills:
        fm = frontmatter.load(kit / "skills" / s / "SKILL.md") or {}
        if not USE_WHEN.search(frontmatter.unquote(fm.get("description"))):
            no_use_when.append(s)
    if no_use_when:
        (errors if require_all else notes).append(f'description without a "Use when" phrase ({len(no_use_when)}): {", ".join(no_use_when)}')
    if other:
        notes.append("other cases (not trigger cases): " + ", ".join(f"{p}-* ({n})" for p, n in sorted(other.items())))
    return errors, notes, suites


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Static check of the eval suite (no tokens): case format, trigger "
                                 "suites of >= 10 cases with positives and near misses, and notes on coverage.")
    ap.add_argument("--require-all", action="store_true",
                    help='also fail on a skill without a trigger suite or a description without "Use when"')
    ap.add_argument("--kit", type=Path, default=KIT, help="the kit root (default: this checkout)")
    a = ap.parse_args(argv)
    errors, notes, suites = check(a.kit.resolve(), a.require_all)
    for n in notes:
        print(f"eval-check: note — {n}")
    for e in errors:
        print(f"eval-check: FAIL — {e}")
    if errors:
        return 1
    summary = ", ".join(f"{s} {n['positive']}+{n['near-miss']}" for s, n in sorted(suites.items())) or "none"
    print(f"eval-check: OK — trigger suites (positive+near miss): {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
