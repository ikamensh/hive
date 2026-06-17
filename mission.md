# Mission

Hive is a system that uses coding agents and LLMs to continuously work on software projects.

It manages a portfolio of projects for a user (later: an organization). Each project has a high-level mission and a concrete, timeboxed iteration goal. Hive keeps agents working toward the iteration goal as fast as available resources allow — and stops to ask the human precisely when the spec is ambiguous in ways that matter. A large part of the value is not the coding itself but the **spec clarification loop**: hive distinguishes "spec is clear, build the next bit" from "there's a contradiction/gap, resolve with human input first," and clarification answers accumulate into an ever-more-precise spec.

Product-shaped from day one (others should eventually self-host it), but early versions are maximally simple and personally runnable, reusing GCP managed services where they simplify things.

**Anti-bloat is a core principle.** The known failure mode of autonomous development is complexity growth ("we could also check this extra thing"). All tests and CI must verify something with real value. Distilling specs, code, tests, and CI is a priority and a prerequisite for success — it is baked into the verifier's checklist and is the heart of the future Maintain mode.

**Proactive autonomy, with a visible human/AI boundary.** Hive should make progress on the human's intention without waiting for a fully specified slice. Two principles bound this:

1. **Clarity: separate what the human specified from what Hive assumed.** Every assumption Hive makes instead of asking is recorded as a provenance-tagged entry in a decision ledger (`source_type`, `impact`, `reversibility`, `expires_when`, `status`), so the human can always see — and flip — what the AI decided on its own. A guess that silently becomes a "requirement" is the boundary dissolving.
2. **Leverage: Hive proceeds autonomously on everything it is authorized to decide.** A per-project **agent authority** contract draws a hard boundary: a `must_ask` set of product-sensitive categories (auth/permissions, billing/pricing, data retention and destructive defaults, public-API contracts, legal/compliance wording, security-sensitive defaults) that Hive never guesses, and a `may_decide` set (internal APIs, schema shape, tests, refactors, minor UX) that is always Hive's to choose. The `guess_propensity` dial governs only the gray zone between them. The readiness gate is therefore not "is everything specified?" but "is enough known for this increment, with the rest either authorized to Hive, explicitly assumed, or isolated as a question?"

Full design in `wiki/proactive-autonomy.md`.
