# Build Molding Foundry: a reproducible injection-molding and factory-planning simulator

Use `docs/brief.md` as the model/acceptance contract and `docs/sources.md` for provenance. Build a useful local engineering sandbox that can grow into a production-planning game. Implement each task as a complete reviewed increment. Keep `make setup`, `make check`, and `make dev` documented and working as their components arrive. Hive should run `make check` before landing each item. Preserve prior acceptance checks; distinguish synthetic scenario parameters from physical approximations in UI and documentation.

## Establish executable domain and scenario contracts

Create the Python `moldsim` package, locked environment, validated scenario models, README, and root Make targets described in the brief. Define explicit units and IDs for material, part, mold, machine, recipe, order, deliveries, and simulation settings. Add a complete synthetic baseline scenario and the independent numerical reference inputs. Provide a CLI that validates a scenario and returns actionable errors. Keep the initial development command useful by exposing scenario inspection through a minimal FastAPI endpoint.

Acceptance: from a clean checkout, `make setup` and `make check` succeed; valid JSON round-trips, duplicate/broken references and invalid units/counts/temperature order fail clearly; importing the engine does not start a server. Establish the real test gate now without placeholder always-pass tests.

## Calculate cooling, cycle phases, material demand, and machine compatibility

Implement the brief's cooling approximation, volume/mass calculations, phase overlap, clamp requirement, shot capacity checks, and explanatory constraint results. Surface them through CLI and API scenario inspection. Source comments should identify the equation and assumption, not imply calibrated material data.

Acceptance: match the independent thermal, thickness-scaling, 15-second overlap, and 220-kN clamp cases. Test unit conversion and monotonic relationships; reject impossible setups before simulation. Report actual cooling time and the limiting phase. Do not double-count holding, recovery, and cooling.

## Run deterministic molding events with a conserved material ledger

Build headless start, pause, single-event step, and advance-to-time operations around a deterministic event queue. Implement machine/shot states, resin reservations, completed cavity output, runner scrap, starvation, and timed deliveries. Snapshot state includes in-process material and a useful event log. Support a CLI seeded run to JSON.

Acceptance: reproduce the one-hour 480-part/2.4-kg-consumed benchmark and the starvation/delivery case; verify conservation after every event and around the observation horizon. Same scenario/seed produces the same canonical trace regardless of step sizes. Simultaneous events cannot duplicate completion, manufacture at time zero, or start unwanted work after the final horizon.

## Schedule real orders and changeovers across presses

Add released/due orders, stable FIFO and earliest-due-date selection, compatible machine/mold assignment, exclusive physical molds, setup duration, purge mass, and demand reservations. Log waiting reasons and order completion/lateness. Pending order changes and recipe edits become timestamped commands; active shots retain their original recipe.

Acceptance: five requested parts with two cavities take three good shots and leave one excess part. Two presses cannot allocate the same demand or use one mold simultaneously. Verify setup/purge happen once, inventory never goes negative, and future-order edits change subsequent dispatch without rewriting past output. A scenario comparing FIFO and due-date scheduling shows an explainable delivery tradeoff.

## Add transparent quality, failure, energy, and cost behavior

Implement seeded baseline rejects and the documented configurable cooling/holding penalties. Add failures sampled in molding operating time, fixed-duration repair, and interrupted-shot scrap. Compute per-state energy, resin/energy/machine-time operating cost, yield, and scoped OEE. Store assumptions in scenarios and explain reasons in events.

Acceptance: reject probabilities 0 and 1 produce exact outcomes; interrupted shots consume mass and never later complete; deterministic mode remains unchanged. Match the independent 6-kWh and 0.675-OEE cases. Test that cooling-target changes have no artificial quality penalty when recovery already supplies sufficient cooling. No parts, energy, scrap, or costs are counted twice; zero denominators display unavailable values explicitly.

## Persist and replay simulations through a usable control API

Implement versioned JSON save/load for scenario, state, pending events, IDs, RNG state, and future commands. Add API operations for run control, snapshots, order changes, future recipe changes, and save/load. Validate input transactionally and use explicit errors for unsupported versions or malformed state. Document reproducible CLI and HTTP examples.

Acceptance: save during an active shot and during repair; reload and finish with byte-equivalent canonical event records and identical metrics to uninterrupted runs. Invalid loads/edits leave existing state unchanged. Changes appear in a replayable action log; frontend pacing cannot alter outcomes. Test the full behavior through the API, not only serialization helpers.

## Build an interactive engineering and factory dashboard

Create the Vite/TypeScript interface: scenario selection, units-aware recipe editor, visible phase/constraint diagram, machine states, orders, resin and scrap, production/energy/cost history, and inspectable event log. Provide pause, step, advance, speed, reset/seed, save/load, and pending-order controls. Show model assumptions near engineering results; make validation errors actionable. Use responsive CSS and labeled keyboard-operable controls.

Acceptance: browser tests exercise load→run→pause→edit future recipe/order→step→save→reset→load and check real changed metrics. Add frontend type check/build and browser acceptance to `make check`. Inspect screenshots at desktop and narrow width and fix clipping, unreadable charts, and misleading states. Run a production build served by the documented app command; no mocked dashboard data or network dependency on paid services.

## Demonstrate realistic scaling and an extensible second operation

Add an optional packaging operation with explicit capacity, finite input buffer, and correct blocking/material transfers. Package good parts before shipping when enabled. Supply rerunnable baseline, extra-press/duplicate-mold, and packaging-upgrade scenarios; add side-by-side results and bottleneck explanation to the dashboard. Use the operation seam for packaging instead of copying the molding scheduler. Document extension points and a short future-game backlog.

Acceptance: adding a press increases output when molding is limiting; under a packaging constraint, additional molding capacity increases waiting/buffer pressure instead of magically increasing shipments; upgrading packaging improves that scenario. Verify conservation, exclusive resources, no buffer overflow, deterministic replay, and zero-demand/zero-resource behavior. Compare stochastic variants across a documented seed set and distinguish sampling uncertainty from model fidelity. A clean `make setup`, `make check`, and `make dev` plus an illustrated README walkthrough reproduce the complete experiment.
