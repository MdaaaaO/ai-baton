#!/usr/bin/env python3
"""diagram-plan — which diagrams a PR body needs, derived from the diff (pr-open § Diagrams).

The plan is deterministic: changed paths → *facets* (dbt-model, dag, pipeline, event-schema, api,
service, ui, infra, ci, migration, skill, deps, config, docs, tests) → at most one artifact per
reviewer question — WHERE the change sits, WHAT changes (before → after), what RUNS when.
The model draws exactly what the plan asks for and stamps the plan marker into the body; a later
push is compared against the marker (`--check`) so the diagrams are redrawn when the shape moved.

Usage (from the workspace root; stdlib only):
  diagram-plan.py --pr <owner/repo> <n> [--check] [--json]      # files + labels + body via gh
  diagram-plan.py --repo <dir | owner/repo> [--base <ref>] [--type <t>] [--json]   # local git diff
  diagram-plan.py --files <list.txt> [--repo …] [--type …]        # one path per line (name-status ok)
  diagram-plan.py --explain                                       # the facet globs and the facet → question matrix

  --repo   a DIRECTORY (the clone to diff — `git diff` runs there, the env-config overlay key is read
           from its `origin`), or an `owner/repo` slug when the diff is the cwd's or comes from --files.
           Sessions run from the workspace root, so a local-diff run names the repo dir.

  --type   bugfix | refactor | feature — the PR *intent*, which paths cannot show. Read from the
           type label with --pr (bug/hotfix → bugfix; tech-debt/refactor → refactor), else feature.
  --check  with --pr: compare the body's `<!-- diagram-plan: … -->` marker to the fresh plan;
           prints `OK`, `DRIFT <old> → <new>`, `NO MARKER` or `MALFORMED MARKER <line>`; exit 3 on
           drift (or a marker that fails to parse) so a watcher can act.
  --trivial-lines / --secondary-share  override the two facet-weighting thresholds below (rarely
           needed; a repo whose diffs run unusually large or small).

Exit codes: 0 success (a plan was printed, or --check found the marker current); 1 a `gh`/`git`
call failed (message on stderr, `FAIL …`); 2 bad usage (no changed files found, or --check without
--pr); 3 --check found drift, no marker, or a marker present but malformed.

Facet globs: core defaults below (generic conventions) + the env config's overlay
`diagrams.repos.<owner/repo>.facets.<facet>: [globs]` (first match wins, overlay before defaults)
and `diagrams.repos.<owner/repo>.ignore: [globs]`. Environment-specific paths live in the env config,
never here (kit-health greps for leaks).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(KIT / "context-db" / "bin"))
try:
    import kit_profile  # type: ignore
except Exception:  # env store absent (bare checkout) — core defaults still work
    kit_profile = None

# ── facets ──────────────────────────────────────────────────────────────────────────────────
# Order matters: the first matching facet wins. Supporting facets (docs/tests/config/deps) never
# drive the plan on their own; substantive ones do.
SUPPORTING = {"docs", "tests", "config", "deps"}

DEFAULT_FACETS: list[tuple[str, list[str]]] = [
    ("tests", ["**/test_*.py", "**/*_test.go", "**/*_test.py", "**/*.test.*", "**/*.spec.*",
               "**/tests/**", "**/__tests__/**", "**/*.stories.*", "**/unit_tests/**", "**/fixtures/**"]),
    ("dbt-model", ["**/models/**/*.sql", "**/models/**/*.yml", "**/models/**/*.yaml",
                   "**/macros/**/*.sql", "**/snapshots/**/*.sql", "**/seeds/**/*.csv", "**/seeds/**/*.yml",
                   "**/analyses/**/*.sql", "dbt_project.yml", "**/dbt_project.yml"]),
    ("dag", ["**/dags/**/*.py", "**/dags/**/*.yaml", "**/dags/**/*.yml", "**/dag_*.py", "airflow_settings.yaml"]),
    ("migration", ["**/migrations/**", "**/*backfill*", "**/*cutover*", "**/*cutoff*", "**/alembic/**"]),
    ("event-schema", ["**/*.proto", "**/*.avsc", "**/event/**", "**/events/**", "**/schemas/**/*.json",
                      "**/schema/**/*.json", "**/*.avdl"]),
    ("api", ["**/routes/**", "**/api/**", "**/rpc/**", "**/handlers/**", "**/controllers/**",
             "**/endpoints/**", "**/graphql/**", "**/openapi*", "**/*_service.*"]),
    ("ui", ["**/*.tsx", "**/*.jsx", "**/*.vue", "**/*.svelte", "**/components/**", "**/*.css", "**/*.scss"]),
    ("ci", [".github/workflows/**", ".github/actions/**", ".gitlab-ci.yml", "Jenkinsfile", ".circleci/**",
            "**/.github/workflows/**"]),
    ("infra", ["**/*.tf", "**/*.tfvars", "**/charts/**", "**/helm/**", "**/argo/**", "**/k8s/**",
               "**/kubernetes/**", "**/terraform/**", "**/Dockerfile*", "**/docker-compose*.y*ml",
               "**/infra/**", "**/*.service", "**/*.timer"]),
    ("pipeline", ["**/pipeline*/**", "**/pipelines/**", "**/etl/**", "**/runner/**", "**/jobs/**",
                  "**/flink/**", "**/spark/**", "**/*.sql.j2"]),
    ("skill", ["**/skills/**", "**/.claude/**", "**/agents/**", "**/prompts/**", "**/*.prompt.md"]),
    ("deps", ["package.json", "**/package.json", "package-lock.json", "**/package-lock.json", "pnpm-lock.yaml",
              "**/pnpm-lock.yaml", "yarn.lock", "requirements*.txt", "**/requirements*.txt", "pyproject.toml",
              "**/pyproject.toml", "poetry.lock", "uv.lock", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
              "Gemfile*", "**/Gemfile*"]),
    ("docs", ["**/*.md", "**/*.rst", "**/*.txt", "**/*.adoc", "docs/**", "**/docs/**", "LICENSE*", "CODEOWNERS",
              "**/CODEOWNERS"]),
    ("config", ["**/*.yml", "**/*.yaml", "**/*.json", "**/*.toml", "**/*.ini", "**/*.cfg", "**/*.csv",
                "**/.env*", "**/*.env", "**/*.conf", "**/*.properties"]),
    ("service", ["**/*.py", "**/*.go", "**/*.ts", "**/*.js", "**/*.mjs", "**/*.java", "**/*.kt", "**/*.rs",
                 "**/*.rb", "**/*.sh", "**/*.sql", "**/*.scala"]),
]

# ── facet → the three reviewer questions ──────────────────────────────────────────────────────
# Each entry: WHERE (placement), WHAT (before → after), RUNS (flow). `None` = the question is not
# raised by this facet. Mermaid where the content is a graph or a flow; a markdown table where the
# content is a delta — a column list is a table, not an erDiagram.
MATRIX: dict[str, dict[str, str | None]] = {
    "dbt-model": {
        "where": "lineage `flowchart LR` — sources → the changed model(s) → known downstreams; changed nodes highlighted (`classDef new`), untouched neighbours plain, `(follow-up <key>)` where a consumer lands later",
        "what": "column delta **table** — `column | before | after | note`; call out grain, unique key, partition/cluster and incremental strategy changes on their own row",
        "runs": None,  # add only when incremental/merge logic changed — see note below
    },
    "dag": {
        "where": "task graph `flowchart TD` of the DAG with the touched tasks highlighted; sensors/triggers as their own nodes",
        "what": "before → after task graph when tasks were added/removed/rewired; a schedule/dependency **table** (`dag | before | after`) when only the cadence or a trigger changed",
        "runs": "one run `sequenceDiagram` (`autonumber`) only when a task's runtime contract changed (new connection, new pool, retries/SLA, cross-DAG trigger)",
    },
    "pipeline": {
        "where": "stage `flowchart LR` — source → each stage → sink, changed stages highlighted",
        "what": "data delta **table** — what each changed stage reads/writes before → after (tables, keys, partitions)",
        "runs": "one record end-to-end `sequenceDiagram` when the routing, dedup or error path changed",
    },
    "event-schema": {
        "where": "event path `flowchart LR` — producer → transport/pipeline → consumers, the touched event(s) highlighted",
        "what": "field delta **table** — `field | before | after | PII | note`; a new event lists every field; `breaking` changes on their own row",
        "runs": "`sequenceDiagram` of one event end-to-end only when routing, validation or the consumer set changed",
    },
    "api": {
        "where": "component `flowchart` — caller(s) → the touched route/RPC/handler → stores/downstream services; new vs existing labelled",
        "what": "request/response shape delta **table** when the contract changed (`field | before | after`)",
        "runs": "request `sequenceDiagram` (`autonumber`) — `alt` for the auth/validation gates, `Note` for the invariant the PR relies on",
    },
    "service": {
        "where": "component `flowchart` — the touched module(s) and their immediate neighbours, new vs existing labelled",
        "what": None,
        "runs": "one concrete call path `sequenceDiagram` through the changed code",
    },
    "ui": {
        "where": "component tree `flowchart TD` of the touched surface (route → view → components → data hooks), new components highlighted",
        "what": None,
        "runs": "user-flow `sequenceDiagram` (user → component → API) or `stateDiagram-v2` when the change is a lifecycle/state machine",
    },
    "infra": {
        "where": "resource graph `flowchart` before → after (two `subgraph`s) — modules, resources, IAM edges, secrets locations",
        "what": "resource/variable delta **table** (`resource | before | after`)",
        "runs": None,  # deploy flow only when the deployment path itself changed (that is the `ci` facet)
    },
    "ci": {
        "where": None,
        "what": "job/step delta **table** (`job | trigger | before | after`) when jobs, triggers or gates changed",
        "runs": "pipeline `flowchart LR` of triggers → jobs → gates only when jobs or gates were added/removed/rewired",
    },
    "migration": {
        "where": None,
        "what": "content timeline **table** — `window | source | before | after` (cutoff/seed flips, backfill ranges), plus the rollback row",
        "runs": "`stateDiagram-v2` of the cutover (pre → dual-write/backfill → cut → verified) with the verification step named",
    },
    "skill": {
        "where": "invocation `flowchart` — who triggers it, which scripts/agents it runs, what it writes; only if the skill has a runtime (scripts/hooks)",
        "what": None,
        "runs": None,
    },
}

# PR intent overlays (paths cannot show intent): which questions survive.
INTENT = {
    "feature": {"where", "what", "runs"},
    "bugfix": {"runs"},        # one sequence of the failing path with the fixed step highlighted
    "refactor": {"where", "what"},  # before → after placement; no flow
}
INTENT_NOTE = {
    "bugfix": "bugfix: one `sequenceDiagram` of the failing path, the fixed step highlighted (`rect`), no architecture block",
    "refactor": "refactor: WHERE as before → after (two `subgraph`s), no flow block",
}
TYPE_LABELS = {
    "bug": "bugfix", "bugfix": "bugfix", "hotfix": "bugfix", "fix": "bugfix",
    "refactor": "refactor", "tech-debt": "refactor", "techdebt": "refactor", "cleanup": "refactor",
}
TRIVIAL_LINES = 12  # a substantive facet touched below this many lines does not raise a diagram on its own
SECONDARY_SHARE = 0.25  # a secondary facet fills a question only at ≥ this share of the dominant facet's weight
# Both are overridable per run via --trivial-lines / --secondary-share (main()); these are only the defaults.


# ── inputs ──────────────────────────────────────────────────────────────────────────────────
def sh(cmd: list[str], env: dict | None = None) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise SystemExit(f"FAIL {' '.join(cmd[:3])}…: {p.stderr.strip()[:300]}")
    return p.stdout


def gh_env() -> dict:
    """os.environ plus `github.sandbox_token_prefix` (kit_profile is its one reader); unchanged without an env store."""
    if kit_profile is None:
        return dict(os.environ)
    try:
        return kit_profile.gh_env()
    except (SystemExit, Exception):
        return dict(os.environ)


def files_from_pr(repo: str, n: int) -> tuple[list[tuple[str, int]], list[str], str]:
    env = gh_env()
    out = sh(["gh", "api", "--paginate", f"repos/{repo}/pulls/{n}/files?per_page=100",
              "--jq", ".[] | [.filename, (.additions + .deletions)] | @tsv"], env)
    files = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, changes = line.rsplit("\t", 1)
        files.append((name, int(changes)))
    meta = json.loads(sh(["gh", "api", f"repos/{repo}/pulls/{n}", "--jq", "{labels: [.labels[].name], body: (.body // \"\")}"], env))
    return files, meta["labels"], meta["body"]


def repo_slug(d: Path) -> str:
    """`owner/repo` from the clone's origin remote, "" when there is none."""
    p = subprocess.run(["git", "-C", str(d), "remote", "get-url", "origin"], capture_output=True, text=True)
    m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", p.stdout.strip()) if p.returncode == 0 else None
    return m.group(1) if m else ""


def resolve_repo(arg: str | None) -> tuple[Path | None, str | None]:
    """--repo → (the clone dir to diff in, the owner/repo overlay key). A directory gives both (the slug from
    its origin); a slug gives no dir (the cwd is diffed); None gives neither."""
    if not arg:
        return None, None
    d = Path(arg).expanduser()
    if d.is_dir():
        return d.resolve(), repo_slug(d) or None
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", arg):
        raise SystemExit(f"FAIL --repo {arg!r} is neither a directory nor an owner/repo slug")
    return None, arg


def files_from_git(base: str, cwd: Path | None = None) -> list[tuple[str, int]]:
    if cwd is not None and not (cwd / ".git").exists() and subprocess.run(["git", "-C", str(cwd), "rev-parse", "--git-dir"], capture_output=True).returncode != 0:
        raise SystemExit(f"FAIL {cwd} is not a git repo")
    out = sh(["git", *(["-C", str(cwd)] if cwd else []), "diff", "--numstat", f"{base}...HEAD"])
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, name = parts
        if " => " in name:  # rename: "old => new" or "dir/{old => new}/file"
            name = re.sub(r"\{[^}]* => ([^}]*)\}", r"\1", name)
            name = name.split(" => ")[-1]
        n = (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
        files.append((name, n))
    return files


def files_from_list(path: str) -> list[tuple[str, int]]:
    files = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t") if "\t" in line else line.split()
        name = parts[-1]
        n = 0
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():  # numstat
            n = int(parts[0]) + int(parts[1])
        files.append((name, n))
    return files


# ── classification ──────────────────────────────────────────────────────────────────────────
def env_get(key: str, default):
    """Env-config lookup that degrades to the default when no env store is wired (bare kit checkout)."""
    if kit_profile is None:
        return default
    try:
        return kit_profile.get(key, default)
    except (SystemExit, Exception):
        return default


def env_overlay(repo: str | None) -> tuple[list[tuple[str, list[str]]], list[str]]:
    if not repo:
        return [], []
    repos = env_get("diagrams.repos", {})  # one lookup by the whole slug: a dotted path would split `owner/site.io` at the `.`
    cfg = repos.get(repo) if isinstance(repos, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}  # a bare value under the slug (a hand edit) is no overlay either, not an AttributeError
    facets = [(k, list(v)) for k, v in (cfg.get("facets") or {}).items()]
    return facets, list(cfg.get("ignore") or [])


def variants(g: str) -> set[str]:
    """`**` in fnmatch is just `*`, so `**/dags/**/*.py` needs `dags/*.py` and `dags/**/*.py` too."""
    out = {g}
    if g.startswith("**/"):
        out.add(g[3:])
    for v in list(out):
        out.add(v.replace("/**/", "/"))
        out.add(v.replace("/**", "/*"))
    return out


def match(path: str, globs: list[str]) -> bool:
    p = path.removeprefix("./")  # a prefix, not a character set: `.github/x` must keep its dot
    base = p.rsplit("/", 1)[-1]
    for g in globs:
        if "/" not in g and fnmatch.fnmatch(base, g):
            return True
        if any(fnmatch.fnmatch(p, v) for v in variants(g)):
            return True
    return False


def classify(files: list[tuple[str, int]], repo: str | None) -> tuple[dict[str, list[str]], Counter, list[str]]:
    overlay, ignore = env_overlay(repo)
    rules = overlay + DEFAULT_FACETS
    by_facet: dict[str, list[str]] = defaultdict(list)
    lines: Counter = Counter()
    ignored: list[str] = []
    for path, n in files:
        if match(path, ignore):
            ignored.append(path)
            continue
        facet = next((f for f, globs in rules if match(path, globs)), "other")
        by_facet[facet].append(path)
        lines[facet] += n
    return by_facet, lines, ignored


def plan(by_facet: dict[str, list[str]], lines: Counter, intent: str,
         trivial_lines: int = TRIVIAL_LINES, secondary_share: float = SECONDARY_SHARE) -> dict:
    substantive = [f for f in by_facet if f not in SUPPORTING and f != "other"]
    # weight = changed lines when known, else file count
    weight = {f: (lines[f] if sum(lines.values()) else len(by_facet[f])) for f in substantive}
    if any(lines.values()):
        big = [f for f in substantive if lines[f] >= trivial_lines]
        if big:
            substantive = big  # a facet touched in passing does not raise a diagram
    substantive.sort(key=lambda f: -weight[f])
    result: dict = {"facets": {f: len(v) for f, v in sorted(by_facet.items())}, "lines": dict(lines),
                    "intent": intent, "dominant": None, "questions": {}, "skip": None, "notes": []}
    if not substantive:
        result["skip"] = "supporting-only (docs / tests / config / deps / trivial) — one line under `## Diagrams`, no block"
        return result
    dominant = substantive[0]
    result["dominant"] = dominant
    allowed = INTENT.get(intent, INTENT["feature"])
    if intent in INTENT_NOTE:
        result["notes"].append(INTENT_NOTE[intent])
    for q in ("where", "what", "runs"):
        if q not in allowed:
            continue
        pick = None
        for f in substantive:  # dominant first; a secondary facet fills a question the dominant does not raise
            if f != dominant and weight[f] < secondary_share * weight[dominant]:
                continue  # touched in passing next to the dominant facet — it does not earn a block
            spec = MATRIX.get(f, {}).get(q)
            if spec:
                pick = {"facet": f, "spec": spec, "conditional": "only when" in spec}
                break
        if pick:
            result["questions"][q] = pick
    if dominant == "dbt-model" and "runs" in allowed:
        result["notes"].append("dbt-model raises RUNS only when incremental/merge logic changed — add a merge-strategy `sequenceDiagram` then, otherwise leave it out")
    if dominant == "infra" and "runs" in allowed:
        result["notes"].append("infra raises RUNS only when the deploy path itself changed (that is the `ci` facet)")
    if dominant == "skill":
        runtime = {"py", "sh", "mjs", "js", "ts", "bash", "zsh"}
        if not any(f.rsplit(".", 1)[-1] in runtime for f in by_facet["skill"]) and not by_facet.get("service"):
            result["notes"].append("skill without scripts/hooks has no runtime — the skip line is the honest answer")
    if len(result["questions"]) > 3:
        for q in list(result["questions"])[3:]:
            del result["questions"][q]
    if len(substantive) > 2:
        result["notes"].append(f"{len(substantive)} substantive facets ({', '.join(substantive)}) — one PR = one concern; consider splitting, else draw the dominant facet only and name the rest in prose")
    return result


def marker(p: dict) -> str:
    qs = ",".join(f"{q}={v['facet']}" for q, v in p["questions"].items()) or "-"
    facets = ",".join(sorted(f for f in p["facets"]))
    return f"<!-- diagram-plan: facets={facets} dominant={p['dominant'] or '-'} intent={p['intent']} {qs} -->"


def marker_from_body(body: str) -> str | None:
    # the real marker is a whole line starting with `facets=`; prose that quotes the marker shape is not it
    m = re.search(r"^\s*<!-- diagram-plan: (facets=[^>\n]*?) -->\s*$", body, re.M)
    return m.group(1).strip() if m else None


def malformed_marker_line(body: str) -> str | None:
    """A `<!-- diagram-plan: … -->`-shaped line present in the body that `marker_from_body` could not parse
    (a hand edit that dropped the closing `-->`, added trailing text, lost the `facets=` prefix, …) —
    distinguishes that case from a body that carries no marker at all, so `--check` names the broken line
    instead of reporting NO MARKER on a marker that is actually there."""
    if marker_from_body(body) is not None:
        return None
    for line in body.splitlines():
        if line.lstrip().startswith("<!-- diagram-plan:"):  # prose quoting the shape is not a marker
            return line.strip()
    return None


def render(p: dict, ignored: list[str], mk: str) -> str:
    out = []
    fac = "  ".join(f"{f}({n}{', ' + str(p['lines'][f]) + ' lines' if p['lines'].get(f) else ''})"
                    for f, n in p["facets"].items())
    out.append(f"facets:   {fac}")
    if ignored:
        out.append(f"ignored:  {len(ignored)} path(s) via env-config ignore globs")
    out.append(f"intent:   {p['intent']}")
    out.append(f"dominant: {p['dominant'] or '-'}")
    if p["skip"]:
        out.append(f"plan:     SKIP — {p['skip']}")
    else:
        out.append("plan:     (draw exactly these; ≤3 blocks)")
        for q in ("where", "what", "runs"):
            v = p["questions"].get(q)
            if not v:
                out.append(f"  {q.upper():<6} — not raised")
                continue
            tag = f"{q.upper()}?" if v.get("conditional") else q.upper()
            out.append(f"  {tag:<6} {v['facet']}: {v['spec']}")
        if any(v.get("conditional") for v in p["questions"].values()):
            out.append("          (`?` = conditional — draw it only when its 'only when' clause holds, else say so in the skip line)")
    for n in p["notes"]:
        out.append(f"note:     {n}")
    out.append(f"marker:   {mk}")
    return "\n".join(out)


def explain() -> str:
    """The facet globs and the facet → question matrix as prose — the one place it is written (pr-open's SKILL.md
    points here instead of copying it)."""
    out = ["facets — first matching glob wins, top to bottom; the env config's `diagrams.repos.<owner/repo>.facets` overlay",
           "comes before these defaults and `.ignore` globs drop a path entirely:"]
    for facet, globs in DEFAULT_FACETS:
        kind = "supporting" if facet in SUPPORTING else "substantive"
        out.append(f"  {facet:<13} {kind:<12} {', '.join(globs)}")
    out.append("")
    out.append("facet → the three reviewer questions (WHERE the change sits · WHAT changes · what RUNS); `—` = not raised;")
    out.append("`only when …` = conditional, drawn only when its clause holds:")
    for facet, qs in MATRIX.items():
        out.append(f"  {facet}")
        for q in ("where", "what", "runs"):
            out.append(f"    {q.upper():<6} {qs.get(q) or '—'}")
    out.append(f"  supporting ({', '.join(sorted(SUPPORTING))}): never raise a block; alone → SKIP")
    out.append("")
    out.append("intent overlays: " + "; ".join(f"{k} → {', '.join(sorted(v))}" for k, v in INTENT.items()))
    out.append(f"composition (defaults, override with --secondary-share / --trivial-lines): dominant facet = most "
               f"changed lines; a secondary facet fills a question only at ≥ {int(SECONDARY_SHARE * 100)} % "
               f"of the dominant's weight; a facet under {TRIVIAL_LINES} changed lines never earns a block; 3+ substantive facets → "
               "'one PR = one concern' note")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pr", nargs=2, metavar=("OWNER/REPO", "N"))
    ap.add_argument("--repo", help="the clone to diff (a directory; its origin names the env-config overlay), or an owner/repo slug")
    ap.add_argument("--explain", action="store_true", help="print the facet globs and the facet → question matrix, then exit")
    ap.add_argument("--base", default="origin/main", help="diff base for the local git mode")
    ap.add_argument("--files", help="path list file (plain paths, name-status or numstat lines)")
    ap.add_argument("--type", choices=sorted(INTENT), help="PR intent; overrides the label-derived one")
    ap.add_argument("--check", action="store_true", help="with --pr: compare the body marker to the fresh plan")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--trivial-lines", type=int, default=TRIVIAL_LINES, metavar="N",
                     help=f"a substantive facet under N changed lines never earns a block (default {TRIVIAL_LINES})")
    ap.add_argument("--secondary-share", type=float, default=SECONDARY_SHARE, metavar="F",
                     help=f"a secondary facet fills a question only at >= F of the dominant facet's weight (default {SECONDARY_SHARE})")
    a = ap.parse_args(argv[1:])
    if a.explain:
        print(explain())
        return 0

    labels: list[str] = []
    body = ""
    repo_dir, repo = resolve_repo(a.repo)
    if a.pr:
        repo, n = a.pr[0], int(a.pr[1])
        files, labels, body = files_from_pr(repo, n)
    elif a.files:
        files = files_from_list(a.files)
    else:
        files = files_from_git(a.base, repo_dir)
    if not files:
        print("FAIL no changed files found", file=sys.stderr)
        return 2

    intent = a.type or next((TYPE_LABELS[l.lower()] for l in labels if l.lower() in TYPE_LABELS), "feature")
    by_facet, lines, ignored = classify(files, repo)
    p = plan(by_facet, lines, intent, trivial_lines=a.trivial_lines, secondary_share=a.secondary_share)
    if repo_dir is not None and repo is None:  # the fallback is visible, never silent
        p["notes"].append(f"no `origin` remote in the --repo directory ({a.repo}; or an unparsable URL) — the env-config "
                          "overlay `diagrams.repos.<owner/repo>` was not applied; name the remote `origin` to use it")
    mk = marker(p)

    if a.check:
        if not a.pr:
            print("FAIL --check needs --pr", file=sys.stderr)
            return 2
        old = marker_from_body(body)
        new = mk[len("<!-- diagram-plan: "):-len(" -->")]
        if old is None:
            bad = malformed_marker_line(body)
            if bad is not None:
                print(f"MALFORMED MARKER — {bad}; fresh plan: {new}")
                return 3
            print(f"NO MARKER — body carries no diagram-plan marker; fresh plan: {new}")
            return 3
        if old == new:
            print(f"OK {new}")
            return 0
        print(f"DRIFT {old} → {new}")
        return 3

    if a.json:
        p["marker"] = mk
        p["ignored"] = ignored
        print(json.dumps(p, indent=1))
    else:
        print(render(p, ignored, mk))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
