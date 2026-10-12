#!/usr/bin/env python3
"""check.py - the gate on a built mission-control page: complete, self-contained, no remote reference.

    check.py <index.html> [--allow-host HOST]... [--sections sessions,cost,...]

Reads the page with the stdlib HTML parser (no browser, no network) and reports one finding per line:
  - a required section id is missing (default: sessions, cost, catalog, priorities, queues, pointers) or the
    label text "data as of" is;
  - the #data / #build JSON blocks are missing, are not application/json, or do not parse as a JSON object;
  - a <script src>, <iframe>, <object>, <embed>, <link href>, <img src> or <form action>;
  - an @import or a url() to a remote address in a <style> block or a style attribute;
  - an attribute value starting http://, https:// or // anywhere but on an <a href> whose host is github.com,
    the host of the store's tracker.url_template, or one given with --allow-host.

Exit 0 and one line on stdout when clean; exit 1 with the findings on stdout and one summary line on stderr;
exit 2 with one stderr line on a usage error or an unreadable file. Stdlib only, Python 3.9+.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ENGINE_BIN = HERE.parent.parent / "context-db" / "bin"

SECTIONS = ("sessions", "cost", "catalog", "priorities", "queues", "pointers")
LABEL = "data as of"
BLOCKS = ("data", "build")
KIT_HOSTS = ("github.com",)
BAD_TAGS = ("iframe", "object", "embed")
LOADS = {"script": "src", "link": "href", "img": "src", "form": "action"}  # element -> the attribute that fetches
URL_START = re.compile(r"(?:https?:)?//", re.I)
CSS_REMOTE = re.compile(r"@import[^;]*|url\(\s*[\"']?\s*(?:https?:)?//[^)\"'\s]*", re.I)
ID_RE = re.compile(r"[A-Za-z][\w-]*\Z")
BLANK = "".join(chr(c) for c in range(33))  # what a URL parser trims from both ends of a value


def clean(text: object, limit: int = 90) -> str:
    """One printable ASCII line for a finding: whitespace collapsed, non-ASCII replaced, long values cut."""
    line = " ".join(str(text).split()).encode("ascii", "replace").decode("ascii")
    return line if len(line) <= limit else line[: limit - 3] + "..."


def url_of(value: str) -> str:
    """An attribute value as a URL parser reads it: tabs and newlines dropped, ends trimmed, backslashes as slashes."""
    return re.sub(r"[\t\r\n]", "", value).strip(BLANK).replace("\\", "/")


def host_of(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def tracker_host() -> str | None:
    """The host of the store's tracker.url_template, None when unset, templated or the store cannot be read."""
    try:
        with contextlib.redirect_stderr(io.StringIO()):  # kit_profile warns once on a machine without a store
            sys.path.insert(0, str(ENGINE_BIN))
            import kit_profile  # noqa: E402  - lazy: --help and a usage error never touch the store

            template = kit_profile.get("tracker.url_template", "")
    except (Exception, SystemExit):
        return None
    host = host_of(template) if isinstance(template, str) else None
    return host if host and "{" not in host and "}" not in host else None


class Scan(HTMLParser):
    def __init__(self, allowed: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.allowed = allowed
        self.ids: set[str] = set()
        self.text: list[str] = []  # visible text: everything outside <script> and <style>
        self.css: list[str] = []
        self.blocks: dict[str, list[str]] = {}
        self.types: dict[str, str] = {}
        self.found: list[str] = []
        self.raw: str | None = None  # the open <script> or <style>
        self.block: str | None = None  # the JSON block being read

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a: dict[str, str] = {}
        for name, value in attrs:
            a.setdefault(name, value or "")  # the first occurrence wins, as in a browser
        if "id" in a:
            self.ids.add(a["id"])
        loads = LOADS.get(tag) if LOADS.get(tag) in a else None
        if tag in BAD_TAGS:
            self.found.append(f"forbidden element: <{tag}>")
        elif loads:
            self.found.append(f'forbidden element: <{tag} {loads}="{clean(a[loads])}">')
        for name, value in a.items():
            if name == loads:
                continue
            if name == "style":
                self.found.extend(f'remote style: "{clean(m)}" in a style attribute' for m in CSS_REMOTE.findall(value))
            url = url_of(value)
            if not URL_START.match(url):
                continue
            if tag == "a" and name == "href":
                host = host_of(url)
                if host not in self.allowed:
                    self.found.append(f'link host not allowed: {host or "(none)"} in <a href="{clean(value)}">')
            else:
                self.found.append(f'remote reference outside a link: <{tag} {name}="{clean(value)}">')
        if tag in ("script", "style"):
            self.raw = tag
            if tag == "script" and a.get("id") in BLOCKS and a["id"] not in self.blocks:
                self.block = a["id"]
                self.blocks[self.block] = []
                self.types[self.block] = a.get("type", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == self.raw:
            self.raw = self.block = None

    def handle_data(self, data: str) -> None:
        if self.raw is None:
            self.text.append(data)
        elif self.raw == "style":
            self.css.append(data)
        elif self.block:
            self.blocks[self.block].append(data)


def scan(page: str, sections: list[str], allowed: set[str]) -> list[str]:
    """Every finding for a page, in a stable order, each once."""
    s = Scan(allowed)
    try:
        s.feed(page)
        s.close()
    except Exception as e:  # the stdlib parser has no strict mode; a page it cannot read is a finding, not a crash
        return [f"unparsable HTML: {type(e).__name__}"]
    out = [f"missing section id: {sid}" for sid in sections if sid not in s.ids]
    if LABEL not in " ".join("".join(s.text).split()):
        out.append(f'missing text: "{LABEL}"')
    for bid in BLOCKS:
        if bid not in s.blocks:
            out.append(f"missing JSON block: #{bid}")
            continue
        if s.types[bid].strip().lower() != "application/json":
            out.append(f'JSON block is not application/json: #{bid} (type="{clean(s.types[bid])}")')
        try:
            doc = json.loads("".join(s.blocks[bid]))
        except (ValueError, RecursionError) as e:
            out.append(f"invalid JSON in #{bid}: {clean(e)}")
            continue
        if not isinstance(doc, dict):
            out.append(f"JSON block is not an object: #{bid}")
    out.extend(f'remote style: "{clean(m)}" in <style>' for m in CSS_REMOTE.findall("".join(s.css)))
    out.extend(s.found)
    return list(dict.fromkeys(out))


class Parser(argparse.ArgumentParser):
    def error(self, message: str):  # one stderr line, exit 2 (argparse's default prints the whole usage block)
        sys.stderr.write(f"check.py: {message}\n")
        raise SystemExit(2)


def die(message: str) -> int:
    sys.stderr.write(f"check.py: {message}\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = Parser(prog="check.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                allow_abbrev=False)
    ap.add_argument("page", help="the built index.html")
    ap.add_argument("--allow-host", action="append", default=[], metavar="HOST",
                    help="one more host an <a href> may point at (repeatable; github.com and the tracker host "
                         "of the store's tracker.url_template are always allowed)")
    ap.add_argument("--sections", action="append", default=None, metavar="ID,ID",
                    help="the section ids the page must hold (default: " + ",".join(SECTIONS) + ")")
    args = ap.parse_args(argv)
    sections = list(SECTIONS)
    if args.sections is not None:
        sections = [p.strip() for chunk in args.sections for p in chunk.split(",") if p.strip()]
        if not sections or not all(ID_RE.match(p) for p in sections):
            return die("--sections wants a comma-separated list of section ids")
    allowed = set(KIT_HOSTS)
    for value in args.allow_host:
        host = host_of(value.strip() if "//" in value else "//" + value.strip())
        if not host:
            return die(f"--allow-host wants a host name, got {clean(value)!r}")
        allowed.add(host)
    try:
        page = Path(args.page).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as e:
        return die(f"cannot read {clean(args.page)}: {clean(getattr(e, 'strerror', None) or e)}")
    host = tracker_host()
    if host:
        allowed.add(host)
    findings = scan(page, sections, allowed)
    if findings:
        print("\n".join(findings))
        sys.stderr.write(f"check.py: {clean(args.page)}: {len(findings)} finding{'s' * (len(findings) != 1)}\n")
        return 1
    print(f"ok: {clean(args.page)}: {len(sections)} sections, {len(BLOCKS)} JSON blocks, no remote reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
