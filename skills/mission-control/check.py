#!/usr/bin/env python3
"""check.py - the gate on a built mission-control page: complete, self-contained, no remote load or script call.

    check.py <index.html> [--allow-host HOST]... [--sections sessions,cost,...]

Reads the page with the stdlib HTML parser (no browser, no network) and reports one finding per line:
  - a required section id is missing (default: sessions, cost, catalog, priorities, queues, pointers) or the
    label text "data as of" is;
  - the #data / #build JSON blocks are missing, are not application/json, or do not parse as a JSON object;
  - a <script src>, <iframe>, <object>, <embed>, <link href>, <img src> or <form action>, a <meta http-equiv=refresh>
    (any), or a <script> of type module or speculationrules;
  - a srcset, imagesrcset, poster, data, action, formaction or ping attribute with any value;
  - an @import, a url() or an image-set() in a <style> block or a style attribute;
  - in an inline <script> (the JSON blocks apart), in an on... attribute or in a javascript: URL, a whole-word
    match for fetch(, XMLHttpRequest, sendBeacon, WebSocket, EventSource, import(, new Worker, serviceWorker,
    importScripts, new Image, new Audio (connections and loads), document.write, innerHTML, outerHTML,
    insertAdjacentHTML (markup writes), window.open, an assignment to location or an append to it (location =
    and location +=, also on window.location and document.location), an assignment or append to location.host,
    .hostname, .pathname, .search, .port or .protocol, location.href, location.assign or location.replace
    (navigation); an assignment to .src; or a literal http:// or https:// URL;
  - an attribute value starting http://, https:// or // anywhere but on an <a href> whose host is github.com,
    the host of the store's tracker.url_template, or one given with --allow-host.

It matches text: markup, CSS and script are read and never run, so a call assembled at run time is out of its
reach. A clean run says the scan found nothing, not that the page is proven offline.

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
BAD_SCRIPTS = ("module", "speculationrules")  # script types that import code or prefetch pages the text never names
LOADS = {"script": "src", "link": "href", "img": "src", "form": "action"}  # element -> the attribute that fetches
LOAD_ATTRS = ("srcset", "imagesrcset", "poster", "data", "action", "formaction", "ping")  # fetch or navigate, anywhere
URL_START = re.compile(r"(?:https?:)?//", re.I)
CSS_LOADS = re.compile(r"@import[^;]*|(?:url|image-set)\([^)]*\)?", re.I)
CALLS = tuple(  # (name shown, pattern): script text that opens a connection, loads code, writes markup or navigates
    (name, re.compile(pattern))
    for name, pattern in (
        ("fetch(", r"\bfetch\s*\("),
        ("XMLHttpRequest", r"\bXMLHttpRequest\b"),
        ("sendBeacon", r"\bsendBeacon\b"),
        ("WebSocket", r"\bWebSocket\b"),
        ("EventSource", r"\bEventSource\b"),
        ("import(", r"\bimport\s*\("),
        ("new Worker", r"\bnew\s+(?:Shared)?Worker\b"),
        ("serviceWorker", r"\bserviceWorker\b"),
        ("document.write", r"\bdocument\s*\.\s*write(?:ln)?\b"),
        ("new Image", r"\bnew\s+Image\b"),
        ("importScripts", r"\bimportScripts\b"),
        ("innerHTML", r"\binnerHTML\b"),
        ("outerHTML", r"\bouterHTML\b"),
        ("insertAdjacentHTML", r"\binsertAdjacentHTML\b"),
        ("new Audio", r"\bnew\s+Audio\b"),
        ("window.open", r"\bwindow\s*\.\s*open\b"),
        ("location.href|assign|replace", r"\blocation\s*\.\s*(?:href|assign|replace)\b"),
        ("location =", r"\b(?:(?:window|document)\s*\.\s*)?location\s*\+?=(?![=>])"),  # not == (compare) or => (arrow)
        ("location.host|hostname|pathname|search|port|protocol =",
         r"\blocation\s*\.\s*(?:host|hostname|pathname|search|port|protocol)\s*\+?=(?![=>])"),
        (".src =", r"\.\s*src\s*=(?!=)"),
        ("http(s)://", r"(?i:\bhttps?://)"),  # a remote URL spelled out; never a bare //, which is a comment
    )
)
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


def calls(code: str) -> list[str]:
    """The names, in table order, of the connections, loads and markup writes a piece of script text mentions."""
    return [name for name, pattern in CALLS if pattern.search(code)]


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
        self.css: list[list[str]] = []  # the text of each <style>, in the pieces the parser delivered it in
        self.code: list[list[str]] = []  # ... of each <script> a browser may run (a JSON data block is not one)
        self.blocks: dict[str, list[str]] = {}
        self.types: dict[str, str] = {}
        self.found: list[str] = []
        self.raw: str | None = None  # the open <script> or <style>
        self.cur: list[str] = []  # its pieces

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
        if tag == "meta" and a.get("http-equiv", "").strip().lower() == "refresh":
            self.found.append(f'meta refresh: <meta http-equiv="refresh" content="{clean(a.get("content", ""))}">')
        if tag == "script" and a.get("type", "").strip().lower() in BAD_SCRIPTS:
            self.found.append(f'forbidden script type: <script type="{clean(a["type"])}">')
        for name, value in a.items():
            if name == loads:
                continue
            url = url_of(value)
            if name in LOAD_ATTRS and url:
                self.found.append(f'forbidden attribute: <{tag} {name}="{clean(value)}">')
                continue
            if name == "style":
                self.found.extend(f'style loads: "{clean(m)}" in a style attribute' for m in CSS_LOADS.findall(value))
            if name.startswith("on") or url[:11].lower() == "javascript:":
                self.found.extend(f"script call: {c} in the {name} attribute" for c in calls(value))
            if not URL_START.match(url):
                continue
            if tag == "a" and name == "href":
                host = host_of(url)
                if host not in self.allowed:
                    self.found.append(f'link host not allowed: {host or "(none)"} in <a href="{clean(value)}">')
            else:
                self.found.append(f'remote reference outside a link: <{tag} {name}="{clean(value)}">')
        if tag in ("script", "style"):
            self.raw, self.cur = tag, []
            if tag == "style":
                self.css.append(self.cur)
            elif a.get("type", "").strip().lower() != "application/json":
                self.code.append(self.cur)
            if tag == "script" and a.get("id") in BLOCKS and a["id"] not in self.blocks:
                self.blocks[a["id"]] = self.cur
                self.types[a["id"]] = a.get("type", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == self.raw:
            self.raw = None

    def handle_data(self, data: str) -> None:
        (self.text if self.raw is None else self.cur).append(data)


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
    out.extend(f'style loads: "{clean(m)}" in <style>' for part in s.css for m in CSS_LOADS.findall("".join(part)))
    out.extend(f"script call: {c} in an inline script" for part in s.code for c in calls("".join(part)))
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
    print(f"ok: {clean(args.page)}: {len(sections)} sections, {len(BLOCKS)} JSON blocks, "
          "no remote load or script call found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
