#!/bin/sh
# scaffold_script (--scaffold only): runs in the empty scratch workspace before Claude starts. Brings the
# builder's contract in as ./builder.md, copied from the plugin under test at run time (never a copy in evals/): inside
# a `claude plugin eval` run the model cannot search the plugin, only read its own working directory.
set -eu
cp "$(dirname "$0")/../../agents/builder.md" ./builder.md
