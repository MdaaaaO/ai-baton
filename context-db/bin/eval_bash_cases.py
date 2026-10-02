#!/usr/bin/env python3
"""eval_bash_cases.py — case names in scope whose prompt.md lists Bash in `allowed_tools` (the eval target's
"per case, never a blanket grant" rule: `--allow-tools Bash` applies to every case in the invocation it is
passed to, so a case that wants Bash runs alone, in its own invocation, rather than widening every other case
in the same scope). Reuses eval_check.py's case-directory walk and frontmatter reader so this never disagrees
with the static gate about what a case's `allowed_tools` says. Stdlib only, no tokens spent.

Usage: eval_bash_cases.py [--case GLOB | --skill NAME] [--kit DIR]
Prints one case directory name per line, sorted; an empty or absent scope prints every case that declares
Bash. Exit 0 always.
"""
from __future__ import annotations
import argparse
import fnmatch
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_check  # noqa: E402

BASH_RE = re.compile(r"(?<![\w-])Bash(?![\w-])")


def declares_bash(allowed_tools_raw) -> bool:
    """Whether a case's raw `allowed_tools` frontmatter value (eval_check.read_md's shape: the unparsed
    "[A, B, C]" string, or None when the key is absent) names Bash as its own entry, not as a prefix or
    suffix of a longer tool name."""
    return allowed_tools_raw is not None and bool(BASH_RE.search(str(allowed_tools_raw)))


def bash_case_names(kit: Path, case_glob: str | None) -> list[str]:
    """Case directory names in scope (case_glob, else every case) whose prompt.md declares Bash. A case.yaml-
    only case (no prompt.md) is out of scope here, the same way eval_check.check_case never reads its
    allowed_tools either."""
    root = eval_check.eval_dir(kit)
    if not root.is_dir():
        return []
    names = []
    for case_dir in sorted(p for p in root.iterdir()
                            if p.is_dir() and p.name not in eval_check.SKIP_DIRS and not p.name.startswith(".")):
        if case_glob and not fnmatch.fnmatch(case_dir.name, case_glob):
            continue
        prompt = case_dir / "prompt.md"
        if not prompt.is_file():
            continue
        fm, _ = eval_check.read_md(prompt)
        if declares_bash(fm.get("allowed_tools")):
            names.append(case_dir.name)
    return names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--case", help="a case-name glob, the same shape `claude plugin eval --case` takes")
    scope.add_argument("--skill", help="shorthand for --case '<skill>-*'")
    ap.add_argument("--kit", type=Path, default=eval_check.KIT, help="the kit root (default: this checkout)")
    a = ap.parse_args(argv)
    case_glob = a.case or (f"{a.skill}-*" if a.skill else None)
    for name in bash_case_names(a.kit.resolve(), case_glob):
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
