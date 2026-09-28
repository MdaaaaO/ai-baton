# signed-git-commits

The why and the discipline behind signed commits where the machine that edits cannot sign or push:
worktree-first branching, exact staging, message-to-file, rebase quirks, the gh token prefix. The hand-off
itself runs through the `sign-queue` skill — never pasted git commands.

**Needs:** `systems.signed_commits`. No env fact rows.

**Without it:** `signed-git-commits: not applicable here — signed_commits is false`, one line, stop —
commits are made and pushed directly.

**Example:**

> **user:** commit this and open the PR
>
> **claude (in a signed-commits repo):** creates a worktree branch, stages exactly the change, writes the message
> to a file, enqueues the job with `sign-queue`, and tells the user to run `make sign` on the host before
> opening the PR.
