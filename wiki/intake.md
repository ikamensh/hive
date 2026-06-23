# Intake log: Hive-on-Hive MVP dogfood

## Final brief

**Mission:** Hive is an autonomous software-work system for the user's project portfolio. It takes a high-level mission plus a concrete iteration goal, keeps coding agents working as capacity allows, verifies work independently, and stops only for material human decisions. Its differentiator remains the spec-clarification loop: assumptions are visible, human decisions stay distinct, and bloat is rejected.

**Next iteration:** Prove the MVP path with a real operator run using Hive itself as both the spec repo and code repo. Use the existing supported runtime modes as appropriate: local mode for operator-driven setup and/or distributed mode where chief and runner split across processes or machines. Start Hive against managed GCP state, register/probe any usable trusted runner backend, run web-driven intake for Hive, approve finalized durable specs via direct push, then let Hive start planning/building until it produces verified progress or parks on a concrete question/resource blocker.

## Accepted assumptions

- Demo target is `hive` itself.
- Verified progress may be either a tested code change or a durable spec-only update, as long as Hive independently verifies or clearly records the result.
- Either Codex or Claude is acceptable as the required scout/backend, as long as it is usable and trusted on the operator machine.
- Direct push is acceptable for the MVP proof; PR mode is not required for this iteration.
- GCP managed state remains the intended state backend.
- “Local demo” should not imply a separate product mode. It means the operator initiates the proof from this working environment, using Hive's existing local and/or distributed modes.
- The launchpad directive/checkout slice is mostly present; the main risk is proving the full real demo path.
- A prior CLI test failure was probably environment leakage from real `HIVE_*` variables, not necessarily product logic.

## Likely next steps

1. Stabilize operator preflight: managed-state startup, CLI/web resource visibility, and clear failure modes.
2. Verify runner capacity end to end: backend detection, registration, usability probes, cooldown/auth-block handling.
3. Dogfood web intake against `hive`: repo selection, scout brief, answer/proceed/approve, durable spec direct push.
4. Run one small build loop: orchestrator task, runner execution, independent verify, question/resource handling.
5. Convert demo evidence into spec refinements, cuts, missing instrumentation, or targeted fixes.

## Evidence

- Read: `mission.md`, `iteration.md`, `wiki/architecture.md`, `wiki/code-map.md`, `wiki/project-intake.md`, `wiki/proactive-autonomy.md`, `wiki/project-launchpad.md`.
- Inspected implementation/tests around intake, directives, checkouts, API, runner results, and web project setup.
- Ran targeted repository searches and `uv run pytest tests/`; observed `247 passed, 1 failed`, where `tests/test_cli.py::test_run_chief_requires_managed_state` appeared sensitive to ambient managed-state environment variables.

## Remaining material questions

None at the brief level. Implementation details can be self-answered while proving the path.
