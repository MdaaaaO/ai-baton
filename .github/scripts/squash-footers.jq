# squash-footers.jq — the conventional footer block that ends a PR body: what auto-merge.yml passes as the
# squash commit's message, and what check-pr-footers.sh judges. One parser for both.
#
#   jq -Rjs -f .github/scripts/squash-footers.jq < body.md      (input: the body as one raw string)
#
# A footer is one line, `Token: value` or `Token #N` (`BREAKING CHANGE` is the one token with a space;
# `Closes #12`, `Refs #12`, `BREAKING CHANGE: run kb.py migrate`). The block is the run of footer lines at the
# very end of the body, after a blank line (or the whole body). Output: those lines, or "" when the body ends
# in anything else. Nothing else of the body — prose, HTML comments, code — ever reaches the commit.
# Pure jq: the body is data, never evaluated.
def footer: test("^(BREAKING CHANGE|[A-Za-z][A-Za-z-]*): \\S") or test("^[A-Za-z][A-Za-z-]* #[0-9]+$");

gsub("\r"; "") | sub("\\s+$"; "") | split("\n") as $lines
| ([$lines | to_entries[] | select(.value | footer | not) | .key] | last // -1) as $prose
| if $prose == -1 or ($lines[$prose] | test("^\\s*$"))
  then $lines[$prose + 1:] | join("\n")
  else ""
  end
