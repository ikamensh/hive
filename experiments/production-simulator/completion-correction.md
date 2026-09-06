# Correct the completed simulator's summary against the current implementation

The ten-item Molding Foundry simulator plan is implemented and independently validated. Its automatic completion commit `168ebd73947c7386b846947daaef389936611844` introduced an inaccurate historical detail in `wiki/landed.md`: it says engine contract 2 although the reviewed packaging/game implementation uses engine contract 3. Correct the completion documentation using the current repository as evidence. Preserve the completed implementation and its historical plan.

## Correct stale facts in the automatic completion notes

Review `wiki/landed.md` and `iteration.md` against current source and the current developer guides. Replace the stale hardcoded engine-contract version with a concise, source-linked explanation of versioned, replay-validated checkpoints. Describe multi-press support and the valid zero-resource case accurately; avoid implying an enforced one-to-four-press schema constraint. Keep model assumptions and any timing measurements properly scoped. Prefer a concise delivered-scope summary to copying internal details from older task reports. Preserve the useful setup/run instructions and the original ten-item completion record.

Acceptance: verify the actual current checkpoint contract and resource validation through a public interface, then review every factual change against source. Change documentation only. Keep the existing application byte-identical, add no tests that merely check prose, and run the existing `make check` gate. The fresh reviewer must inspect the original incorrect completion note and the current implementation independently before accepting the corrected documentation.
