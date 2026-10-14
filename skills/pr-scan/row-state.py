#!/usr/bin/env python3
"""row-state.py --queue FILE --ledger FILE

Derives one `state` (+ `section`, `state_label`) per pr-scan candidate row from the row's own
`kind` and `mine` (our review on the row's head, both set by pr-scan.sh) and the pr-review
ledger's entries for that PR. No `gh` calls — everything needed is already in queue.json and
the ledger.

Precedence (first match wins), per row:
  1. kind == follow_up (author replied after our review)  -> needs_you (author replied)
  2. kind == done, our APPROVE stands on this head        -> watching (you approved)
  3. kind == done, our REQUEST_CHANGES stands on it       -> watching (you requested changes)
  4. kind == done otherwise (a review the kit posted)     -> handled this tick
  5. a `held` ledger entry on this exact head             -> needs_you (STOP)
  6. an older-head review, or kind == re_review           -> follow-up (auto|manual policy)
  7. otherwise                                            -> new (auto|manual policy)

`follow_up` and `done` both mean our review already sits on the row's head, so they are read
before the ledger: a reply in a thread we opened needs an answer whatever verdict we left, and
a hold recorded before that review is resolved by it. A `done` row's verdict is the row's own
`mine.state` (the reviews API); when that is missing (the reviews fetch degraded) the ledger's
`event` for this head stands in. pr-scan.sh keeps a `done` row while such a verdict stands on
an open PR, and once for a kit-posted COMMENT (the next scan drops it as done).

The auto/manual policy bit for states 6 and 7 is read straight off the row's own
`auto_comment.eligible` field (already computed by pr-scan.sh's gh-call-free gate) —
this script does not re-read config.json or recompute eligibility itself. An
auto-policy follow-up (a direct re-request `auto_comment` already covers via
`include_re_review`) needs no action from the user, same as an auto-eligible new
request, so it renders in the New section; only a manual-policy follow-up — the one
that actually needs a human decision — lands in the Follow-up section, matching that
section's "— manual" name.

Reads queue.json (a JSON array) and ledger.jsonl (JSON-lines, may be missing or
empty); writes the same array back to stdout with `state`, `section` and
`state_label` added to every row.
"""
import argparse
import json
import sys

SECTION_NEEDS_YOU = "Needs you"
SECTION_NEW = "New — not started"
SECTION_HANDLED = "Handled this tick"
SECTION_FOLLOWUP = "Follow-up — manual"
SECTION_WATCHING = "Watching"


def load_ledger(path):
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return rows


POSTED = ("reviewed", "auto_commented")
VERDICTS = {"APPROVED": "approve", "APPROVE": "approve",
            "CHANGES_REQUESTED": "request_changes", "REQUEST_CHANGES": "request_changes"}


def derive_state(row, ledger_rows):
    repo, pr, head = row.get("repo"), row.get("pr"), row.get("head")
    kind = row.get("kind", "new")
    mine = [e for e in ledger_rows if e.get("repo") == repo and e.get("pr") == pr]
    cur = [e for e in mine if e.get("head") == head]
    posted_cur = [e for e in cur if e.get("status") in POSTED]

    if kind == "follow_up":
        return "needs_you", SECTION_NEEDS_YOU, "needs you (author replied)"
    if kind == "done":
        own = row.get("mine") or {}
        verdict = VERDICTS.get(own.get("state")) or next(
            (VERDICTS[e.get("event")] for e in reversed(posted_cur) if e.get("event") in VERDICTS), None
        )
        if verdict == "approve":
            return "watching", SECTION_WATCHING, "watching (you approved)"
        if verdict == "request_changes":
            return "watching", SECTION_WATCHING, "watching (you requested changes)"
        review_id = next(
            (e.get("review_id") for e in reversed(posted_cur) if e.get("review_id") is not None),
            own.get("review_id"),
        )
        label = f"handled (review #{review_id})" if review_id is not None else "handled"
        return "handled", SECTION_HANDLED, label
    if any(e.get("status") == "held" for e in cur):
        return "needs_you", SECTION_NEEDS_YOU, "needs you (STOP)"

    older_posted = any(e.get("status") in POSTED and e.get("head") != head for e in mine)
    policy = "auto" if (row.get("auto_comment") or {}).get("eligible") else "manual"
    if older_posted or kind == "re_review":
        section = SECTION_NEW if policy == "auto" else SECTION_FOLLOWUP
        return "follow_up", section, f"follow-up ({policy})"
    return "new", SECTION_NEW, f"new ({policy})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--ledger", required=True)
    args = ap.parse_args()

    with open(args.queue) as f:
        rows = json.load(f)
    ledger_rows = load_ledger(args.ledger)

    out = []
    for row in rows:
        state, section, label = derive_state(row, ledger_rows)
        out.append({**row, "state": state, "section": section, "state_label": label})
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
