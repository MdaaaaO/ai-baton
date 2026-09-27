"""leak_shapes.py — the generic SHAPES of environment facts the kit must never carry.

One list for every scanner: `kit-health` § leaks (every kit file, plus the values THIS machine's env store
holds) and `kit_verify --no-env` (a unit's own file, env-free — what a contributor's PR can run). No
organisation's own values live here: those are read at run time from the env store.

`SKIP_LINE` exempts the lines that legitimately hold such shapes — the frontmatter declarations
(`requires:`, `tools:`, `facts:`); `SKIP_FILES` the scanners' own files (this module, kit-health.py, the
allow-list) — a per-file rule, so a doc line that merely mentions `leak_shapes.py` is still scanned.
`allowed()` reads `skills/kit-health/allow.txt` — one regex per line, anchored at the start of `<path>:<match>`
(`$` for an exact match) — the ONE allow-list both scanners honour, so a provenance line kit-health accepts is
not a `kit-verify` failure. `shapes(tracker_kind, key_regex)` is the shape list a scanner uses: the generic list always (kit-verify's
env-free scan, and kit-health on any tracker), plus this environment's `tracker.key_regex` as a second
ticket shape on a Jira-style tracker.
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
SKIP_DIRS = frozenset({"fixtures", "__pycache__"})
SKIP_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".jsonl", ".pyc"})


def skip_path(rel: str) -> bool:
    """True when no leak scanner reads `rel` (a path relative to the kit root, `/`-separated)."""
    parts = rel.split("/")
    return (parts[-1] in SKIP_FILES or any(d in SKIP_DIRS for d in parts[:-1])
            or any(rel.lower().endswith(x) for x in SKIP_SUFFIXES))


def shapes(tracker_kind: str | None = None, key_regex: str | None = None) -> list[tuple[re.Pattern, str]]:
    """The compiled shape list for one scanner: the generic list on every tracker (a key from ANOTHER environment is
    exactly the leak a shared kit risks, so the generic ticket shape is never dropped), plus, on a tracker with a
    compiling `key_regex`, that regex as a second ticket shape so this environment's own keys are caught even when
    they do not fit the generic form. An invalid regex is kit-verify's finding; nothing is added for it."""
    out = [(re.compile(rx), what) for rx, what in LEAK_SHAPES]
    if key_regex and tracker_kind != "github":  # a GitHub key is `#12`: no id to leak, the generic shape already covers foreign keys
        try:
            out.append((re.compile(key_regex), "ticket key (tracker.key_regex)"))
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
