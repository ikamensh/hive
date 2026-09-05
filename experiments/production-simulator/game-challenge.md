# Build Molding Foundry: a reproducible injection-molding and factory-planning simulator

Use `docs/brief.md` as the model/acceptance contract and `docs/sources.md` for provenance. Build a useful local engineering sandbox that can grow into a production-planning game. Implement each task as a complete reviewed increment. Keep `make setup`, `make check`, and `make dev` documented and working as their components arrive. Hive should run `make check` before landing each item. Preserve prior acceptance checks; distinguish synthetic scenario parameters from physical approximations in UI and documentation.

## Add a reproducible production-contract challenge

Build on the validated simulator and packaging operation to add one game-like production contract: a due-date target, finite investment budget, and a small set of explicit capacity upgrades. Let the player choose upgrades and run the same seeded scenario, then explain which orders shipped on time and whether the investment stayed within budget. Reuse the existing engine and controls; keep fictional prices and game scoring distinct from the physical model.

Acceptance: provide an independently checkable baseline failure and successful upgrade strategy, enforce the investment budget and resource limits, preserve save/replay, and add browser coverage for choosing an upgrade and seeing the resulting contract outcome. Document a repeatable illustrated walkthrough and the model assumptions. Run the full make check gate.
