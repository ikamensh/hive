# Decision ledger

## HIVE-MVP-001 · Demo target is Hive itself
source_type: user_provided
impact: high · reversibility: medium · status: accepted
expires_when: after the MVP proof completes or the operator selects another demo target
trace: input-log/2026-06-23-intake.md

The first real MVP demo target is the `hive` project itself, using the same repository as spec home and code repo.

## HIVE-MVP-002 · Verified progress can be code or durable spec
source_type: user_provided
impact: medium · reversibility: high · status: accepted_for_iteration
expires_when: when defining the next iteration's completion gate
trace: input-log/2026-06-23-intake.md

For this proof, verified progress may be either a tested code change or a durable spec-only update, as long as Hive independently verifies or clearly records the result.

## HIVE-MVP-003 · Trusted scout/backend may be Codex or Claude
source_type: user_provided
impact: medium · reversibility: high · status: accepted_for_iteration
expires_when: when backend policy is revisited after runner validation
trace: input-log/2026-06-23-intake.md

Either Codex or Claude is acceptable for the required scout/backend, provided the backend is usable and trusted on the operator machine.

## HIVE-MVP-004 · Direct push is acceptable for MVP proof
source_type: user_provided
impact: medium · reversibility: high · status: accepted_for_iteration
expires_when: before requiring PR-mode proof for production use
trace: input-log/2026-06-23-intake.md

Direct-push mode is acceptable for the first MVP proof; PR mode is not required in this iteration.

## HIVE-MVP-005 · Managed GCP state remains intended backend
source_type: user_provided
impact: high · reversibility: medium · status: accepted
expires_when: if Hive's deployment model changes away from Firestore/GCS
trace: input-log/2026-06-23-intake.md

The operator proof should use managed GCP state (Firestore and GCS), not a separate local-file runtime mode.

## HIVE-MVP-006 · Local demo means operator-initiated, not a new product mode
source_type: user_provided
impact: medium · reversibility: high · status: accepted
expires_when: after local/distributed operating docs are validated
trace: input-log/2026-06-23-intake.md

The proof may use Hive's existing local and/or distributed modes. “Local demo” means the operator initiates the proof from this working environment; it does not create a new product mode.

## HIVE-MVP-007 · Main risk is proving the real demo path
source_type: agent_proposed
impact: medium · reversibility: high · status: accepted_for_iteration
expires_when: once a full dogfood trace exists
trace: input-log/2026-06-23-intake.md

The launchpad directive/checkout slice appears mostly present; the main remaining risk is proving the full real demo path and tightening gaps found by evidence.

## HIVE-MVP-008 · Prior CLI failure likely env leakage
source_type: agent_proposed
impact: low · reversibility: high · status: accepted_for_iteration
expires_when: after the CLI test is made environment-isolated or proven otherwise
trace: input-log/2026-06-23-intake.md

The observed `test_run_chief_requires_managed_state` failure is likely caused by ambient real `HIVE_*` variables leaking into the test environment rather than product logic.
