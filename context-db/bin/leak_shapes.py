"""leak_shapes.py — the generic SHAPES of environment facts the kit must never carry.

One list for every scanner: `kit-health` § leaks (every kit file, plus the values THIS machine's env store
holds) and `kit_verify --no-env` (a unit's own file, env-free — what a contributor's PR can run). No
organisation's own values live here: those are read at run time from the env store.

`SKIP_LINE` exempts the lines that legitimately hold such shapes — the frontmatter declarations
(`requires:`, `tools:`, `facts:`); `SKIP_FILES` the scanners' own files (this module, kit-health.py, the
allow-list) — a per-file rule, so a doc line that merely mentions `leak_shapes.py` is still scanned.
`allowed()` reads `skills/kit-health/allow.txt` — one regex per line, anchored at the start of `<path>:<match>`
(`$` for an exact match) — the ONE allow-list both scanners honour, so a provenance line kit-health accepts is
not a `kit-verify` failure. `shapes(tracker_kind, key_regex, ignore_case=…)` is the shape list a scanner uses: the generic list always (kit-verify's
env-free scan, and kit-health on any tracker), plus this environment's `tracker.key_regex` as a second
ticket shape on a Jira-style tracker.

`configured_values(cfg, facts, …)` is the *value* half — this environment's own facts and config, gathered
into scan-ready patterns (org, tracker repos/site/project, Slack channels/domain, `tz_default`, …); shared by
kit-health's leak scan and `kit_profile.py public-text-check` so the two never disagree about what counts as
this environment's own value. `cross_org_shapes(owner)` is public-text-check's one extra shape: an inline
`<org>/<repo>#<n>` naming a different org than the repo the text is being posted to.
Stdlib only.
"""
from __future__ import annotations
import functools
import re
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
ALLOW_FILE = KIT / "skills" / "kit-health" / "allow.txt"

LEAK_SHAPES = [
    (r"\b[CDG]0[A-Z0-9]{7,9}\b", "Slack channel/DM id"),
    (r"\bU0[A-Z0-9]{7,9}\b", "Slack user id"),
    (r"\bcustomfield_\d+\b", "tracker custom-field id"),
    (r"(?<![\w./:-])\d{12}(?![\w.-])", "12-digit account id"),
    # a ticket key: a letters-only prefix (`W37-2026` is a week, not a key) that is not a well-known acronym followed by
    # a number (`AES-256`, `RSA-2048`, `ARM-64`, `SHA-256`); kit-health adds the tracker's own `key_regex` beside this
    (r"\b(?!(?:UTF|ISO|SHA|RFC|PEP|CVE|MD|HTTP|TLS|KEY|ABC|UTC|API|SQL|AWS|IAM|EKS|ADR|ERR|PR|CI|GH|ID|TF|AES|RSA|ARM|DES|DSA|ECC|"
     r"HMAC|PBKDF|PKCS|FIPS|NIST|IEEE|ANSI|DIN|GPT|IPV|TCP|UDP|EC|CPU|GPU|RAM|USB|HDMI|PCI|X|V)-)[A-Z]{2,10}-\d{2,6}\b", "ticket key"),
    # a real number behind a placeholder prefix (#92): the ticket-key shape above exempts `KEY-` / `ABC-`, so the
    # examples keep to the canonical numbers and a slug placeholder (`KEY-123`, `KEY-456`, `key-123-<slug>`)
    (r"\b(?:KEY|ABC)-(?!(?:123|456)\b)\d{2,6}\b", "real-looking number behind a placeholder prefix — use KEY-123 / KEY-456"),
    (r"\bkey-(?!(?:123|456)-)\d{2,6}-[a-z0-9]", "real-looking ticket slug or topic — use key-123-<slug>"),
    # a concrete claude.ai connector tool id (#94): `mcp__claude_ai_<connector>__<tool>` names the connector as one install
    # named it (a tool a GitHub Action or the kit itself serves has one fixed name and is not matched), so it leaks the
    # install and breaks on every other — write the placeholder form `mcp__<server>__<tool>` or the bare tool name
    (r"\bmcp__claude_ai_[A-Za-z0-9_-]+__[A-Za-z0-9_]+", "concrete claude.ai connector tool id (install-specific) — use mcp__<server>__<tool> or the bare tool name"),
    # third-party attribution (#93, #95): a possessive colleague / former employer followed by a repo names someone
    # else's work and workplace; provenance is the owner's own decision line or nothing
    (r"\b(?:[Cc]olleague|[Cc]o-?worker|[Tt]eammate|[Ee]x-?employer|[Ff]ormer (?:employer|team|colleague))(?:'s|\u2019s|s')\s+(?:`(?!<)[^`]+`|[\w.-]+/[\w.-]+)",
     "third-party attribution (a colleague's or former employer's repo) — drop the line; provenance is the owner's own decision"),
    (r"\bsbx\b", "a specific sandbox product's CLI — say \"a sandbox shell\" or name the capability"),
    # a pointer to a personal memory note (`memory \`x\``, `memory note \`x\``, `Memories: \`x\``, "in the memory note"):
    # the notes live in one machine's auto-memory; the content belongs in the skill's own reference/*.md
    (r"\b[Mm]emor(?:y(?: note)?|ies:)\s*`[a-z0-9-]+(?:\.md)?`|\b(?:in|the) memory notes?\b|\bMemories:",
     "memory-note pointer (notes do not ship with the kit — the content belongs in the skill's reference/*.md)"),
    (r"\b[0-9a-f]{32}\b", "32-hex id (Notion page/database)"),
    (r"\b[a-z0-9-]+\.(?:slack\.com|atlassian\.net|awsapps\.com|snowflakecomputing\.com)\b", "org host"),
    (r"\b(?:America|Europe|Asia|Africa|Australia|Pacific|Atlantic|Indian)/[A-Z][A-Za-z_]+\b", "timezone literal"),
    # the variable a sandbox exports per job, in any expansion (`$CLAUDE_JOB_DIR`, `${CLAUDE_JOB_DIR}/tmp`): unset on a host,
    # so a path or a test built on it silently changes meaning there; `kit_profile.py scratch` is the portable path
    (r"\$\{?CLAUDE_JOB_DIR\b", "sandbox-only variable (unset on a host) — use `kit_profile.py scratch`"),
    (r"(?<![\w-])/run/sandbox\b", "sandbox-only path (absent on a host) — test the capability the step needs, not the mount"),
    # a sandbox stated as the universe ("this sandbox", "the sandbox has no …", "sandbox-only"): the kit runs on any
    # machine, so a body says what is true generically or makes it a conditional — "where … (a sandbox), …"
    (r"\b(?:[Tt]his|[Tt]he|[Ii]n the|[Oo]n the|[Ff]rom the) sandbox\b|\bsandbox-only\b|\b[Ss]andbox (?:can(?:'|\u2019)?t|cannot|has no|lacks)\b",
     "universal sandbox wording (the kit runs on any machine) — say what is true generically, or make it a conditional"),
]
SKIP_LINE = re.compile(r"^\s*(requires|facts):")  # not `tools:`: an agent's roster is where an install-specific MCP id hides (#94)
SKIP_FILES = frozenset({"leak_shapes.py", "kit-health.py", "allow.txt"})
# One skip rule for every scanner — kit-health's tree walk and the review gate (diff and --tree) decide the same:
# a `fixtures/` directory holds deliberate test data (decoy leaks the tests must see), a binary suffix has no lines.
# `node_modules` is a vendored dependency tree (`skills/pr-open/mermaid-check.mjs` needs `npm ci` there): third-party
# text changes with every dependency bump, so an allow.txt entry per hit is not workable — the tree is skipped
# outright, the same way `fixtures/` is, rather than scanned and allowed.
SKIP_DIRS = frozenset({"fixtures", "__pycache__", "node_modules"})
SKIP_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".jsonl", ".pyc"})


def skip_path(rel: str) -> bool:
    """True when no leak scanner reads `rel` (a path relative to the kit root, `/`-separated)."""
    parts = rel.split("/")
    return (parts[-1] in SKIP_FILES or any(d in SKIP_DIRS for d in parts[:-1])
            or any(rel.lower().endswith(x) for x in SKIP_SUFFIXES))


def shapes(tracker_kind: str | None = None, key_regex: str | None = None, *,
           ignore_case: bool = False) -> list[tuple[re.Pattern, str]]:
    """The compiled shape list for one scanner: the generic list on every tracker (a key from ANOTHER environment is
    exactly the leak a shared kit risks, so the generic ticket shape is never dropped), plus, on a tracker with a
    compiling `key_regex`, that regex as a second ticket shape so this environment's own keys are caught even when
    they do not fit the generic form. An invalid regex is kit-verify's finding; nothing is added for it.
    `ignore_case` (default False, kit-health's own case-sensitive scan of kit files) compiles `key_regex` with
    `re.IGNORECASE` too — `kit_profile.py public-text-check` wants this: a lane name lower-cases its tracker key
    (`<key>-<n>-<topic>`), and the generic LEAK_SHAPES ticket shape is already case-shaped (upper-case prefix),
    so only `key_regex` needs the flag."""
    out = [(re.compile(rx), what) for rx, what in LEAK_SHAPES]
    flags = re.IGNORECASE if ignore_case else 0
    if key_regex and tracker_kind != "github":  # a GitHub key is `#12`: no id to leak, the generic shape already covers foreign keys
        try:
            out.append((re.compile(key_regex, flags), "ticket key (tracker.key_regex)"))
        except re.error:
            pass
    return out


@functools.lru_cache(maxsize=None)
def allowed(path: Path = ALLOW_FILE) -> tuple[re.Pattern, ...]:
    """The allow-list regexes (`<path relative to .claude>:<matched text>`), compiled once per process and file;
    a line that does not compile is skipped with ONE warning on stderr rather than killing the scan. Missing file =
    no allow-list."""
    import sys
    out: list[re.Pattern] = []
    if not path.is_file():
        return ()
    for n, ln in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        rx = re.split(r"\s+#", ln, maxsplit=1)[0].strip()
        if not rx or ln.lstrip().startswith("#"):
            continue
        try:
            out.append(re.compile(rx))
        except re.error as e:
            print(f"leak_shapes: {path.name}:{n}: allow-list regex does not compile ({e}) — line ignored", file=sys.stderr)
    return tuple(out)


def is_allowed(rel: str, hit: str, allow) -> bool:
    """An allow-list entry is anchored at the start of `<path>:<match>` (a bare `acme` cannot allow `notacme`) and must
    consume the whole path and its colon (`docs/a` does not allow a hit in `docs/ab.md`); append `$` for an
    exact match."""
    key = f"{rel}:{hit}"
    return any((m := a.match(key)) is not None and m.end() > len(rel) for a in allow)


def line_hits(line: str, pats, rel: str = "", allow=None, keep=None) -> list[tuple[str, str]]:
    """(what, matched text) for EVERY shape match on `line` that is not allow-listed (an allowed first match no
    longer hides a second value on the same line). A span already claimed by an earlier shape — allowed or reported —
    is not reported again, so a key that fits both the generic ticket shape and `tracker.key_regex` is one hit.
    `keep(match)` (optional) returns False for a match the caller exempts on its own terms."""
    out: list[tuple[str, str]] = []
    taken: list[tuple[int, int]] = []
    for rx, what in pats:
        for m in rx.finditer(line):
            a, b = m.span()
            if a == b or any(a < y and x < b for x, y in taken):
                continue
            taken.append((a, b))
            if allow and is_allowed(rel, m.group(0), allow):
                continue
            if keep is not None and not keep(m):
                continue
            out.append((what, m.group(0)))
    return out


def scan(text: str, shapes: list[tuple[re.Pattern, str]] | None = None, rel: str = "",
         allow=None) -> list[tuple[int, str, str]]:
    """(line number, what, matched text) for every un-allowed shape hit in `text` — every match on a line (`line_hits`),
    skipping SKIP_LINE lines and, when `rel` (the file's path relative to .claude) and `allow` are given, the
    allow-listed `<rel>:<match>` hits."""
    pats = shapes if shapes is not None else [(re.compile(rx), what) for rx, what in LEAK_SHAPES]
    out: list[tuple[int, str, str]] = []
    for n, line in enumerate(text.split("\n"), 1):
        if SKIP_LINE.search(line):
            continue
        out += [(n, what, hit) for what, hit in line_hits(line, pats, rel, allow)]
    return out


# ── configured values — this environment's own facts and config, not a generic shape ──────────────────────────
# Shared by kit-health's leak scan (every kit file) and `kit_profile.py public-text-check` (one outgoing PR/issue
# body): both need the SAME set of "this environment's own values" so the two scanners never drift on what
# counts as a leak. `GENERIC`/`COMMON_REPO_NAMES`/`keep_value`/`redact`/`common_word_kinds` are the shared
# predicates; `configured_values` is the shared value-gathering (kit-health composes it with its own
# kit-tree-specific additions — the dependency-address exclusion, `identity_values` — public-text-check uses it
# as is).
GENERIC = {"true", "false", "none", "jira", "github", "slack", "notion", "datalake", "airflow", "dbt",
           "issues", "main", "master"}
# bare repo names that half of GitHub has and the kit uses as ordinary words: `setup.sh --personal` fills
# `tracker.repos` from `gh repo list` on a fresh workspace, and a `config` repo flagged every kit file (#118).
# The full `owner/<repo>` slug and the `<org>/<repo>` path shape still catch these repos.
COMMON_REPO_NAMES = {"config", "configs", "dotfiles", "docs", "notes", "scripts", "tools", "utils", "setup",
                     "infra", "test", "tests", "templates", "examples", "sandbox", "playground", "website",
                     "blog", "archive", "backup", "workspace", "projects"}


def keep_value(v: str, kind: str = "", common: "set[str] | frozenset[str]" = frozenset()) -> bool:
    """Is this configured value worth scanning for? Not when it is short, generic or a placeholder — those match
    everywhere and mean nothing. For an env-store ROW (`kind` given): a common-word kind (`common_word: true` in its
    manifest — first names, teams, labels) skips a plain single word but still scans a value with a space or hyphen
    (`First Last`, `<team>-platform` are the leaks the scan exists for), and any kind skips a plain word under six
    letters. A config or identity value (no `kind`) keeps the old four-character floor: a short org or
    login is the leak most worth catching."""
    s = v.strip()
    if len(s) < 4 or s.lower() in GENERIC or (s.isdigit() and len(s) < 6) or s.startswith("<"):
        return False
    if not kind:
        return True
    plain_word = re.fullmatch(r"[A-Za-z]+", s) is not None
    if plain_word and kind in common:
        return False
    if plain_word and len(s) < 6:
        return False
    return True


def redact(what: str, value: str) -> str:
    """`<kind>:<first2>…` — the report and the audit log name the kind and two characters, never the value.
    Shapes give their name as `what`; configured values their key."""
    kind = what.split(" (")[0].replace("env fact ", "").strip("`")
    return f"{kind}:{value[:2]}…" if len(value) > 2 else f"{kind}:…"


def common_word_kinds(manifests: dict) -> set[str]:
    """`<system>.<kind>` of every manifest fact flagged `common_word: true` — kinds whose values are ordinary words
    (a person's first name, a team, a label) and would flood the value scan. `manifests` is `kb.load_manifests()`'s
    result — this module stays stdlib-only and does no I/O of its own; the caller loads it (and an environment's
    own `_discovery/` overlay is picked up the same way `kb` already merges it)."""
    out: set[str] = set()
    for m in (manifests or {}).values():
        for f in m.get("facts") or []:
            if isinstance(f, dict) and f.get("common_word") and f.get("target", "row") == "row":
                out.add(str(f.get("key", "")).split(" ")[0])
    return out


def configured_values(cfg: dict, facts: dict, *, common: "set[str] | frozenset[str]" = frozenset(),
                       core_domains: "set[str] | frozenset[str]" = frozenset(),
                       login_tail: str = r"(?![\w-])", org_not_dep: str = "",
                       repo_dependencies: "set[str] | frozenset[str]" = frozenset()) -> tuple[list[tuple[re.Pattern, str]], list[str]]:
    """(patterns, loader errors) — every literal value THIS environment has configured, as scan-ready patterns:
    every env-store fact row's value (`facts`, `kb.all_facts()`'s shape) + config keys that carry identity (org,
    review bot, owner teams, Slack channels/domain, tracker site/project/board_sprint_prefix, `tracker.repos` —
    full slug and bare name, skipping `COMMON_REPO_NAMES` — `tz_default`, `leaks.markers` (#95: product/org
    markers no shape knows), `.context/` domains). `cfg` is a loaded `kit_profile.load()` (or `{}`), `facts` a
    loaded `kb.all_facts()` (or `{}`) — this function does no I/O of its own, so a caller decides how (and
    whether) to degrade when the store is unreadable. Short / numeric / generic values never make the cut
    (`keep_value`) — they would match everywhere and mean nothing.

    `common` is `common_word_kinds()`'s result (kit-health passes it; public-text-check may pass nothing —
    a bare four-character floor is still applied via `keep_value`'s no-kind path only when `kind` is dropped,
    so this function keeps a `kind` on every fact-row value regardless, trading a few short common words for
    never missing a real one). `core_domains` excludes the engine's own domain folder names
    (`kit_profile.CORE_DOMAINS`) — ordinary English words a real environment also uses as a domain. `login_tail`
    / `org_not_dep` let kit-health exclude its own dependency address (the kit's own repo mentioning itself is
    not a leak) from the login and `<org>/<repo>` patterns; `repo_dependencies` does the same for `tracker.repos`
    itself (a tracked repo that IS one of the kit's own dependencies, e.g. the kit's own checkout, is skipped
    entirely, not just its `<org>/<repo>` form). All three default to "exclude nothing", which is what
    public-text-check wants (it is not scanning the kit's own tree)."""
    vals: dict[str, str] = {}
    errors: list[str] = []

    def keep(v: object, what: str, kind: str = "") -> None:
        if v in (None, ""):
            return
        s = str(v)
        if keep_value(s, kind, common):
            vals.setdefault(s.strip(), what)

    try:
        for system, kinds in (facts or {}).items():
            for kind, rows in kinds.items():
                for row in rows.values():
                    keep(row["value"], f"env fact `{system}.{kind}`", f"{system}.{kind}")
    except (AttributeError, TypeError, KeyError) as e:
        errors.append(f"env-store tables malformed ({e}) — the value scan is shapes-only until that is fixed")

    logins: list[tuple[re.Pattern, str]] = []
    if cfg:
        gh = cfg.get("github") or {}
        keep(gh.get("review_bot"), "github.review_bot")
        for login in (gh.get("display_names") or {}):
            if keep_value(str(login)):
                logins.append((re.compile(rf"(?<![\w-]){re.escape(str(login).strip())}{login_tail}"),
                               "colleague login (github.display_names)"))
        for team in gh.get("owner_teams") or []:
            keep(team, "github.owner_teams")
        keep((cfg.get("slack") or {}).get("domain"), "slack.domain")
        for v in ((cfg.get("slack") or {}).get("channels") or {}).values():
            keep(v, "Slack channel id (slack.channels)")
        for k in ("site", "project", "board_sprint_prefix"):
            keep((cfg.get("tracker") or {}).get(k), f"tracker.{k}")
        keep(cfg.get("tz_default"), "tz_default")
        for m in (cfg.get("leaks") or {}).get("markers") or []:  # #95: product/org markers no shape knows
            keep(m, "leaks.markers")
        for repo in (cfg.get("tracker") or {}).get("repos") or []:
            name = str(repo).rsplit("/", 1)[-1]
            if name in repo_dependencies:
                continue
            keep(repo, "tracker.repos")
            if name.lower() not in COMMON_REPO_NAMES:
                keep(name, "tracker.repos (repo name)")

    pats: list[tuple[re.Pattern, str]] = []
    for d in sorted({str(d).strip() for d in ((cfg or {}).get("domains") or [])} - {""} - set(core_domains)):
        pats.append((re.compile(rf"(?:\.context/|DOMAIN=){re.escape(d)}\b"), "domains (a local .context/ domain)"))
    for v, what in vals.items():
        esc = re.escape(v)
        pats.append((re.compile((r"\b" if v[0].isalnum() else "") + esc + (r"\b" if v[-1].isalnum() else "")), what))
    org = str(((cfg or {}).get("github") or {}).get("org") or "")
    if org:
        pats.append((re.compile(rf"\b{re.escape(org)}/{org_not_dep}[a-z][\w.-]*"), f"`{org}/<repo>` path"))
        pats.append((re.compile(rf"@{re.escape(org)}/"), "org team handle"))
    return pats + logins, errors


def cross_org_shapes(owner: str) -> list[tuple[re.Pattern, str]]:
    """`<org>/<repo>#<n>` naming a DIFFERENT org than `owner` (the repo text is about to be posted to) — a bare
    inline issue/PR reference is plausibly a private repo leaking through a shorthand nobody meant to publish
    (the honest form for a genuine cross-repo reference is the full URL anyway). A same-org reference
    (`<owner>/<repo>#<n>`, an ordinary link within one's own org) is not flagged. `owner` empty → no shape (a
    caller that could not parse `--repo` has already failed earlier)."""
    if not owner:
        return []
    # the match start is anchored on `(?<![\w.-])`, not `\b`: `\b` also holds right after a hyphen or dot inside
    # the owner (e.g. `my-org/pub` — a boundary sits between `-` and `o`), so a same-org link could still match
    # from mid-owner (`org/pub#1`) once the negative lookahead below has already been stepped past.
    return [(re.compile(rf"(?<![\w.-])(?!{re.escape(owner)}/)[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*#\d+\b",
                        re.IGNORECASE),
             "other-org `<org>/<repo>#<n>` reference — use the full URL, or describe it generically")]
