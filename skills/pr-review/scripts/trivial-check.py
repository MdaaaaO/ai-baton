#!/usr/bin/env python3
"""trivial-check.py <owner/repo> <pr> [--head SHA]
Deterministic eligibility gate for the trivial-PR auto-approve path (owner decision, 2026-09-19).
Reads .context/state/pr-review/config.json `auto_approve` (PR_REVIEW_HOME overrides the dir). Prints one JSON object:
  {"eligible": bool, "class": "docs"|"patch-bump"|null, "reasons": [...], "packages": [...],
   "head": sha, "lines": n, "files": n, "ci": "...", "bot": "...", "threads_open": n}
`reasons` lists every failed gate (empty when eligible). Exit 0 always unless the API fails — then
{"eligible": false, "error": true, "reasons": [...]} and exit 1 (callers must treat error=true as NOT eligible).
`--head SHA` refuses (reason "head moved") when the live head differs — submit-review.sh --auto passes the reviewed head.
`--head SHA` / `--head=SHA` with an empty or non-40-hex value (an unset shell variable in the caller), a
repeated --head, or any other `--` flag exits 2: nothing disarms the check or is dropped silently.
A missing or unparsable config.json, or one without a `login`, is a setup problem: the same error shape
({"eligible": false, "error": true, "reasons": [...]}, the message also on stderr) and exit 3 — never a traceback.
Never posts anything. A PR is eligible only if EVERY gate passes:
  - the user is a requested reviewer (login in requested_reviewers, or a requested team in auto_approve.owner_teams) — repo-sweep PRs never qualify
  - open, not draft, base == default branch (no stacked PRs), author != login, no human CHANGES_REQUESTED, 0 unresolved review threads
  - no file matches an exclude glob; files <= max_files; non-lock lines <= max_lines
  - class docs: every file is a docs path (md/rst/txt outside manifests, docs/**); agent instructions
    (SKILL.md, skills/*/reference/**,
    agents/**, commands/**, WORKSPACE.md, CLAUDE.md, AGENTS.md) are behaviour, never docs, whatever the config says
  - class patch-bump: every file is a manifest/lockfile; every changed line in a non-lock manifest is
    the same line with only a version literal changed, and that line has the dependency-pin shape of
    its manifest's ecosystem (DEP_SHAPES — a changed date, IP or section number is not a bump); every
    bump is patch (x.y.Z), or minor when the package is in dev_tooling; major never; lockfiles must
    accompany at least one manifest; a patch that does not parse as unified-diff hunks is refused
  - CI: required contexts are the union of the rulesets endpoint and classic branch protection (a
    ruleset-governed repo must not pass vacuously); every required context must be present and green.
    With no required context — none configured, or neither source readable (`"protection": "unreadable"`
    in the output) — every check-run on head must have concluded success/skipped/neutral (>=1 run,
    paginated); the combined status must not be failure either way
  - docs class excludes `docs_exclude_globs` (dbt model docs, packages/constraints txt are not "docs")
  - repos in require_bot_review: a review by the environment's review bot (`github.review_bot`, override
    `PR_WATCH_BOT_LOGIN`) on head with a green Assessment
"""
import json, os, re, subprocess, sys, fnmatch, tempfile

_KIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
sys.path.insert(0, os.path.join(_KIT, "context-db", "bin"))
import kit_profile  # stdlib-only, ships with the kit

# the workspace's .context/ (#74), never this script's location (the plugin cache on a plugin install)
ROOT = os.environ.get("PR_REVIEW_HOME") or str(kit_profile.context_root() / "state" / "pr-review")
try:
    os.environ.update(kit_profile.gh_env())  # github.sandbox_token_prefix, if any
    REVIEW_BOT = os.environ.get("PR_WATCH_BOT_LOGIN") or kit_profile.get("github.review_bot") or ""
except Exception:  # env store missing — no bot gate
    REVIEW_BOT = os.environ.get("PR_WATCH_BOT_LOGIN", "")

def load_config():
    """Read config.json under ROOT, or print the error shape a failed gh call prints
    ({"eligible": false, "error": true, "reasons": [...]}) and exit 3 — never a traceback. Exit 3, not
    1: the setup is wrong, a retry will not help."""
    path = os.path.join(ROOT, "config.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
        if isinstance(cfg, dict) and cfg.get("login"):
            return cfg
        msg = f"config.json at {path} has no login"
    except FileNotFoundError:
        msg = f"no config.json at {path}"
    except (OSError, ValueError) as e:  # ValueError: bad JSON, or bytes that are not UTF-8
        msg = f"config.json at {path} is unreadable: {e}"
    print(f"error: {msg}", file=sys.stderr)
    print(json.dumps({"eligible": False, "error": True, "reasons": [msg]}))
    sys.exit(3)

CFG = load_config()
AA = CFG.get("auto_approve", {})
ME = CFG["login"]

def gh(*args, paginate=False, allow_fail=False):
    cmd = ["gh", "api"] + (["--paginate"] if paginate else []) + list(args)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        if allow_fail: return None
        print(json.dumps({"eligible": False, "error": True, "reasons": [f"gh api timed out after 60s: {' '.join(args)}"]}))
        sys.exit(2)
    if p.returncode != 0:
        if allow_fail: return None
        print(json.dumps({"eligible": False, "error": True, "reasons": [f"gh api failed: {' '.join(args)}: {p.stderr.strip()[:200]}"]}))
        sys.exit(1)
    if paginate:  # concatenated JSON arrays
        out = []
        dec = json.JSONDecoder(); s = p.stdout.strip(); i = 0
        try:
            while i < len(s):
                obj, j = dec.raw_decode(s, i); out.extend(obj if isinstance(obj, list) else [obj]); i = j
                while i < len(s) and s[i].isspace(): i += 1
        except json.JSONDecodeError:
            if allow_fail: return None
            print(json.dumps({"eligible": False, "error": True, "reasons": [f"gh api returned invalid JSON: {' '.join(args)}"]})); sys.exit(1)
        return out
    try:
        return json.loads(p.stdout) if p.stdout.strip() else None
    except json.JSONDecodeError:
        if allow_fail: return None
        print(json.dumps({"eligible": False, "error": True, "reasons": [f"gh api returned invalid JSON: {' '.join(args)}"]})); sys.exit(1)

def glob_any(path, globs):
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.replace("**/", "")) for g in globs)

SEMVER = re.compile(r"(?<![\w.])v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+.][0-9A-Za-z.-]+)?(?![\w.])")

def bump_kind(a, b):
    ma, mb = SEMVER.fullmatch(a), SEMVER.fullmatch(b)
    if not (ma and mb): return "unknown"
    A = [int(ma.group(1)), int(ma.group(2)), int(ma.group(3) or 0)]
    B = [int(mb.group(1)), int(mb.group(2)), int(mb.group(3) or 0)]
    if B < A: return "downgrade"
    if A[0] != B[0]: return "major"
    if A[1] != B[1]: return "minor"
    if A[2] != B[2]: return "patch"
    return "none"

HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")

def pair_lines(patch):
    """Return list of (minus, plus) pairs per hunk; ValueError when counts differ or the patch is not hunks.
    Parsed by the hunk header's line counts, so a content line that itself starts with `--`/`++`
    (a Markdown rule, a `--flag`) is a change like any other, never mistaken for a file header."""
    pairs = []; minus = []; plus = []
    def flush():
        nonlocal minus, plus
        if len(minus) != len(plus): raise ValueError("unbalanced hunk")
        pairs.extend(zip(minus, plus)); minus, plus = [], []
    old_left = new_left = 0; in_hunk = False
    for ln in patch.splitlines():
        if old_left == 0 and new_left == 0:
            if ln.startswith("@@"):
                m = HUNK.match(ln)
                if not m: raise ValueError(f"unparseable hunk header: {ln[:40]}")
                flush(); in_hunk = True
                old_left = int(m.group(1)) if m.group(1) is not None else 1
                new_left = int(m.group(2)) if m.group(2) is not None else 1
                continue
            if ln.startswith("\\"): continue  # "\ No newline at end of file"
            if in_hunk or not ln.startswith(("diff ", "index ", "--- ", "+++ ")):
                raise ValueError(f"line outside a hunk: {ln[:40]}")
            continue  # git file headers before the first hunk
        if ln.startswith("\\"): continue
        tag, body = ln[:1], ln[1:]
        if tag == "-" and old_left: minus.append(body); old_left -= 1
        elif tag == "+" and new_left: plus.append(body); new_left -= 1
        elif tag in (" ", "") and old_left and new_left: flush(); old_left -= 1; new_left -= 1
        else: raise ValueError(f"hunk line does not fit its header: {ln[:40]}")
    if old_left or new_left: raise ValueError("truncated hunk")
    flush(); return pairs

def version_only_change(a, b):
    """If a and b differ only in version literals, return (from, to) else None."""
    va, vb = SEMVER.findall(a), SEMVER.findall(b)
    if not va or len(va) != len(vb): return None
    if SEMVER.sub("§", a) != SEMVER.sub("§", b): return None
    ma = [m.group(0) for m in SEMVER.finditer(a)]; mb = [m.group(0) for m in SEMVER.finditer(b)]
    diffs = [(x, y) for x, y in zip(ma, mb) if x != y]
    return diffs[0] if len(diffs) == 1 else None

# The dependency-pin line shape per manifest ecosystem, matched on the line with its version literal
# replaced by "§" (SEMVER.sub). A manifest line that changes one version-looking literal but has none of
# these shapes (a date, an IP, a section number, the project's own version) is not a dependency bump.
_N = r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
_PEP508 = _N + r"(?:\[[A-Za-z0-9,._ -]*\])?\s*(?:===|==|~=|>=)\s*§"
DEP_SHAPES = [
    ("requirements*.txt", re.compile(r"^\s*" + _PEP508 + r"\s*(?:;[^#§]*)?(?:\s+#[^§]*)?$")),
    ("pyproject.toml", re.compile(r"^\s*([\"'])" + _PEP508 + r"\s*(?:;[^\"'§]*)?\1\s*,?\s*(?:#[^§]*)?$")),
    ("pyproject.toml", re.compile(r"^\s*" + _N + r"\s*=\s*([\"'])[\^~=]{0,2}§\2\s*(?:#[^§]*)?$"), True),
    ("package.json", re.compile(r'^\s*"(?P<name>(?:@[a-z0-9._-]+/)?[a-z0-9._-]+)"\s*:\s*"[\^~]?§"\s*,?\s*$'), True),
    ("go.mod", re.compile(r"^\s*(?:require\s+)?(?P<name>[a-z0-9.-]+\.[a-z]+/[A-Za-z0-9._/~-]+)\s+§(?:\s*//\s*indirect)?\s*$")),
    ("Dockerfile", re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(?P<name>[a-z0-9][a-z0-9./_-]*):§(?:\s+AS\s+[A-Za-z0-9_.-]+)?\s*$", re.I)),
    ("Dockerfile", re.compile(r"^\s*(?:ARG|ENV)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*_VERSION)=([\"']?)§\2\s*$", re.I), True),
    (".pre-commit-config.yaml", re.compile(r"^\s*rev:\s*([\"']?)§\1\s*(?:#[^§]*)?$")),
    ("packages.yml", re.compile(r"^\s*version:\s*([\"']?)§\1\s*(?:#[^§]*)?$")),
    (".tool-versions", re.compile(r"^\s*" + _N + r"\s+§\s*$")),
    ("Cargo.toml", re.compile(r"^\s*" + _N + r"\s*=\s*\"[\^~=]?§\"\s*$"), True),
    ("Cargo.toml", re.compile(r"^\s*" + _N + r"\s*=\s*\{\s*version\s*=\s*\"[\^~=]?§\"[^§]*\}\s*$")),
    (".python-version", re.compile(r"^\s*§\s*$")),
]
# keys that a `key = "version"` / `ARG X_VERSION=` shape (third field True) also matches but that name the
# project itself, its release or its runtime, not a dependency
NOT_DEPS = {"version", "requires-python", "python", "python_version", "target-version", "rust-version",
            "edition", "minversion", "node", "npm", "current_version", "__version__", "project_version",
            "app_version", "release_version", "image_version", "build_version", "service_version",
            "package_version", "version_info"}

def dep_name(path, line):
    """The dependency a manifest line pins ("?" when the shape carries no name), or None when the line
    is not a dependency pin in that manifest's ecosystem."""
    base = path.rsplit("/", 1)[-1]
    if len(SEMVER.findall(line)) != 1: return None
    tmpl = SEMVER.sub("§", line)
    for pat, rx, *keyed in DEP_SHAPES:
        if not (fnmatch.fnmatch(base, pat) or (pat == "Dockerfile" and base.startswith("Dockerfile."))): continue
        m = rx.match(tmpl)
        if not m: continue
        name = m.groupdict().get("name") or ("python" if pat == ".python-version" else "?")
        if keyed and name.lower() in NOT_DEPS: return None
        return name
    return None

# Agent instructions look like Markdown but change behaviour: never "docs", whatever docs_globs says.
AGENT_DOCS = ["SKILL.md", "**/SKILL.md", "agents/**", "**/agents/**", "WORKSPACE.md", "**/WORKSPACE.md",
              "CLAUDE.md", "**/CLAUDE.md", "AGENTS.md", "**/AGENTS.md", "skills/*/reference/**",
              "**/skills/*/reference/**", "commands/**", "**/commands/**", ".claude/commands/**", "**/.claude/commands/**"]

def is_docs_path(path, aa):
    return (glob_any(path, aa.get("docs_globs", [])) and not glob_any(path, aa.get("docs_exclude_globs", []) + AGENT_DOCS)
            and not glob_any(path, aa.get("manifest_globs", [])) and not glob_any(path, aa.get("lock_globs", [])))

def required_checks(repo, base_ref):
    """The required status-check contexts for base_ref, from both places a repo can name them: the
    rulesets endpoint and classic branch protection. GitHub enforces the two together, so the result is
    their union — a check only one of them requires is still required. Returns (required, source,
    readable): `source` is "rulesets", "protection" or "rulesets+protection" (None when no check is
    required); `readable` is False only when NEITHER source could be read (the rulesets endpoint
    answers 200 with an empty list for a repo without rules, so an empty list is a successful read)."""
    rules = gh(f"repos/{repo}/rules/branches/{base_ref}", paginate=True, allow_fail=True)  # every page: a rule past the first is still required
    from_rules = set()
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks": continue
            for c in (rule.get("parameters") or {}).get("required_status_checks") or []:
                name = (c or {}).get("context")
                if name: from_rules.add(name)
    prot = gh(f"repos/{repo}/branches/{base_ref}/protection/required_status_checks", allow_fail=True)
    from_prot = set()
    if isinstance(prot, dict):
        from_prot = set(prot.get("contexts") or []) | {c["context"] for c in prot.get("checks") or []}
    source = "+".join(n for n, found in (("rulesets", from_rules), ("protection", from_prot)) if found) or None
    return sorted(from_rules | from_prot), source, isinstance(rules, list) or isinstance(prot, dict)

def parse_args(argv):
    """Return (positional, head). `--head SHA` and `--head=SHA` both need the full 40-hex sha; an empty,
    short or repeated --head, or any other `--` flag, exits 2 — nothing is dropped silently."""
    pos = []; heads = []; i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--head":
            heads.append(argv[i+1] if i+1 < len(argv) else ""); i += 2; continue
        if a.startswith("--head="):
            heads.append(a[len("--head="):]); i += 1; continue
        if a.startswith("--"):
            print(f"error: unknown flag {a!r}", file=sys.stderr); sys.exit(2)
        pos.append(a); i += 1
    if len(heads) > 1:
        print("error: --head given twice", file=sys.stderr); sys.exit(2)
    if heads and not re.fullmatch(r"[0-9a-f]{40}", heads[0]):
        print(f"error: --head needs the full 40-char sha, got {heads[0]!r}", file=sys.stderr); sys.exit(2)
    return pos, (heads[0] if heads else None)

def main():
    args, want_head = parse_args(sys.argv[1:])
    if len(args) != 2: print(__doc__); sys.exit(2)
    repo, pr = args[0], args[1]; o, r = repo.split("/")
    res = {"repo": repo, "pr": int(pr), "eligible": False, "class": None, "reasons": [], "packages": []}
    if AA.get("mode", "off") == "off":
        res["reasons"].append("auto_approve.mode=off"); print(json.dumps(res)); return
    p = gh(f"repos/{repo}/pulls/{pr}")
    head = p["head"]["sha"]; res["head"] = head
    res["author"] = p["user"]["login"]; res["title"] = p["title"]; res["url"] = p["html_url"]
    if want_head and want_head != head: res["reasons"].append(f"head moved {want_head[:9]} -> {head[:9]}")
    if p["state"] != "open": res["reasons"].append(f"state {p['state']}")
    if p["draft"]: res["reasons"].append("draft")
    default_branch = (p.get("base", {}).get("repo") or {}).get("default_branch"); base_ref = p["base"]["ref"]
    res["base"] = base_ref
    if default_branch and base_ref != default_branch: res["reasons"].append(f"base {base_ref} is not the default branch {default_branch} (stacked PR)")
    if p["user"]["login"] == ME: res["reasons"].append("own PR")
    req_users = [u["login"] for u in p.get("requested_reviewers", [])]
    req_teams = [t["slug"] for t in p.get("requested_teams", [])]
    res["requested"] = {"users": req_users, "teams": req_teams}
    if ME not in req_users and not (set(req_teams) & set(AA.get("owner_teams", []))):
        res["reasons"].append("not in the user's review scope (requested: " + ", ".join(req_users + ["@"+t for t in req_teams]) + ")")
    res["files"] = p["changed_files"]
    if p["changed_files"] > AA.get("max_files", 10): res["reasons"].append(f"files {p['changed_files']} > max_files")
    files = gh(f"repos/{repo}/pulls/{pr}/files?per_page=100", paginate=True)
    excl = AA.get("exclude_globs", []); man_g = AA.get("manifest_globs", []); lock_g = AA.get("lock_globs", [])
    paths = [f["filename"] for f in files]
    hard = AA.get("hard_exclude_globs", [])
    bad = [x for x in paths if glob_any(x, hard) or (glob_any(x, excl) and not glob_any(x, man_g) and not glob_any(x, lock_g))]
    if bad: res["reasons"].append("excluded path: " + ", ".join(bad[:3]))
    is_docs = [is_docs_path(x, AA) for x in paths]
    is_man = [glob_any(x, man_g) for x in paths]; is_lock = [glob_any(x, lock_g) for x in paths]
    nonlock_lines = sum(f["additions"] + f["deletions"] for f, l in zip(files, is_lock) if not l)
    res["lines"] = nonlock_lines
    if nonlock_lines > AA.get("max_lines", 200): res["reasons"].append(f"non-lock lines {nonlock_lines} > max_lines")
    if paths and all(is_docs) and "docs" in AA.get("classes", []):
        res["class"] = "docs"
    elif paths and all(m or l for m, l in zip(is_man, is_lock)) and "patch-bump" in AA.get("classes", []):
        if not any(is_man): res["reasons"].append("lockfile-only change (no manifest to read versions from)")
        dev = set(AA.get("dev_tooling", []))
        for f, m in zip(files, is_man):
            if not m: continue
            if "patch" not in f: res["reasons"].append(f"{f['filename']}: no patch returned (too large or binary)"); continue
            try: pairs = pair_lines(f["patch"])
            except ValueError: res["reasons"].append(f"{f['filename']}: added/removed lines do not pair up"); continue
            if not pairs: res["reasons"].append(f"{f['filename']}: no paired changes"); continue
            for a, b in pairs:
                vc = version_only_change(a, b)
                if not vc: res["reasons"].append(f"{f['filename']}: non-version change: {b.strip()[:60]}"); continue
                name = dep_name(f["filename"], a)
                if name is None or dep_name(f["filename"], b) != name:
                    res["reasons"].append(f"{f['filename']}: not a dependency pin: {b.strip()[:60]}"); continue
                kind = bump_kind(*vc)
                short = name.rsplit("/", 1)[-1].lower()
                ok = kind == "patch" or (kind == "minor" and name != "?" and any(name.lower().startswith(d.lower()) or short.startswith(d.lower()) for d in dev))
                res["packages"].append({"file": f["filename"], "name": name, "from": vc[0], "to": vc[1], "kind": kind, "ok": ok})
                if not ok: res["reasons"].append(f"{name} {vc[0]}→{vc[1]} is {kind}" + ("" if kind != "minor" else " (not dev tooling)"))
        res["class"] = "patch-bump"
    else:
        res["reasons"].append("mixed or non-trivial file set")
    # reviews / threads
    reviews = gh(f"repos/{repo}/pulls/{pr}/reviews?per_page=100", paginate=True)
    bots = set(CFG.get("bots", []))
    last = {}
    for rv in reviews:
        u = rv["user"]["login"]
        if u in bots or u == ME: continue
        if rv["state"] in ("APPROVED", "CHANGES_REQUESTED"): last[u] = rv["state"]
    if "CHANGES_REQUESTED" in last.values(): res["reasons"].append("human CHANGES_REQUESTED open")
    res["human_approvals"] = sum(1 for s in last.values() if s == "APPROVED")
    q = 'query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100,after:$c){pageInfo{hasNextPage endCursor}nodes{isResolved}}}}}'
    open_t = 0; cursor = None
    while True:
        extra = ["-F", f"c={cursor}"] if cursor else []
        t = gh("graphql", "-f", f"query={q}", "-F", f"o={o}", "-F", f"r={r}", "-F", f"n={pr}", *extra)
        rt = t["data"]["repository"]["pullRequest"]["reviewThreads"]
        open_t += sum(1 for n in rt["nodes"] if not n["isResolved"])
        if not rt["pageInfo"]["hasNextPage"]: break
        cursor = rt["pageInfo"]["endCursor"]
    res["threads_open"] = open_t
    if open_t: res["reasons"].append(f"{open_t} unresolved review thread(s)")
    if repo in AA.get("require_bot_review", []):
        # bot verdict: one helper, shared with fetch-context/pr-scan/pr-watch/pr-merge — pass the
        # reviews we already fetched above, so this costs no extra gh call.
        bot_script = os.path.join(_KIT, "skills", "pr-watch", "bot-verdict.sh")
        tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        try:
            json.dump(reviews, tf); tf.close()
            env = dict(os.environ)
            if REVIEW_BOT: env["PR_WATCH_BOT_LOGIN"] = REVIEW_BOT
            try:
                p = subprocess.run(["bash", bot_script, repo, pr, head, tf.name], capture_output=True, text=True, env=env, timeout=60)
            except subprocess.TimeoutExpired:
                print(json.dumps({"eligible": False, "error": True, "reasons": ["bot-verdict.sh timed out after 60s"]}))
                sys.exit(2)
        finally:
            os.unlink(tf.name)
        if p.returncode == 0:
            color = p.stdout.strip()
        elif p.returncode == 2:
            color = "none"   # no bot configured — require_bot_review still needs one, so this repo is not eligible
        else:
            print(json.dumps({"eligible": False, "error": True, "reasons": [f"bot-verdict.sh failed: {p.stderr.strip()[:200]}"]}))
            sys.exit(1)
        res["bot"] = color
        if color != "green": res["reasons"].append(f"{REVIEW_BOT or 'review bot'} review on head not green ({color})")
    # CI
    pages = gh(f"repos/{repo}/commits/{head}/check-runs?per_page=100", paginate=True)
    cr = [c for pg in (pages if isinstance(pages, list) else [pages]) for c in (pg.get("check_runs", []) if isinstance(pg, dict) else [])]
    bad_runs = [c["name"] for c in cr if c["status"] != "completed" or c["conclusion"] not in ("success", "skipped", "neutral")]
    st = gh(f"repos/{repo}/commits/{head}/status")
    if st is None:  # a 200 with an empty body (rare, but indexing it blind is a traceback waiting to happen)
        print(json.dumps({"eligible": False, "error": True, "reasons": [f"gh api returned no data for repos/{repo}/commits/{head}/status"]}))
        sys.exit(1)
    st_map = {x["context"]: x["state"] for x in st.get("statuses", [])}
    res["ci"] = f"check-runs {len(cr)} ({len(bad_runs)} not green) · status {st['state']}/{st['total_count']}"
    required, prot_source, readable = required_checks(repo, base_ref)
    if required:
        green = {c["name"] for c in cr if c["status"] == "completed" and c["conclusion"] in ("success", "skipped", "neutral")} | {k for k, v in st_map.items() if v == "success"}
        missing = [x for x in required if x not in green]
        res["protection"] = {"required": required, "missing": missing, "source": prot_source}
        if missing: res["reasons"].append("required checks not green/present: " + ", ".join(missing[:4]))
    else:
        # no required context known: none configured, or neither source could be read (an older host
        # without the rulesets endpoint, a token that may not read protection). Either way every
        # check-run on head has to be green (bad_runs below) and there must be at least one.
        res["protection"] = "none" if readable else "unreadable"
        if not cr: res["reasons"].append("no check-runs on head")
    if bad_runs: res["reasons"].append("check-runs not green: " + ", ".join(bad_runs[:4]))
    if st["total_count"] and st["state"] != "success": res["reasons"].append(f"combined status {st['state']}")
    res["eligible"] = not res["reasons"] and res["class"] is not None
    print(json.dumps(res, ensure_ascii=False))

if __name__ == "__main__":
    main()
