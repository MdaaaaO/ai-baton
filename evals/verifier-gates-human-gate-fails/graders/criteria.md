---
type: llm
weight: 1
---

A human gate is a FAIL because it belongs to the owner: the verifier never signs it, even when the numbers look right (agents/verifier.md § 5 step 2, § Never). The FAIL line's tail names whichever gates were signed (§ 6); it does not assume VG-1 is unsigned, so an answer that signs VG-1 and names it there is not graded down. Signing VG-2 fails the case.
