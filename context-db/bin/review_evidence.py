#!/usr/bin/env python3
"""review_evidence.py — the review's tier 1: machine-prepared evidence the model reads instead of re-deriving.

Writes one markdown file (default `.review/evidence.md`) for a PR head against its base:

  1. the changed files and the units among them with their `metadata.requires` sets, before → after;
  2. the tier-0 verdict (`review_gate.py`: leak shapes on added lines, version/updated bumps) — what CI already
     decided, so the reviewer stops asking for it;
  3. two grep-able-but-ambiguous checks, pre-flagged for judgment, never decided here:
     - a swallowed error on an added line of a script or skill body (`2>/dev/null`, `|| true`, `|| echo`,
       `except: pass`) — is the emptiness of that output meaningful? (WORKSPACE.md's invariant: a swallowed error
       must never read as a negative result);
     - system vocabulary (`kb.SYSTEM_VOCAB`) added to a unit whose `requires` lacks that capability — the unit may be
       describing a generic step, or it may be assuming a system the machine does not have.
  4. the rules file the reviewer judges by, copied from the BASE branch (`docs/REVIEW.md`, tamper-proof: a PR cannot
     rewrite the rules it is judged by), into `.review/RULES.md` beside the evidence.

Stdlib only; needs `git`. `--json` prints the same evidence as an object (the tests read it).
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import frontmatter as fmt  # noqa: E402
import kb  # noqa: E402
import review_gate as gate  # noqa: E402

KIT = gate.KIT
RULES = "docs/REVIEW.md"
# The swallow forms WORKSPACE.md § Verification names (#145): stderr (or both streams) to /dev/null in any spelling, an
# `|| true` / `|| :` / `|| echo` that turns a failure into success, a bare or pass/continue/return `except`.
SWALLOW = re.compile(r"2>\s*/dev/null|&>>?\s*/dev/null|>\s*/dev/null\s+2>&1|2>&1\s+>\s*/dev/null"
                     r"|\|\|\s*true\b|\|\|\s*:(?=\s|;|\)|$)|\|\|\s*echo\b"
                     r"|\bexcept\s*:|\bexcept\b[^:]*:\s*(?:pass|continue|return)\b")
SCRIPT_SUFFIXES = (".sh", ".bash", ".py", ".mjs", ".js", ".mk", "Makefile", "SKILL.md")
SHEBANG = re.compile(r"^#!.*\b(?:(?:ba|da|z)?sh|python[0-9.]*|node)\b")


def is_script(path: str, head: str, cwd: Path = KIT) -> bool:
    """A file whose added lines are code: by suffix, else by shebang (hooks/pre-push has neither suffix nor .sh)."""
    if path.endswith(SCRIPT_SUFFIXES):
        return True
    # bytes, not text: a binary file in the diff (an image, a font) must not crash the evidence with a decode error
    r = subprocess.run(["git", "show", f"{head}:{path}"], cwd=cwd, capture_output=True)
    if r.returncode != 0 or b"\0" in r.stdout[:8000]:
        return False
    return bool(SHEBANG.match(r.stdout.split(b"\n", 1)[0].decode("utf-8", errors="replace")))


def requires_of(text: str | None) -> set[str]:
    fm = fmt.parse(text) if text else None
    md = (fm or {}).get("metadata") if fm else None
    md = md if isinstance(md, dict) else {}
    raw = fmt.unquote(md.get("requires", ""))
    return {r.strip() for r in raw.split(",") if r.strip()} if raw else set()


def units_changed(base: str, head: str, files: dict[str, str], cwd: Path = KIT) -> list[dict]:
    mb = gate.merge_base(base, head, cwd)
    out = []
    for unit in sorted({u for u in (gate.unit_of(p) for p in files) if u}):
        before, after = gate.show(mb, unit, cwd), gate.show(head, unit, cwd)
        out.append({"unit": unit, "name": gate.unit_name(unit), "status": "removed" if after is None else "new" if before is None else "changed",
                    "requires_before": sorted(requires_of(before)), "requires_after": sorted(requires_of(after)),
                    "version": list(gate.meta(before))[0:1] + list(gate.meta(after))[0:1],
                    "files": sorted(p for p in files if gate.unit_of(p) == unit)})
    return out


def swallowed(base: str, head: str, files: dict[str, str], cwd: Path = KIT) -> list[dict]:
    out = []
    for path, status in sorted(files.items()):
        if status == "D" or not is_script(path, head, cwd):
            continue
        for n, text in gate.added_lines(base, head, path, cwd):
            m = SWALLOW.search(text)
            if m:
                out.append({"path": path, "line": n, "match": m.group(0), "text": text.strip()[:160]})
    return out


def vocabulary(base: str, head: str, files: dict[str, str], units: list[dict], cwd: Path = KIT) -> list[dict]:
    out = []
    for u in units:
        if u["status"] == "removed":
            continue
        have = set(u["requires_after"])
        words = [(sys_, w) for sys_, ws in kb.SYSTEM_VOCAB.items() if sys_ not in have for w in ws]
        if not words:
            continue
        pats = [(sys_, w, re.compile(rf"(?<![\w.-]){re.escape(w)}(?![\w-])")) for sys_, w in words]
        for path in u["files"]:
            if files.get(path) == "D":
                continue
            for n, text in gate.added_lines(base, head, path, cwd):
                if re.match(r"\s*(requires|facts|compatibility):", text):
                    continue
                for sys_, w, rx in pats:
                    if rx.search(text):
                        out.append({"unit": u["name"], "path": path, "line": n, "word": w, "system": sys_, "text": text.strip()[:160]})
                        break
    return out


def rules_from_base(base: str, cwd: Path = KIT) -> tuple[str, str]:
    """(text, source) — the rules file as the base branch has it, else this head's copy with a note, else a stub."""
    t = gate.show(base, RULES, cwd)
    if t is not None:
        return t, f"{base}:{RULES}"
    p = cwd / RULES
    if p.is_file():
        return p.read_text(encoding="utf-8"), f"{RULES} from this head (the base branch has none yet — judge it as part of the PR)"
    return "# Rules\n\n(no docs/REVIEW.md on either branch)\n", "none"


def build(base: str, head: str, skip_bump: bool = False, cwd: Path = KIT) -> dict:
    findings, files = gate.run(base, head, skip_bump, cwd)
    units = units_changed(base, head, files, cwd)
    return {"base": base, "head": head, "files": files, "units": units, "tier0": findings, "skip_bump": skip_bump,
            "swallowed": swallowed(base, head, files, cwd), "vocabulary": vocabulary(base, head, files, units, cwd)}


def render(ev: dict, rules_source: str) -> str:
    L = [f"# Review evidence — {ev['head']} against {ev['base']}", "",
         "Machine-prepared (review_evidence.py). Read, do not re-derive. Sections 1–2 are decided; section 3 is yours to judge.", "",
         "## 1. Changed files and units", ""]
    L += [f"- `{p}` ({s})" for p, s in sorted(ev["files"].items())] or ["- (none)"]
    L += [""]
    if ev["units"]:
        L += ["| unit | status | requires before | requires after | version |", "|---|---|---|---|---|"]
        for u in ev["units"]:
            vb, va = (u["version"] + [None, None])[:2]
            L.append(f"| `{u['name']}` | {u['status']} | {', '.join(u['requires_before']) or '—'} | {', '.join(u['requires_after']) or '—'} | {vb} → {va} |")
    else:
        L.append("No skill or agent changed (engine, docs or CI only).")
    L += ["", "## 2. Tier 0 — decided by CI, not by you", ""]
    if ev["tier0"]:
        L += ["**FAILED** — the PR is red until these are fixed; do not repeat them as findings:", ""] + [f"- {f}" for f in ev["tier0"]]
    else:
        L.append("PASSED: leak shapes on every added line (chat/user ids, ticket keys, account ids, org hosts, timezone literals, e-mails, "
                 "home paths, token shapes), version + `updated` bump for every changed unit"
                 + (" (bump check skipped: the PR is marked wording-only — judge whether that claim holds)" if ev["skip_bump"] else "")
                 + ". Also green before this step: frontmatter schema, description budget, referenced scripts, plugin manifests (kit-verify).")
    L += ["", "## 3. Pre-flagged for judgment (grep found the shape, only you can tell the meaning)", "", "### Swallowed errors on added lines", ""]
    L += [f"- `{s['path']}:{s['line']}` `{s['match']}` — `{s['text']}`" for s in ev["swallowed"]] or ["- none"]
    L += ["", "Ask: is the emptiness or the exit status of that command meaningful to what follows? If yes, WARN (WORKSPACE.md invariant).", "",
          "### System vocabulary in a unit that does not require the capability", ""]
    L += [f"- `{v['unit']}` `{v['path']}:{v['line']}` says `{v['word']}` (capability `{v['system']}` not in requires) — `{v['text']}`" for v in ev["vocabulary"]] or ["- none"]
    L += ["", "Ask: does the unit assume that system, or describe a generic step with a specific word? Assuming → WARN with the env-store key it belongs to.",
          "", f"## 4. Rules: `.review/RULES.md` (from {rules_source})", ""]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="write the review evidence file for a PR head against its base")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--skip-bump", action="store_true")
    ap.add_argument("--out", default=".review/evidence.md", help="evidence path; RULES.md is written beside it")
    ap.add_argument("--json", action="store_true", help="print the evidence object instead of writing files")
    ap.add_argument("--repo", type=Path, default=None, help="the repository (default: the working directory's git top level)")
    a = ap.parse_args(argv)
    cwd = a.repo or gate.repo_root()
    ev = build(a.base, a.head, a.skip_bump, cwd)
    rules, source = rules_from_base(a.base, cwd)
    if a.json:
        ev["rules_source"] = source
        print(json.dumps(ev, indent=1))
        return 0
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(ev, source), encoding="utf-8")
    (out.parent / "RULES.md").write_text(rules, encoding="utf-8")
    print(f"review-evidence: {out} ({len(ev['files'])} files, {len(ev['units'])} units, tier0 {'FAIL' if ev['tier0'] else 'OK'}, "
          f"{len(ev['swallowed'])} swallowed-error line(s), {len(ev['vocabulary'])} vocabulary line(s)); rules from {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
