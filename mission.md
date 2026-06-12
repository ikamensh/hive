# Mission

Hive is a system that uses coding agents and LLMs to continuously work on software projects.

It manages a portfolio of projects for a user (later: an organization). Each project has a high-level mission and a concrete, timeboxed iteration goal. Hive keeps agents working toward the iteration goal as fast as available resources allow — and stops to ask the human precisely when the spec is ambiguous in ways that matter. A large part of the value is not the coding itself but the **spec clarification loop**: hive distinguishes "spec is clear, build the next bit" from "there's a contradiction/gap, resolve with human input first," and clarification answers accumulate into an ever-more-precise spec.

Product-shaped from day one (others should eventually self-host it), but early versions are maximally simple and personally runnable, reusing GCP managed services where they simplify things.

**Anti-bloat is a core principle.** The known failure mode of autonomous development is complexity growth ("we could also check this extra thing"). All tests and CI must verify something with real value. Distilling specs, code, tests, and CI is a priority and a prerequisite for success — it is baked into the verifier's checklist and is the heart of the future Maintain mode.
