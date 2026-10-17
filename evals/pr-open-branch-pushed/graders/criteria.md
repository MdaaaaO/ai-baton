---
type: llm
weight: 1
---

The response treats the request as opening a pull request properly: it works through, or lists as what it will do, at least two of — a PR body (overview, changes, diagrams, test plan), labels, reviewers, a commit-style check of the title, a tracker link, watching the PR, a review request drafted rather than sent. It is not only generic git advice. The run starts in an empty scratch directory with no repo, branch, remote, workspace, tracker, chat or GitHub access, so judge the approach, not whether an action succeeded: stopping early to name what is missing (the branch or repo, a config value, a tool) is fine, provided the response also lists at least two of the above as what it will do once that is supplied.
