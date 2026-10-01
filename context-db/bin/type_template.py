#!/usr/bin/env python3
"""type_template.py — ties a ctx-store type's declared `sections` (and `log.section`) to its template's
`## ` headings. One tiny module so `context-db/tests/test_type_template.py` and kit-health's environment
override check (`skills/kit-health/kit-health.py`) share one implementation, not two.

Why a required section is rare: a `sections` entry fails `ctx validate` on every existing doc that lacks
it, and a migration cannot add a heading to a doc that is not there to migrate — so a section is only safe
to require once every store already has it. Today only `epic` declares any. `log.section`, when a type
declares one, must itself be one of the declared `sections` (the log writer's insertion point would
otherwise sit in a section nothing requires).

Stdlib only.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

BIN = Path(__file__).resolve().parent
ENGINE = BIN.parent                            # context-db/
TYPES_DIR = ENGINE / "ctx-store" / "types"
TEMPLATES_DIR = ENGINE / "_templates"


HEADING = re.compile(r"^## +(.*?)\s*$")
FENCE = re.compile(r"^(```|~~~)")


def heading_list(template_text: str) -> list[str]:
    """Every `## ` heading of a template's text, in order and with repeats — read the way the store reads a
    section (`ctx validate`): a heading inside fenced code is not one, the trailing words are kept as they are."""
    found: list[str] = []
    fence = None
    for line in template_text.split("\n"):
        mark = FENCE.match(line)
        if mark:
            if fence is None:
                fence = mark.group(1)
            elif line.startswith(fence):
                fence = None
            continue
        if fence is None:
            m = HEADING.match(line)
            if m:
                found.append(m.group(1))
    return found


def template_headings(template_text: str) -> set[str]:
    """The distinct `## ` headings of a template's text (`heading_list`)."""
    return set(heading_list(template_text))


def missing_sections(sections: list[str], template_text: str) -> list[str]:
    """`sections` that are not a `## ` heading of `template_text`, in the order given — empty when every one is."""
    headings = template_headings(template_text)
    return [s for s in sections if s not in headings]


def repeated_sections(sections: list[str], template_text: str) -> list[str]:
    """`sections` whose heading is in `template_text` more than once, in the order given — the store refuses a
    doc with the same `## ` heading twice, so a template that repeats one scaffolds docs that never validate."""
    headings = heading_list(template_text)
    return [s for s in sections if headings.count(s) > 1]


def type_sections(type_path: Path) -> tuple[list[str], str | None]:
    """(declared `sections`, declared `log.section`) of a `ctx-store/types/<type>.json` file — `([], None)`
    when the type declares neither."""
    data = json.loads(type_path.read_text(encoding="utf-8"))
    sections = data.get("sections") or []
    log_section = (data.get("log") or {}).get("section")
    return sections, log_section


def log_section_untied(sections: list[str], log_section: str | None) -> bool:
    """True when a declared `log.section` is not among the declared `sections` — the one `log.section`
    finding this module reports; false when the type declares no `log.section` at all."""
    return bool(log_section) and log_section not in sections
