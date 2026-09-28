# Recipes

Counting (cheap, one call, exact):
```sh
gh api -X GET search/issues -f q="author:<login> type:pr user:<org> is:merged merged:>=<since>" --jq .total_count
gh api -X GET search/issues -f q="reviewed-by:<login> -author:<login> type:pr user:<org> updated:>=<since>" --jq .total_count
```

Listing with fields (search, ≤1000, paged 100):
```sh
gh api -X GET search/issues -f q="…" -f per_page=100 --paginate \
  --jq '.items[] | "\(.repository_url|sub(".*/repos/";""))\t\(.number)\t\(.user.login)\t\(.created_at)"' > list.tsv
```
`--jq` per page is fine here because each row is independent.

Per-PR details in a loop (jq flags outside `gh`):
```sh
while IFS=$'\t' read -r repo num rest; do
  gh api "repos/$repo/pulls/$num" 2>>err.txt \
    | jq -c --arg r "$repo" '{repo:$r,n:.number,add:.additions,del:.deletions,files:.changed_files,merged:.merged_at}' >> sizes.jsonl
  gh api "repos/$repo/pulls/$num/reviews" --paginate 2>>err.txt \
    | jq -sc --arg n "$num" 'add // [] | {n:$n, states:[.[].state], reviewers:([.[].user.login]|unique)}' >> revs.jsonl
done < list.tsv
echo "rows=$(wc -l < sizes.jsonl) errs=$(wc -l < err.txt)"   # both, always
```

Paced search sweep (background):
```sh
ORG=$(python3 $BATON/context-db/bin/kit_profile.py get github.org)
for l in "${LOGINS[@]}"; do for w in <window-start-1> <window-start-2>; do
  n=$(gh api -X GET search/issues -f q="author:$l type:pr user:$ORG is:merged merged:>=$w" --jq .total_count 2>>err.txt)
  printf '%s\tauthored\t%s\t%s\n' "$l" "$w" "$n" >> cells.tsv; sleep 2.2
done; done
```
Afterwards: `awk -F'\t' '$4==""' cells.tsv` lists the cells to re-run.

Reading a file from a repo without cloning:
```sh
gh api repos/<org>/<repo>/contents/<path> --jq .content | base64 -d
gh api repos/<org>/<repo>/contents/<dir> --jq '.[] | .path'        # directory listing
```
