---
type: llm
weight: 1
---

`systems.signed_commits` is false, so this is the skill's own gate case (SKILL.md: "On a machine where
`signed_commits` is false, print `signed-git-commits: not applicable here — signed_commits is false` and
stop — commits are made and pushed directly"). The response states, in one line, that
signed-git-commits is not applicable here because signed_commits is false, and stops the skill's own
discipline there. It does not require a dedicated worktree, does not enqueue a `sign-queue` job, and does
not insist on a message-to-file hand-off — at most it makes and pushes the commit directly (an ordinary
`git commit` / `git push` in the current working tree), the way the skill itself says a machine without
the ruleset works.
