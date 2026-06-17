# Mission

Hive is a system that uses coding agents and LLMs to continuously work on software projects.

It manages a portfolio of projects for a user (later: an organization). Each project has a high-level mission and a concrete, timeboxed iteration goal. Hive keeps agents working toward the iteration goal as fast as available resources allow — and stops to ask the human precisely when the spec is ambiguous in ways that matter. A large part of the value is not the coding itself but the **spec clarification loop**: hive distinguishes "spec is clear, build the next bit" from "there's a contradiction/gap, resolve with human input first," and clarification answers accumulate into an ever-more-precise spec.

Product-shaped from day one (others should eventually self-host it), but early versions are maximally simple and personally runnable, reusing GCP managed services where they simplify things.

**Anti-bloat is a core principle.** The known failure mode of autonomous development is complexity growth ("we could also check this extra thing"). All tests and CI must verify something with real value. Distilling specs, code, tests, and CI is a priority and a prerequisite for success — it is baked into the verifier's checklist and is the heart of the future Maintain mode.

**Proactive autonomy, with a visible human/AI boundary.** Hive should make progress on the human's intention without waiting for a fully specified slice. Two principles bound this:

1. **Clarity: separate what the human specified from what Hive assumed.** Every assumption Hive makes instead of asking is recorded as a provenance-tagged entry in a decision ledger (`source_type`, `impact`, `reversibility`, `expires_when`, `status`), so the human can always see — and flip — what the AI decided on its own. A guess that silently becomes a "requirement" is the boundary dissolving.
2. **Leverage: Hive decides by default and asks only when it must.** There is no list of what Hive is allowed to do — it may decide anything and logs it. Work leaves the default-act path two ways: a small **`must_ask`** set of product-sensitive categories the human reserves (auth/permissions, billing/pricing, data retention and destructive defaults, public-API contracts, legal/compliance wording, security-sensitive defaults — a global default, extended per project only when it differs); and a danger Hive spots in an unlisted decision, where it stops and **names the concrete danger** ("I'm unsure" is not a valid reason to ask). `guess_propensity` tunes only how readily Hive escalates a self-spotted danger. The readiness gate is therefore not "is everything specified?" but "is enough known for this increment, with the rest either decided-and-logged, `must_ask`, or a named danger?"

Full design in `wiki/proactive-autonomy.md`.
