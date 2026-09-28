---
type: llm
weight: 1
---

The response treats the request as a self-check of this session against the kit: it runs, or says it will run, the transcript extraction and judges what it finds (user corrections, denials, failed commands, rule slips) as a kit gap or a session slip, offering an issue per kit gap and a memory per slip. It does not write a weekly self-assessment, run a session close-out or review a pull request. The run starts in an empty scratch directory with no transcript, workspace or GitHub access, so judge the approach: stopping with "no transcript found" is fine.
