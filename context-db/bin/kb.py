#!/usr/bin/env python3
"""kb.py — the environment fact store: `.context/reference/env/`.

Skills need environment facts (a Slack channel id, a Jira custom-field id, an AWS account, a Notion
database) that differ between the places the kit runs. Those facts never live in a SKILL.md or an
engine script; they live here, in the user's local `.context/` — as a knowledge base that fills
itself as the kit is used (discover → ask → `kb set`) and that nobody has to hand-author up front.

Layout (all under `.context/reference/env/`, created by `kb.py init`):
  config.json          the few STRUCTURAL switches scripts branch on: `environment` (this machine's
                       environment name), tracker.kind / key_regex / url_template / mcp_tools,
                       github.org / review_bot / bots / owner_teams, slack.enabled / domain, systems.*,
                       tz_default, domains, labels, self_assessment, diagrams, kit.install_mode
                       (written by setup.sh: clone | plugin | dev-checkout)
  _templates/<type>.md optional overrides of the engine's doc templates (`new.sh` looks here first)
  <system>.md          one doc per system (slack, tracker, github, aws, notion, …); one `## <kind>`
                       section per fact kind; one table row per fact:
                       | name | value | purpose | learned-from |

Usage:
  kb.py get  <system>.<kind> <name>            → the value (exit 1 + a hint when unknown; a stale-row
                                                 hint on stderr when it is older than its manifest ttl)
  kb.py set  <system>.<kind> <name> <value> [--purpose TEXT] [--from PROVENANCE]   (upsert)
                                                 PROVENANCE = tool:<tool-name> | user | derived:<key>;
                                                 today's date is appended (default: user).
                                                 Same value + an explicit --from = `re-verified` (the
                                                 provenance is re-dated, so `stale` clears); exit 2 on an
                                                 empty name
  kb.py rm   <system>.<kind> <name>
  kb.py list [<system>[.<kind>]]               → `system.kind  name  value  purpose  learned-from`
  kb.py values                                 → every literal value, one per line (the leak scanner)
  kb.py config [<dotted.key>]                  → config.json, or one key (JSON for non-scalars)
  kb.py config-set <dotted.key> <json-or-string>
  kb.py discover <system>.<kind> [<name>]      → the discovery PLAN for a fact (tool + args, verify, ttl,
  kb.py discover <config.key>                    the write-back command) from context-db/discovery/*.json —
                                                 it never calls the tool; the session does, then `set`
  kb.py discover --all | --check               → every manifest fact with its state here | validate manifests
  kb.py stale [--days N] [--check]             → tool/import/derived rows older than their manifest ttl_days
                                                 (N overrides every ttl); --check = exit 3 when any
  kb.py migrate [--check]                      → bring the store up to the kit's schema: renamed system
                                                 flags moved (value kept), missing flags added (seeded from
                                                 their legacy flag, else false), rows under a renamed kind
                                                 (`slack.channels` → `slack.channel`) moved; prints the
                                                 skills now off here; --check = dry run, exit 3 when pending; --off = only that list
  kb.py init --blank                           → create an empty store (never overwrites anything)
  kb.py init --personal                        → blank store + the zero-config GitHub-only fill: identity
                                                 from `gh api user`, tracked repos from the workspace clones,
                                                 tz from the OS, every systems.* false — discovered, never asked
  kb.py path                                   → the store directory

Resolution order for a fact a skill needs: `kb get` → `kb discover` (the manifest names the system's
discovery tool: `slack_search_channels`, Jira issue-type metadata / transitions, `gh label list`, …) →
run that tool and verify → ask the user only when the tool cannot settle it → `kb set … --from tool:<name>`
(or `user`) so the next session has it. Manifest schema: context-db/discovery/README.md.
Stdlib only. Reads CONTEXT_ROOT (default: the `.context/` beside `.claude/`).
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_profile  # noqa: E402

KIT = kit_profile.KIT
CTX = kit_profile.context_root()
ROOT = CTX.parent
ENV = kit_profile.ENV_DIR
COLUMNS = ("name", "value", "purpose", "learned-from")

# Blank sections a fresh store starts with — the kinds skills ask for most. Any other `## kind` may be
# added by `kb set` at any time; these only save the first user a guess at the vocabulary.
DEFAULT_KINDS: dict[str, list[str]] = {
    "slack": ["channel", "review-venue", "user"],
    "tracker": ["setting", "field", "transition", "user", "query"],
    "github": ["person", "team", "label-set"],
    "aws": ["account", "profile", "region", "cluster"],
    "notion": ["database", "page"],
    "datalake": ["setting", "tool"],
}
# (system, old kind) → (system, kind): a heading some sessions wrote that means an existing kind. `get`/`set`
# answer under the canonical kind at once; `migrate` moves the rows; kit-verify flags a store still carrying one.
RENAMED_KINDS: dict[tuple[str, str], tuple[str, str]] = {("slack", "channels"): ("slack", "channel")}

# `learned-from` is structured so `stale` can measure a row's age: `<who> <YYYY-MM-DD>` with who =
# tool:<tool-name> (a discovery tool answered) | user (the user said so — never stale) | derived:<key> (computed
# from another fact). Rows written by the retired `kb.py import` (`import:<env> <date>`) still read fine: the
# date is what `stale` measures, the `import:` word is treated as free text.
PROVENANCE = re.compile(r"^(user|tool:[\w.-]+|derived:[\w. /:-]+?)(?:\s+(\d{4}-\d{2}-\d{2}))?$")
MANIFEST_DIR = KIT / "context-db" / "discovery"   # the kit's manifests; the store's `_discovery/` overlays them
BUILTIN_TOOLS = ("cli", "roster", "settings", "derive", "user")
FACT_KEY = re.compile(r"^[a-z][\w-]*(\.[\w:/@+-]+)*(?: \S+)?$")
DEFAULT_TTL = 180  # days, for a tool-sourced row no manifest covers

# Structural keys copied from an imported config.json into env/config.json (everything a script reads).
STRUCTURAL = {
    "tracker": ["kind", "key_regex", "url_template", "mcp_tools", "close_reasons", "repos"],
    "github": ["org", "review_bot", "bots", "signed_commits", "sandbox_token_prefix", "owner_teams"],
    "slack": ["enabled", "domain"],
}


# ── table docs ──────────────────────────────────────────────────────────────────────────────
def today() -> str:
    return dt.date.today().isoformat()


def doc_path(system: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", system):
        raise SystemExit(f"bad system name '{system}' (lowercase, digits, -/_)")
    return ENV / f"{system}.md"


def new_doc(system: str, kinds: list[str]) -> str:
    body = [
        "---",
        f"title: Env facts — {system}",
        "type: reference",
        "domain: reference",
        f"tags: [env, {system}]",
        "status: reference",
        f"updated: {today()}",
        "---",
        "",
        f"# Env facts — {system}",
        "",
        "<!-- Managed by `python3 $BATON/context-db/bin/kb.py` (get / set / list). One `## <kind>` section per",
        "     fact kind, one row per fact; hand edits are fine — keep the four columns. `learned-from` says how the",
        "     value was established (a discovery tool, the user, a derivation) so a stale row can be re-verified. -->",
        "",
    ]
    for k in kinds:
        body += [f"## {k}", "", table_header(), ""]
    return "\n".join(body)


def table_header() -> str:
    return "| " + " | ".join(COLUMNS) + " |\n|---|---|---|---|"


def esc(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


def unesc(v: str) -> str:
    return v.replace("\\|", "|").strip()


def split_row(line: str) -> list[str] | None:
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return None
    cells = re.split(r"(?<!\\)\|", s[1:-1])
    return [unesc(c) for c in cells]


def parse_doc(system: str) -> tuple[list[str], dict[str, dict[str, dict[str, str]]]]:
    """Return (lines, {kind: {name: {column: value}}}) — rows are read, not modified."""
    p = doc_path(system)
    if not p.is_file():
        return [], {}
    lines = p.read_text(encoding="utf-8").split("\n")
    facts: dict[str, dict[str, dict[str, str]]] = {}
    kind = ""
    for line in lines:
        m = re.match(r"^##\s+(\S+)\s*$", line)
        if m:
            kind = m.group(1)
            facts.setdefault(kind, {})
            continue
        cells = split_row(line)
        if not kind or cells is None or len(cells) < 2:
            continue
        if cells[0] in ("name", "---") or set(cells[0]) <= {"-", ":", " "}:
            continue
        row = {c: (cells[i] if i < len(cells) else "") for i, c in enumerate(COLUMNS)}
        if row["name"]:
            facts[kind][row["name"]] = row
    return lines, facts


def all_facts() -> dict[str, dict[str, dict[str, dict[str, str]]]]:
    out = {}
    if ENV.is_dir():
        for p in sorted(ENV.glob("*.md")):
            _, facts = parse_doc(p.stem)
            if facts:
                out[p.stem] = facts
    return out


def canonical_kind(system: str, kind: str) -> str:
    return RENAMED_KINDS.get((system, kind), (system, kind))[1]


def kind_aliases(system: str, kind: str) -> list[str]:
    """Every heading a row of `kind` may sit under: the canonical name first, then the renamed ones — the ONE
    resolution get/set/rm/list/stale share (a caller may pass either spelling)."""
    canon = canonical_kind(system, kind)
    return [canon, *legacy_kinds(system, canon)]


def legacy_kinds(system: str, kind: str) -> list[str]:
    return [old for (s, old), (_, new) in RENAMED_KINDS.items() if s == system and new == kind]


def kind_rows(facts_of_system: dict, system: str, kind: str) -> dict[str, dict[str, str]]:
    """The rows of a kind, including those still filed under a renamed heading (canonical rows win)."""
    merged: dict[str, dict[str, str]] = {}
    for old in legacy_kinds(system, kind):
        merged.update(facts_of_system.get(old, {}))
    merged.update(facts_of_system.get(kind, {}))
    return merged


def get_row(system: str, kind: str, name: str) -> dict[str, str] | None:
    _, facts = parse_doc(system)
    kind = canonical_kind(system, kind)
    row = kind_rows(facts, system, kind).get(name)
    return row if row and row["value"] else None


def get(system: str, kind: str, name: str) -> str | None:
    row = get_row(system, kind, name)
    return row["value"] if row else None


def normalize_provenance(learned: str) -> str:
    """`--from` → `<who> <date>`; free text is refused so every row's age stays measurable."""
    learned = (learned or "user").strip()
    m = PROVENANCE.match(learned)
    if not m:
        raise SystemExit(f"kb: --from '{learned}' is not a provenance — use tool:<tool-name>, user or "
                         "derived:<key> (today's date is appended), e.g. --from tool:slack_search_channels")
    if m.group(2):
        try:
            dt.date.fromisoformat(m.group(2))
        except ValueError:
            raise SystemExit(f"kb: --from '{learned}' carries an impossible date {m.group(2)} (want YYYY-MM-DD)")
    return f"{m.group(1)} {m.group(2) or today()}"


def provenance(learned: str) -> tuple[str, dt.date | None]:
    """(who, date) of a `learned-from` cell — who ∈ user | tool | import | derived | legacy (free text)."""
    m = PROVENANCE.match(learned.strip())
    if m:
        try:
            d = dt.date.fromisoformat(m.group(2)) if m.group(2) else None
        except ValueError:  # a hand-edited cell with an impossible date reads as undated, never as a crash
            d = None
        return m.group(1).split(":")[0], d
    d = None
    for ds in reversed(re.findall(r"\d{4}-\d{2}-\d{2}", learned)):
        try:
            d = dt.date.fromisoformat(ds)
            break
        except ValueError:
            continue
    low = learned.strip().lower()
    who = "user" if low.startswith("user") else "legacy"
    return who, d


def usage_error(msg: str) -> "SystemExit":
    """A caller error: the message on stderr, exit 2 (a missing value is exit 1, a crash would be a traceback)."""
    print(f"kb: {msg}", file=sys.stderr)
    return SystemExit(2)


def write_doc(system: str, lines: list[str]) -> None:
    """Write a system doc back and re-stamp its `updated:` — every row write (set, rm, migrate) goes through here."""
    text = "\n".join(lines)
    text = re.sub(r"^updated:.*$", f"updated: {today()}", text, count=1, flags=re.M)
    doc_path(system).write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def set_fact(system: str, kind: str, name: str, value: str, purpose: str = "", learned: str = "",
             keep_learned: bool = False) -> str:
    """Upsert one row; returns 'added' | 'updated' | 're-verified' | 'unchanged'. `learned` is validated as a
    provenance unless `keep_learned` (migrations carry a legacy cell over as-is). A row whose value and purpose
    are unchanged but that arrives with an explicit `--from` is RE-VERIFIED: its provenance (and date) is
    rewritten, so re-running a discovery clears a stale row; without `--from` it is `unchanged`."""
    if not re.fullmatch(r"[A-Za-z0-9][\w.:/@+-]*", kind):
        raise usage_error(f"bad kind '{kind}' (letters, digits, `.:/@+-`)")
    if not name or not name.strip() or name != name.strip():
        raise usage_error(f"{system}.{kind}: the row name must be a non-empty word without surrounding spaces, got {name!r}")
    if "|" in name:
        raise usage_error(f"{system}.{kind}: the row name {name!r} may not contain `|`")
    kind = canonical_kind(system, kind)
    ENV.mkdir(parents=True, exist_ok=True)
    p = doc_path(system)
    if not p.is_file():
        p.write_text(new_doc(system, DEFAULT_KINDS.get(system, [kind])), encoding="utf-8")
    lines, facts = parse_doc(system)
    explicit = bool(learned.strip())
    learned = learned if keep_learned and learned else normalize_provenance(learned)
    new_row = f"| {esc(name)} | {esc(value)} | {esc(purpose)} | {esc(learned)} |"
    existing = facts.get(kind, {}).get(name)
    if existing is not None:
        same = existing["value"] == value and (not purpose or existing["purpose"] == purpose)
        if same and not explicit:
            return "unchanged"
        if same and existing["learned-from"] == learned:
            return "unchanged"
        # replace in place
        in_kind = False
        for i, line in enumerate(lines):
            m = re.match(r"^##\s+(\S+)\s*$", line)
            if m:
                in_kind = m.group(1) == kind
                continue
            cells = split_row(line) if in_kind else None
            if cells and cells[0] == name:
                if not purpose:
                    new_row = f"| {esc(name)} | {esc(value)} | {esc(existing['purpose'])} | {esc(learned)} |"
                lines[i] = new_row
                break
        result = "re-verified" if same else "updated"
    else:
        if kind not in facts:
            # append a new section
            while lines and lines[-1] == "":
                lines.pop()
            lines += ["", f"## {kind}", "", *table_header().split("\n"), new_row, ""]
        else:
            # insert after the last row of that section
            in_kind = False
            last = None
            for i, line in enumerate(lines):
                m = re.match(r"^##\s+(\S+)\s*$", line)
                if m:
                    if in_kind:
                        break
                    in_kind = m.group(1) == kind
                    continue
                if in_kind and split_row(line) is not None:
                    last = i
            if last is None:  # section without a table yet
                for i, line in enumerate(lines):
                    if re.match(rf"^##\s+{re.escape(kind)}\s*$", line):
                        lines[i + 1:i + 1] = ["", *table_header().split("\n"), new_row]
                        break
            else:
                lines.insert(last + 1, new_row)
        result = "added"
    write_doc(system, lines)
    return result


def rm_fact(system: str, kind: str, name: str) -> bool:
    """Remove the row `name` from `kind` — under the canonical heading or a renamed one (`slack.channels x` removes
    a row filed as `slack.channel x` and vice versa) — and re-stamp `updated:`. False when there is no such row.
    `rm_fact_report()` also says which heading went and whether another alias still holds the name."""
    return rm_fact_report(system, kind, name)[0] is not None


def rm_fact_report(system: str, kind: str, name: str) -> tuple[str | None, list[str]]:
    """(the heading the row was removed from — None when there was none, the alias headings that STILL hold `name`
    afterwards). In the CONFLICT state (the name under both headings) one `rm` removes one row, so the caller can
    say that `get` will still answer from the other heading until `kb.py migrate`."""
    if not name or not name.strip():
        raise usage_error(f"{system}.{kind}: the row name must be a non-empty word")
    lines, facts = parse_doc(system)
    # the heading as spelled first; an alias only when the spelled one has no such row — and ONE row ever: in the
    # CONFLICT case (the name under both headings) `rm slack.channels x` drops the legacy copy and keeps the canonical
    spelled = [kind] if name in facts.get(kind, {}) else []
    headings = spelled or [k for k in kind_aliases(system, kind) if name in facts.get(k, {})][:1]
    if not headings:
        return None, []
    in_kind = False
    out = []
    for line in lines:
        m = re.match(r"^##\s+(\S+)\s*$", line)
        if m:
            in_kind = m.group(1) in headings
        cells = split_row(line) if in_kind else None
        if cells and cells[0] == name:
            continue
        out.append(line)
    write_doc(system, out)
    remaining = [k for k in kind_aliases(system, kind) if k != headings[0] and name in facts.get(k, {})]
    return headings[0], remaining


def rm_row_under(system: str, heading: str, name: str) -> bool:
    """Remove `name` from exactly the `## heading` given (a migration drops the legacy copy, never the canonical one)."""
    lines, facts = parse_doc(system)
    if name not in facts.get(heading, {}):
        return False
    in_kind, out = False, []
    for line in lines:
        m = re.match(r"^##\s+(\S+)\s*$", line)
        if m:
            in_kind = m.group(1) == heading
        cells = split_row(line) if in_kind else None
        if cells and cells[0] == name:
            continue
        out.append(line)
    write_doc(system, out)
    return True


def drop_empty_section(system: str, kind: str) -> bool:
    """Remove a `## kind` heading (and its empty table) once no row is left under it."""
    lines, facts = parse_doc(system)
    if kind not in facts or facts[kind]:
        return False
    out, skipping = [], False
    for line in lines:
        if line.startswith("#"):  # any heading ends the section — hand-written prose after the table survives
            skipping = re.match(rf"^##\s+{re.escape(kind)}\s*$", line) is not None
            if skipping:
                continue
        if not skipping:
            out.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip("\n") + "\n"
    doc_path(system).write_text(text, encoding="utf-8")
    return True


# ── discovery manifests ─────────────────────────────────────────────────────────────────────
def load_manifests() -> dict[str, dict]:
    """{file stem: manifest} — the kit's `context-db/discovery/*.json`, overlaid by the store's `_discovery/*.json`
    (same file name wins). A file that does not parse is kept as {'_error': …} so `--check` can report it."""
    out: dict[str, dict] = {}
    for d in (MANIFEST_DIR, ENV / "_discovery"):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(m, dict):
                    m = {"_error": f"{p}: top level must be an object"}
            except json.JSONDecodeError as e:
                m = {"_error": f"{p}: invalid JSON — {e}"}
            m["_file"] = str(p)
            out[p.stem] = m
    return out


def validate_manifests() -> list[str]:
    errs: list[str] = []
    for m in load_manifests().values():
        rel = m["_file"]
        if "_error" in m:
            errs.append(m["_error"])
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", str(m.get("system", ""))):
            errs.append(f"{rel}: 'system' must be a lowercase slug")
        if m.get("requires") is not None and m["requires"] not in SYSTEMS:
            errs.append(f"{rel}: requires '{m['requires']}' is not a capability flag {sorted(SYSTEMS)}")
        when = m.get("when")
        if when is not None and not (isinstance(when, dict) and isinstance(when.get("config"), str) and "equals" in when):
            errs.append(f"{rel}: 'when' must be {{\"config\": \"<dotted.key>\", \"equals\": <value>}}")
        facts = m.get("facts")
        if not isinstance(facts, list) or (not facts and not str(m.get("note") or "").strip()):
            errs.append(f"{rel}: 'facts' must be a list — empty only with a 'note' saying why the flag has no store fact yet")
            continue
        seen: set[str] = set()
        for f in facts:
            if not isinstance(f, dict):
                errs.append(f"{rel}: every fact must be an object")
                continue
            key = str(f.get("key", ""))
            where = f"{rel} [{key or '?'}]"
            if not FACT_KEY.match(key):
                errs.append(f"{where}: bad key (want <system>.<kind>[ <name>] or a dotted config key)")
                continue
            if key in seen:
                errs.append(f"{where}: duplicate key")
            seen.add(key)
            target = f.get("target", "row")
            if target not in ("row", "config"):
                errs.append(f"{where}: target must be row or config")
            if target == "row":
                head = key.split(" ", 1)[0]
                if head.count(".") != 1:
                    errs.append(f"{where}: a row key is <system>.<kind>[ <name>]")
                elif head.split(".")[0] != m.get("system"):
                    errs.append(f"{where}: row key belongs to system '{head.split('.')[0]}', manifest says '{m.get('system')}'")
            tool = f.get("tool")
            args = f.get("args", {})
            if not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z][\w-]*", tool):
                errs.append(f"{where}: tool must be an MCP tool name or one of {BUILTIN_TOOLS}")
            if not isinstance(args, dict):
                errs.append(f"{where}: args must be an object")
                args = {}
            need = {"cli": "cmd", "roster": "tool", "settings": "key", "derive": "from"}
            if tool in need and not isinstance(args.get(need[tool]), str):
                errs.append(f"{where}: tool {tool} needs args.{need[tool]}")
            if not isinstance(f.get("verify"), str) or not f["verify"].strip():
                errs.append(f"{where}: verify (one clause) is required")
            ttl = f.get("ttl_days")
            if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0:
                errs.append(f"{where}: ttl_days must be an integer ≥ 0 (0 = never stale)")
    # the same key in two manifests is a silent override unless their `when` guards exclude each other
    owners: dict[str, list[tuple[str, dict]]] = {}
    switches: set[str] = set()  # config keys a `when` may read: `user` facts in manifests that carry no `when`
    for m in load_manifests().values():
        if "_error" in m or not isinstance(m.get("facts"), list):
            continue
        for f in m["facts"]:
            if isinstance(f, dict) and isinstance(f.get("key"), str):
                owners.setdefault(f["key"], []).append((m["_file"], m))
                if f.get("target") == "config" and f.get("tool") == "user" and m.get("when") is None:
                    switches.add(f["key"])
    for m in load_manifests().values():
        when = m.get("when")
        if "_error" not in m and isinstance(when, dict) and isinstance(when.get("config"), str) and when["config"] not in switches:
            errs.append(f"{m['_file']}: `when` reads '{when['config']}', which no unguarded manifest declares as a "
                        "`user` config fact — the switch a guard reads is decided by the user, never discovered")
    for key, lst in sorted(owners.items()):
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                (sa, ma), (sb, mb) = lst[i], lst[j]
                if not mutually_exclusive(ma, mb):
                    errs.append(f"{sa} + {sb} both define '{key}' without excluding each other — give both a "
                                "`when` on the same config key with different values, or keep the key in one manifest")
    return errs


def fact_entries() -> list[tuple[dict, dict]]:
    return [(m, f) for m in load_manifests().values() if "_error" not in m
            for f in m.get("facts", []) if isinstance(f, dict) and isinstance(f.get("key"), str)]


def find_fact(key: str, name: str = "", cfg: dict | None = None) -> tuple[dict, dict] | None:
    """The manifest entry for a fact — the exact `<key> <name>` entry first, then the generic `<key>` one.
    A renamed kind resolves through its canonical name. Two manifests may define the same key when their
    `when` guards exclude each other (tracker-jira / tracker-github): with `cfg` the applicable one wins,
    without it the first in manifest order."""
    if key.count(".") == 1 and " " not in key:
        system, kind = key.split(".")
        key = f"{system}.{canonical_kind(system, kind)}"
    hits: dict[str, list[tuple[dict, dict]]] = {}
    for m, f in fact_entries():
        hits.setdefault(f["key"], []).append((m, f))
    exact = hits.get(f"{key} {name}", []) if name else []
    generic = hits.get(key, [])
    if not exact and not generic:
        return None
    if cfg is not None:  # an applicable manifest beats a non-applicable one, exact beats generic within each tier
        for cands in (exact, generic):
            for m, f in cands:
                if not applicable(m, cfg):
                    return m, f
    return (exact or generic)[0]


def mutually_exclusive(a: dict, b: dict) -> bool:
    """Two manifests never apply on the same machine: same `when.config`, different `equals`."""
    wa, wb = a.get("when"), b.get("when")
    return bool(wa and wb and wa.get("config") == wb.get("config") and wa.get("equals") != wb.get("equals"))


def applicable(m: dict, cfg: dict) -> str:
    """'' when the manifest applies on this machine, else why it does not."""
    req = m.get("requires")
    if req and (cfg.get("systems") or {}).get(req) is not True:
        return f"systems.{req} is not true here"
    when = m.get("when")
    if when and dotted_get(cfg, when["config"]) != when["equals"]:
        return f"{when['config']} is not {json.dumps(when['equals'])} here"
    return ""


def render(template, name: str, cfg: dict, shell: bool = False):
    """Fill `{name}`, `{config.<key>[i]}` and `{row.<system>.<kind>.<name>}` in an args template.
    `shell=True` (a command line or prose quoting one): every filled value is `shlex.quote`d — the name comes from a
    `NEEDS` line an untrusted surface may have supplied, so it must never be read by the shell as code (#131)."""
    if isinstance(template, dict):
        return {k: render(v, name, cfg, shell) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, name, cfg, shell) for v in template]
    if not isinstance(template, str):
        return template
    q = shlex.quote if shell else (lambda v: v)

    def sub(m: re.Match) -> str:
        # ONE pass over the template: a filled value is never scanned again, so a name carrying `}` or `{config.…}`
        # cannot re-open a placeholder; an unset placeholder names the fact, so it is quoted like a value.
        if m.group("name") is not None:
            return q(name) if name else "<name>"
        if m.group("nested") is not None:   # `{row.aws.profile.{name}}`: the raw name selects the row
            ref = f"{m.group('nested')}{name or '<name>'}{m.group('tail')}"
        else:
            ref = m.group("ref")
        if ref.startswith("config."):
            path, idx = ref[len("config."):], None
            mi = re.match(r"^(.*)\[(\d+)\]$", path)
            if mi:
                path, idx = mi.group(1), int(mi.group(2))
            v = dotted_get(cfg, path)
            if idx is not None:
                v = v[idx] if isinstance(v, list) and idx < len(v) else None
            return q(str(v)) if v not in (None, "", [], {}) else q(f"<{path} unset — kb.py discover {path}>")
        parts = ref[len("row."):].split(".", 2)
        if len(parts) == 3:
            v = get(*parts)
            return q(v) if v else q(f"<{parts[0]}.{parts[1]} {parts[2]} unset — kb.py discover {parts[0]}.{parts[1]} {parts[2]}>")
        return q(m.group(0)) if m.group("nested") is not None else m.group(0)
    return re.sub(r"(?P<name>\{name\})|\{(?P<nested>row\.[^{}]*)\{name\}(?P<tail>[^{}]*)\}|\{(?P<ref>(?:config|row)\.[^{}]+)\}",
                  sub, template)


def cli_provenance(template_cmd: str, rendered_cmd: str) -> str:
    """`tool:<first word of the command>` for a `cli` fact — from the rendered command, else the template's first word,
    else `tool:cli`; always something `PROVENANCE` accepts, so the plan's write line never names an unfilled
    `<config.x unset …>` placeholder as the tool."""
    for cmd in (rendered_cmd, template_cmd):
        first = cmd.split()[0] if cmd.split() else ""
        if PROVENANCE.match(f"tool:{first}"):
            return f"tool:{first}"
    return "tool:cli"


def discover_plan(key: str, name: str, cfg: dict) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in f"{key}{name}"):
        # a newline would print a forged `run:` line into the plan the caller executes (#131)
        raise SystemExit(f"kb: discover refuses a key or name with a control character: {json.dumps(f'{key} {name}'.strip())}")
    hit = find_fact(key, name, cfg)
    label = f"{key} {name}".strip()
    if hit is None:
        raise SystemExit(f"kb: no discovery manifest covers '{label}' — ask the user, then `kb.py set {key} {shlex.quote(name) if name else '<name>'} <value> --from user`; "
                         f"to make it discoverable add an entry to context-db/discovery/<system>.json (schema: its README.md)")
    m, f = hit
    target = f.get("target", "row")
    if target == "row" and " " in f["key"]:           # an exact entry names the row itself
        key, name = f["key"].split(" ", 1)
    out = [f"fact:    {label} → " + (f"row in {m['system']}.md" if target == "row" else "config.json key")]
    na = applicable(m, cfg)
    if na:
        out.append(f"status:  NOT APPLICABLE here — {na}")
    if target == "row":
        row = get_row(*key.split(".", 1), name) if name else None
        if row:
            out.append(f"current: {row['value']}  ({row['learned-from']})")
    else:
        cur = dotted_get(cfg, key)
        if cur not in (None, "", [], {}):
            out.append(f"current: {cur if isinstance(cur, str) else json.dumps(cur, ensure_ascii=False)}")
    tool = f["tool"]
    args = render(f.get("args", {}), name, cfg, shell=tool == "cli")
    if tool == "cli":
        out.append(f"run:     {args.get('cmd')}")
        try:  # the same command as an argv list, for a caller that runs it without a shell
            out.append(f"argv:    {json.dumps(shlex.split(str(args.get('cmd', ''))), ensure_ascii=False)}")
        except ValueError:
            pass
        prov = cli_provenance(str(f.get("args", {}).get("cmd", "")), str(args.get("cmd", "")))
    elif tool == "roster":
        out.append(f"check:   is `{args.get('tool')}` among this session's tools → true / false")
        prov = "tool:roster"
    elif tool == "settings":
        out.append(f"read:    `{args.get('key')}` from the shell environment (WORKSPACE_* identity: plugin /config option, else .claude/settings.local.json)")
        prov = "tool:settings"
    elif tool == "derive":
        out.append(f"derive:  from `{args.get('from')}`" + (f" with template {args['template']}" if args.get("template") else ""))
        prov = f"derived:{args.get('from')}"
    elif tool == "user":
        out.append("ask:     no tool settles this — ask the user once, with what you know")
        prov = "user"
    else:
        out.append(f"call:    {tool} {json.dumps(args, ensure_ascii=False)}")
        prov = f"tool:{tool}"
    out.append(f"verify:  {render(f['verify'], name, cfg, shell=tool == 'cli')}")
    if f.get("note"):
        out.append(f"note:    {render(f['note'], name, cfg, shell=tool == 'cli')}")
    ttl = f.get("ttl_days", 0)
    out.append(f"ttl:     {'never stale' if not ttl else f'{ttl} days'}")
    if target == "row":
        purpose = f" --purpose {shlex.quote(f['purpose'])}" if f.get("purpose") else ""
        out.append(f"write:   kb.py set {key} {shlex.quote(name) if name else '<name>'} <value>{purpose} --from {prov}")
    else:
        out.append(f"write:   kb.py config-set {key} <value>")
    return "\n".join(out)


def safe_config() -> dict | None:
    """config.json, or None when it is missing/unparseable — for hints that must never fail a read."""
    try:
        return load_config()
    except (OSError, ValueError):
        return None


def staleness(system: str, kind: str, name: str, row: dict[str, str], ttl_override: int | None = None,
              cfg: dict | None = None) -> dict | None:
    """None when the row is fresh (or user-given / never-stale), else {age, ttl, who, discoverable}.
    `cfg` picks the applicable manifest when two own the key; None falls back to manifest order."""
    who, d = provenance(row.get("learned-from", ""))
    if who == "user" or not row.get("value"):
        return None
    hit = find_fact(f"{system}.{kind}", name, cfg)
    ttl = hit[1].get("ttl_days", DEFAULT_TTL) if hit else DEFAULT_TTL
    if ttl_override is not None:
        ttl = ttl_override
    if ttl == 0:
        return None
    age = (dt.date.today() - d).days if d else None
    if age is not None and age <= ttl:
        return None
    return {"age": age, "ttl": ttl, "who": who, "discoverable": hit is not None}


def stale_rows(ttl_override: int | None = None) -> list[dict]:
    out = []
    cfg = safe_config()
    for system, kinds in all_facts().items():
        for kind, rows in kinds.items():
            for nm, row in rows.items():
                st = staleness(system, canonical_kind(system, kind), nm, row, ttl_override, cfg)
                if st:
                    out.append({"key": f"{system}.{kind}", "name": nm, "value": row["value"], "learned": row["learned-from"], **st})
    return out


# ── config.json ─────────────────────────────────────────────────────────────────────────────
def config_path() -> Path:
    return ENV / "config.json"


def load_config() -> dict:
    p = config_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:  # one line naming the file, never a traceback
        raise SystemExit(f"{p}: invalid JSON — {e}; fix it (or move it aside and `kb.py init --blank`)")
    except OSError as e:
        raise SystemExit(f"{p}: cannot read — {e}")


def save_config(cfg: dict) -> None:
    ENV.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def dotted_get(cur, path: str):
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def dotted_set(cfg: dict, path: str, value) -> None:
    parts = path.split(".")
    cur = cfg
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
        if not isinstance(cur, dict):
            raise SystemExit(f"{path}: '{part}' is not an object")
    cur[parts[-1]] = value


# Capability flags a skill may `requires:` — the kit never names an environment, only what a machine has.
# Kept in step with kit_verify.SYSTEMS and environment-template/config.json.
SYSTEMS = ("jira", "slack", "notion", "datalake", "airflow", "dbt",
           "aws_sso", "incident_io", "lattice", "signed_commits")
RENAMED_SYSTEMS = {"snowflake": "datalake"}  # old flag → new flag (2026-09-25); `migrate` moves the value
# The flags whose system is reached through an MCP server in the session (no CLI to probe from a shell): kit-health § 4
# says so instead of probing them; the others are a CLI (`aws`, `gh`/`git`) or an identity variable (`lattice`).
MCP_BACKED = ("jira", "slack", "notion", "datalake", "incident_io")
# The vocabulary each capability brings with it. A unit whose `requires` lacks the flag but whose added lines use
# one of these words is pre-flagged for the reviewer's judgment (review_evidence.py) — never failed outright: "a DAG of
# steps" is not an Airflow dependency. Words are matched whole, case-sensitively; generic terms (tracker, chat, datalake,
# BI tool, cloud account) are what a unit without the capability uses instead.
SYSTEM_VOCAB = {
    "jira": ("Jira", "JQL", "Atlassian"),
    "slack": ("Slack",),
    "notion": ("Notion",),
    "datalake": ("Snowflake", "BigQuery", "Redshift", "Databricks"),
    "airflow": ("Airflow", "DAG", "DAGs"),
    "dbt": ("dbt", "sqlfluff"),
    "aws_sso": ("AWS",),
    "incident_io": ("incident.io",),
    "lattice": ("Lattice",),
    "signed_commits": ("GPG", "gpg", "YubiKey", "1Password"),
}
# new flag → the legacy flag it starts from on the first migrate: the skills now gated on airflow/dbt ran
# only where the legacy flag was set, so seeding keeps them on there instead of switching them off silently
SEEDED_SYSTEMS = {"airflow": "snowflake", "dbt": "snowflake"}


CLOSE_REASON_KEYS = ("done", "wont_do", "cancelled")          # what the ticket-close skill reads
GITHUB_CLOSE_REASONS = {"done": "completed", "wont_do": "not planned", "cancelled": "not planned"}
RENAMED_CLOSE_REASON_KEYS = {"wont-do": "wont_do"}             # pre-2026-09-26 manifest spelling
RENAMED_CLOSE_REASON_VALUES = {"not_planned": "not planned"}   # `gh issue close --reason` wants the space


def migrate_close_reasons(cfg: dict) -> list[str]:
    """One key set and gh's exact strings under `tracker.close_reasons` on a GitHub tracker (in place)."""
    tracker = cfg.get("tracker") or {}
    if not isinstance(tracker, dict) or tracker.get("kind") != "github":
        return []
    reasons = tracker.get("close_reasons") or {}   # a store may carry `null`
    tracker["close_reasons"] = reasons
    done: list[str] = []
    for old, new in RENAMED_CLOSE_REASON_KEYS.items():
        if old in reasons:
            reasons.setdefault(new, reasons[old])
            del reasons[old]
            done.append(f"tracker.close_reasons.{old} → .{new}")
    for key, val in list(reasons.items()):
        if val in RENAMED_CLOSE_REASON_VALUES:
            reasons[key] = RENAMED_CLOSE_REASON_VALUES[val]
            done.append(f"tracker.close_reasons.{key}: {val!r} → {reasons[key]!r}")
    for key in CLOSE_REASON_KEYS:
        if key not in reasons:
            reasons[key] = GITHUB_CLOSE_REASONS[key]
            done.append(f"tracker.close_reasons.{key} added ({reasons[key]!r})")
    return done


def close_reason_problems(cfg: dict) -> list[str]:
    """Values `gh issue close --reason` would reject, or keys the skill reads that are missing (github only)."""
    tracker = cfg.get("tracker") or {}
    if not isinstance(tracker, dict) or tracker.get("kind") != "github":  # a non-object tracker is kit_verify's finding
        return []
    reasons = tracker.get("close_reasons") or {}
    out = [f"tracker.close_reasons.{k} missing" for k in CLOSE_REASON_KEYS if k not in reasons]
    out += [f"tracker.close_reasons.{k} = {v!r} is not one of {sorted(set(GITHUB_CLOSE_REASONS.values()))}"
            for k, v in reasons.items() if k in CLOSE_REASON_KEYS and v not in GITHUB_CLOSE_REASONS.values()]
    # extra keys (e.g. a `duplicate` reason newer gh accepts) are the machine's business — not checked
    return out


def systems_of(cfg: dict) -> dict:
    """`cfg["systems"]` as the flag object: `null`/absent is `{}` (nothing set); a list or a string is the one-line
    type error kit-verify already reports for that store, never a traceback from `dict(list)` / `str.get`."""
    systems = cfg.get("systems") or {}
    if not isinstance(systems, dict):
        raise SystemExit(f"kb: systems must be an object of true/false flags, got {systems!r} — "
                         f"fix {config_path()} by hand (see environment-template/config.json)")
    return systems


def migrate_config(cfg: dict) -> list[str]:
    """Bring `systems` up to the kit's flag set in place. Returns one line per change (empty = current)."""
    done: list[str] = []
    systems = cfg["systems"] = systems_of(cfg)  # `systems: null` reads as nothing set: migrate adds every flag
    legacy = dict(systems)
    for old, new in RENAMED_SYSTEMS.items():
        if old in systems:
            if new not in systems:
                systems[new] = systems[old]
                done.append(f"systems.{old} → systems.{new} ({str(systems[new]).lower()})")
            else:
                done.append(f"systems.{old} dropped (systems.{new} already set)")
            del systems[old]
    sources = (cfg.get("self_assessment") or {}).get("sources") or []
    for i, s in enumerate(sources):
        if s in RENAMED_SYSTEMS and RENAMED_SYSTEMS[s] not in sources:
            sources[i] = RENAMED_SYSTEMS[s]
            done.append(f"self_assessment.sources: {s} → {sources[i]}")
    done += migrate_close_reasons(cfg)
    for name in SYSTEMS:
        if name not in systems:
            seed = SEEDED_SYSTEMS.get(name)
            if seed in legacy:
                systems[name] = bool(legacy[seed])
                done.append(f"systems.{name} added ({str(systems[name]).lower()}, from the old systems.{seed} — "
                            f"`kb.py config-set systems.{name} {str(not systems[name]).lower()}` if that is wrong here)")
            else:
                systems[name] = False
                done.append(f"systems.{name} added (false — `kb.py config-set systems.{name} true` if this machine has it)")
    return done


def unmet_units(cfg: dict) -> list[str]:
    """`<unit> (<flag>, …)` for every skill/agent whose `metadata.requires` names a flag that is not true in `cfg`."""
    import frontmatter as fmt  # noqa: E402  (sibling module; imported here so kb.py stays importable on its own)
    systems = systems_of(cfg)
    out = []
    for f in fmt.units(KIT):
        off = [r for r in fmt.requires_of(fmt.load(f)) if systems.get(r) is not True]
        if off:
            out.append(f"{fmt.unit_name(f)} ({', '.join(off)})")
    return out


def migrate_kinds(apply: bool = True) -> list[str]:
    """Move rows filed under a renamed kind to the canonical one. Returns one line per row; a `CONFLICT` line
    (both headings hold the name with different values) is left for the user and stays pending."""
    done: list[str] = []
    for (system, old), (_, new) in RENAMED_KINDS.items():
        _, facts = parse_doc(system)
        for nm, row in list(facts.get(old, {}).items()):
            cur = facts.get(new, {}).get(nm)
            if cur is None or not cur["value"]:
                if apply:
                    set_fact(system, new, nm, row["value"], row["purpose"], row["learned-from"], keep_learned=True)
                    rm_row_under(system, old, nm)
                done.append(f"{system}.{old} '{nm}' → {system}.{new}")
            elif cur["value"] == row["value"]:
                if apply:
                    rm_row_under(system, old, nm)
                done.append(f"{system}.{old} '{nm}' dropped (same value already under {system}.{new})")
            else:
                done.append(f"CONFLICT {system}.{old} '{nm}' = {row['value']} vs {system}.{new} = {cur['value']} — "
                            f"verify (`kb.py discover {system}.{new} {nm}`), `kb.py rm {system}.{old} '{nm}'`, re-run migrate")
        if apply and old in facts:
            drop_empty_section(system, old)
    return done


# Top-level config.json keys a store may leave out: absent means the kit default (commit_style.py falls back to
# `conventional`; cost-report runs in private mode; datalake.kind is read only where systems.datalake is true; `kit`
# holds kit.install_mode, which setup.sh records — absent until it has run, and kit-health § 1 says so, #34).
# kit_verify reads this set; `config_key_drift()` keeps blank_config() and environment-template/config.json in step.
OPTIONAL_CONFIG_KEYS = frozenset({"commits", "cost", "datalake", "kit"})
TEMPLATE_CONFIG = KIT / "environment-template" / "config.json"


def template_config_keys() -> set[str]:
    """The top-level keys environment-template/config.json documents (the `_comment` keys aside)."""
    tmpl = json.loads(TEMPLATE_CONFIG.read_text(encoding="utf-8"))
    return {k for k in tmpl if not k.startswith("_")}


CLOSED_SECTIONS = frozenset({"systems"})  # a fixed flag set: an unknown key there is a typo, not a new entry
# keys a script writes that the template does not document (yet): accepted like template keys
EXTRA_CONFIG_KEYS = frozenset({"leaks"})


def _shape(v) -> str:
    if isinstance(v, bool):
        return "true/false"
    if isinstance(v, (int, float)):
        return "a number"
    if isinstance(v, str):
        return "a string"
    if isinstance(v, list):
        return "a list"
    if isinstance(v, dict):
        return "an object"
    return "null"


def config_value_problem(key: str, val) -> str:
    """'' when `config-set <key> <val>` fits environment-template/config.json, else why not. Only what the template
    defines is checked: an unknown top-level key, an unknown flag in a closed section (`systems`), and a value whose
    shape (object / list / true-false / string / number) differs from the template's at that path. Below a path the
    template leaves open (`cost.columns.<x>`, `tracker.mcp_tools.<x>`) any value is accepted."""
    parts = key.split(".")
    tmpl = json.loads(TEMPLATE_CONFIG.read_text(encoding="utf-8"))
    known = template_config_keys() | OPTIONAL_CONFIG_KEYS | EXTRA_CONFIG_KEYS
    if parts[0] not in known:
        return f"unknown config key '{parts[0]}' — known: {', '.join(sorted(known))}"
    node = tmpl
    for i, part in enumerate(parts):
        if not isinstance(node, dict) or part not in node:
            if i > 0 and parts[i - 1] in CLOSED_SECTIONS and i == 1:
                return f"'{parts[0]}' has no flag '{part}' — known: {', '.join(sorted(k for k in node if not k.startswith('_')))}"
            return ""  # the template stops here: an open map, nothing to compare
        node = node[part]
    if node is None or (isinstance(node, str) and isinstance(val, (int, float)) and not isinstance(val, bool)):
        return ""  # template null = any; a number where the template shows a placeholder string is an id, fine
    if _shape(node) != _shape(val):
        return f"'{key}' is {_shape(node)} in environment-template/config.json, not {_shape(val)} ({json.dumps(val)})"
    return ""


def config_key_drift() -> list[str]:
    """Where blank_config() and the template disagree on the key set — empty when they are one list:
    every non-optional template key is in the blank store, every blank key is documented by the template, the
    template's `systems` flags are exactly kb.SYSTEMS, and every structural key a script reads is documented."""
    want = template_config_keys()
    blank = {k for k in blank_config() if not k.startswith("_")}
    out = [f"template key '{k}' missing from kb.blank_config()" for k in sorted(want - blank - OPTIONAL_CONFIG_KEYS)]
    out += [f"kb.blank_config() key '{k}' is not in environment-template/config.json" for k in sorted(blank - want)]
    tmpl = json.loads(TEMPLATE_CONFIG.read_text(encoding="utf-8"))
    if set(tmpl.get("systems", {})) != set(SYSTEMS):
        out.append(f"template systems.* {sorted(tmpl.get('systems', {}))} != kb.SYSTEMS {sorted(SYSTEMS)}")
    for section, keys in STRUCTURAL.items():
        for k in keys:
            if k not in (tmpl.get(section) or {}):
                out.append(f"template {section}.{k} missing (a script reads it — kb.STRUCTURAL)")
            if k not in (blank_config().get(section) or {}):
                out.append(f"kb.blank_config() {section}.{k} missing (a script reads it — kb.STRUCTURAL)")
    return out


def blank_config() -> dict:
    return {
        "_comment": "Structural switches the kit's scripts branch on. Facts (ids, names, hosts) go in the "
                    "<system>.md tables next to this file via `kb.py set`. Edit by hand or `kb.py config-set`.",
        "environment": "local",
        "tracker": {"kind": "none", "key_regex": "", "url_template": "", "mcp_tools": {}, "close_reasons": {}, "repos": []},
        "github": {"org": "", "review_bot": "", "bots": ["dependabot[bot]", "renovate[bot]", "github-actions[bot]"],
                   "signed_commits": False, "sandbox_token_prefix": "", "owner_teams": []},
        "slack": {"enabled": False, "domain": ""},
        "systems": {name: False for name in SYSTEMS},
        "tz_default": "UTC",
        "domains": [],
        "labels": {"shared": ["bug", "documentation", "enhancement"], "repos": {}},
        "self_assessment": {"sources": ["github", "meetings", "1on1"], "report": "week-file", "scope": ""},
        "diagrams": {"repos": {}},
        "commits": {"default": "conventional", "repos": {}},
    }


# ── init ────────────────────────────────────────────────────────────────────────────────────
def init_blank() -> list[str]:
    made = []
    ENV.mkdir(parents=True, exist_ok=True)
    if not config_path().is_file():
        save_config(blank_config())
        made.append("config.json")
    for system, kinds in DEFAULT_KINDS.items():
        p = doc_path(system)
        if not p.is_file():
            p.write_text(new_doc(system, kinds), encoding="utf-8")
            made.append(p.name)
    return made


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────
def split_key(key: str) -> tuple[str, str]:
    if "." not in key:
        raise SystemExit(f"key must be <system>.<kind>, got '{key}'")
    system, kind = key.split(".", 1)
    return system, kind


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kb.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get"); g.add_argument("key"); g.add_argument("name")
    s = sub.add_parser("set"); s.add_argument("key"); s.add_argument("name"); s.add_argument("value")
    s.add_argument("--purpose", default=""); s.add_argument("--from", dest="learned", default="")
    d = sub.add_parser("rm"); d.add_argument("key"); d.add_argument("name")
    ls = sub.add_parser("list"); ls.add_argument("key", nargs="?", default="")
    sub.add_parser("values")
    c = sub.add_parser("config"); c.add_argument("key", nargs="?", default="")
    cs = sub.add_parser("config-set"); cs.add_argument("key"); cs.add_argument("value")
    cs.add_argument("--force", action="store_true", help="write even when the key or the value's shape differs from the template")
    i = sub.add_parser("init"); grp = i.add_mutually_exclusive_group(required=True)
    grp.add_argument("--blank", action="store_true")
    grp.add_argument("--personal", action="store_true", help="blank store + the zero-config GitHub-only fill: identity from gh, repos from the workspace clones, tz from the OS — no questions")
    sub.add_parser("path")
    mg = sub.add_parser("migrate")
    mg_mode = mg.add_mutually_exclusive_group()
    mg_mode.add_argument("--check", action="store_true", help="dry run: list pending changes, exit 3 if any")
    mg_mode.add_argument("--off", action="store_true", help="only print the skills/agents not applicable here (a required flag is false); no migration")
    dv = sub.add_parser("discover"); dv.add_argument("key", nargs="?", default=""); dv.add_argument("name", nargs="?", default="")
    dv.add_argument("--all", action="store_true", help="every manifest fact with its applicability and state here")
    dv.add_argument("--check", action="store_true", help="validate the manifests (kit-verify runs this)")
    st = sub.add_parser("stale"); st.add_argument("--days", type=int, default=None, help="override every ttl_days")
    st.add_argument("--check", action="store_true", help="exit 3 when any row is stale")
    a = ap.parse_args(argv[1:])

    if a.cmd == "path":
        print(ENV)
    elif a.cmd == "get":
        system, kind = split_key(a.key)
        row = get_row(system, kind, a.name)
        if row is None:
            print(f"kb: no value for {a.key} '{a.name}' in {doc_path(system).relative_to(ROOT) if ENV.is_dir() else ENV} — "
                  f"`kb.py discover {a.key} '{a.name}'` prints how to find it; then: "
                  f"kb.py set {a.key} '{a.name}' <value> --from tool:<tool-name>", file=sys.stderr)
            return 1
        print(row["value"])  # the value first — the ttl hint below is best-effort and must never turn a hit into a miss
        try:
            st = staleness(system, canonical_kind(system, kind), a.name, row, cfg=safe_config())
        except Exception as e:  # noqa: BLE001 — a hint, not a result
            st = None
            print(f"kb: (stale hint skipped: {e})", file=sys.stderr)
        if st:
            age = f"{st['age']}d old" if st["age"] is not None else "undated"
            print(f"kb: {a.key} '{a.name}' is {age} (ttl {st['ttl']}d, {row['learned-from']}) — still returned; "
                  f"re-verify with `kb.py discover {a.key} '{a.name}'`", file=sys.stderr)
    elif a.cmd == "set":
        system, kind = split_key(a.key)
        print(f"{set_fact(system, kind, a.name, a.value, a.purpose, a.learned)} {a.key} {a.name}")
    elif a.cmd == "rm":
        system, kind = split_key(a.key)
        gone, remaining = rm_fact_report(system, kind, a.name)
        if gone is None:
            print(f"no such row {a.key} {a.name}")
            return 1
        print(f"removed {system}.{gone} {a.name}" + (f" (a row under ## {', ## '.join(remaining)} still holds the name — "
                                                     f"`kb.py get` answers from it until `kb.py migrate`)" if remaining else ""))
    elif a.cmd == "list":
        want_sys, want_kind = (a.key.split(".", 1) + [""])[:2] if a.key else ("", "")
        for system, kinds in all_facts().items():
            if want_sys and system != want_sys:
                continue
            for kind, rows in kinds.items():
                if want_kind and kind not in kind_aliases(system, want_kind):
                    continue  # `list slack.channels` and `list slack.channel` both show rows under either heading
                for nm, row in rows.items():
                    print(f"{system}.{kind}\t{nm}\t{row['value']}\t{row['purpose']}\t{row['learned-from']}")
    elif a.cmd == "values":
        seen = set()
        for kinds in all_facts().values():
            for rows in kinds.values():
                for row in rows.values():
                    v = row["value"]
                    if v and v not in seen:
                        seen.add(v)
                        print(v)
    elif a.cmd == "config":
        cfg = load_config()
        if not cfg:
            print("kb: no env/config.json — run `kb.py init --blank` (or `kb.py import <dir>`)", file=sys.stderr)
            return 1
        v = dotted_get(cfg, a.key) if a.key else cfg
        if v is None:
            return 1
        print(v if isinstance(v, (str, int, float)) and not isinstance(v, bool) else json.dumps(v, indent=None if a.key else 2, ensure_ascii=False))
    elif a.cmd == "config-set":
        cfg = load_config() or blank_config()
        try:
            val = json.loads(a.value)
        except json.JSONDecodeError:
            val = a.value
        why = config_value_problem(a.key, val)
        if why and not a.force:
            raise usage_error(f"config-set refused: {why} — fix the value, or --force to write it anyway")
        dotted_set(cfg, a.key, val)
        save_config(cfg)
        print(f"set {a.key}")
    elif a.cmd == "migrate":
        cfg = load_config()
        if not cfg:
            print("kb: no env/config.json — run `kb.py init --blank` (or `kb.py import <dir>`)", file=sys.stderr)
            return 1
        if a.off:
            off = unmet_units(cfg)
            print("\n".join(off) if off else "every skill/agent is applicable here")
            return 0
        done = migrate_config(cfg)
        if a.check:
            done += migrate_kinds(apply=False)
            print("\n".join(f"pending: {d}" for d in done) if done else "env store already current")
            return 3 if done else 0
        if done:
            save_config(cfg)
        done += migrate_kinds(apply=True)
        print("\n".join(done) if done else "env store already current")
        if any(d.startswith("CONFLICT") for d in done):
            print("conflicts stay pending until resolved by hand (see the CONFLICT lines)")
        off = unmet_units(cfg)
        if off:
            print("not applicable on this machine (a required flag is false): " + "; ".join(off))
    elif a.cmd == "discover":
        if a.check:
            errs = validate_manifests()
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
            if errs:
                print(f"DISCOVERY MANIFESTS INVALID — {len(errs)} problem(s)", file=sys.stderr)
                return 1
            ms = load_manifests()
            shadowed = sorted(stem for stem, m in ms.items()
                              if m["_file"].startswith(str(ENV)) and (MANIFEST_DIR / f"{stem}.json").is_file())
            for stem in shadowed:  # informational: the store's file replaced the kit's whole — every kit fact not in it is gone here
                print(f"  ~ {stem}.json: the store's _discovery/ copy replaces the kit manifest on this machine "
                      "(facts only the kit's file has are not discoverable here)")
            print(f"OK — {len(ms)} manifest(s), {len(fact_entries())} facts" + (f", {len(shadowed)} shadowed" if shadowed else ""))
            return 0
        cfg = load_config()
        if a.all:
            for stem, m in load_manifests().items():
                if "_error" in m:
                    print(f"{stem}: {m['_error']}")
                    continue
                na = applicable(m, cfg)
                print(f"{stem}  (system {m['system']}; {'not applicable — ' + na if na else 'applicable'})")
                for f in m.get("facts", []):
                    key = f.get("key", "?")
                    if f.get("target", "row") == "config":
                        cur = dotted_get(cfg, key)
                        state = "set" if cur not in (None, "", [], {}) else "—"
                    elif " " in key:
                        s_, k_, n_ = key.replace(" ", ".", 1).split(".", 2)
                        state = "set" if get(s_, k_, n_) else "—"
                    else:
                        s_, k_ = key.split(".")
                        n = len(kind_rows(all_facts().get(s_, {}), s_, k_))
                        state = f"{n} row(s)" if n else "—"
                    print(f"  {key:<32} {f.get('target', 'row'):<6} {f.get('tool', '?'):<32} ttl {f.get('ttl_days', 0):<4} {state}")
            return 0
        if not a.key:
            raise SystemExit("kb: discover <system>.<kind> [<name>] | <config.key> | --all | --check")
        print(discover_plan(a.key, a.name, cfg))
    elif a.cmd == "stale":
        rows = stale_rows(a.days)
        for r in rows:
            age = f"{r['age']}d" if r["age"] is not None else "undated"
            hint = f"kb.py discover {r['key']} '{r['name']}'" if r["discoverable"] else "no manifest — re-verify by hand or `--from user`"
            print(f"{r['key']}\t{r['name']}\t{r['value']}\t{age} > ttl {r['ttl']}d\t{r['learned']}\t{hint}")
        if not rows:
            print("no stale rows")
        return 3 if rows and a.check else 0
    elif a.cmd == "init" and a.personal:
        import personal  # noqa: E402  (sibling module; imported here so kb.py stays importable on its own)
        lines, _facts = personal.apply_store(ROOT)
        print(f"env store at {ENV.relative_to(ROOT) if str(ENV).startswith(str(ROOT)) else ENV}: personal path")
        for ln in lines:
            print(f"  {ln}")
    elif a.cmd == "init":
        made = init_blank()
        print(f"env store at {ENV.relative_to(ROOT) if str(ENV).startswith(str(ROOT)) else ENV}: " + (", ".join(made) if made else "already present, nothing written"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
