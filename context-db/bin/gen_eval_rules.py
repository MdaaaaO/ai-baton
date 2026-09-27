#!/usr/bin/env python3
"""gen_eval_rules.py — copy the review rules into the kit-review eval prompts.

`claude plugin eval` runs a case in a sandbox whose file reads are confined to a scratch cwd: the plugin's own
`docs/REVIEW.md` is not readable from the prompt. So every `evals/kit-review-*/prompt.md` carries the rules inline,
between `<!-- rules:begin -->` and `<!-- rules:end -->`, and this script keeps that block equal to docs/REVIEW.md
§ 2–§ 6 (the judgment; § 1 is CI's). Run it after editing the rules; `tests/test_eval_rules.py` fails when a prompt
drifts. Usage: gen_eval_rules.py [--check]  (exit 1 when a prompt would change). Stdlib only.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
RULES = KIT / "docs" / "REVIEW.md"
BEGIN, END = "<!-- rules:begin -->", "<!-- rules:end -->"


def rules_block() -> str:
    text = RULES.read_text(encoding="utf-8")
    start = text.index("## 2.")
    body = text[start:].rstrip() + "\n"
    return f"{BEGIN}\nThe rules (docs/REVIEW.md § 2–6 of the plugin, copied here by gen_eval_rules.py — do not edit in place):\n\n{body}{END}"


def apply(text: str, block: str) -> str:
    if BEGIN in text and END in text:
        return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _m: block, text, count=1, flags=re.S)
    return text.rstrip("\n") + "\n\n" + block + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    block = rules_block()
    changed = []
    for p in sorted(KIT.glob("evals/kit-review-*/prompt.md")):
        old = p.read_text(encoding="utf-8")
        new = apply(old, block)
        if new != old:
            changed.append(p.relative_to(KIT))
            if not check:
                p.write_text(new, encoding="utf-8")
    for c in changed:
        print(("would update " if check else "updated ") + str(c))
    if not changed:
        print("eval prompts carry the current rules")
    return 1 if (check and changed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
