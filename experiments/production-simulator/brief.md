# Molding Foundry: engineering and production simulator

Build a local application for exploring how part design, process settings, orders, and investment decisions affect a small injection-molding factory. A user should be able to explain why throughput changed, where material went, and what limited an order. This is the first playable slice of a future engineering and factory-planning game.

This experiment also tests Hive: the seed repository contains requirements and references; Hive writes and reviews all application code. Copy this file to `docs/brief.md`, the accompanying `sources.md` to `docs/sources.md`, and `plan.md` to `docs/plan.md` in the simulator repository before importing the plan.

## Product boundary

Start with rectangular, uniform-wall plaques, configurable thermoplastic properties, cold-runner molds with one or more identical cavities, one to four presses, resin inventory, and customer orders. Include changeovers, machine failures, repair, scrap, operating cost, and an optional shared packaging station. Make geometry and material changes meaningful rather than attaching arbitrary production bonuses to upgrades.

Show assumptions alongside results. The thermal model is an engineering approximation; defect probabilities, machine reliability, prices, and default material numbers are explicitly synthetic scenario parameters. There is no measured factory calibration in this release. Do not claim CFD, detailed rheology, dimensional warpage prediction, machine-control suitability, or a validated digital twin. Do not simulate drying, regrind, cavities of different shapes within one mold, shift calendars, finance, staffing, or supply-chain networks yet.

## Small implementation surface

- Python 3.12+, an importable `moldsim` simulation package, FastAPI, and Pydantic; `uv` for Python dependencies.
- TypeScript with Vite, ordinary HTML/CSS and SVG charts. No frontend framework is needed. Use accessible native controls before custom widgets.
- Python tests for the engine and API; browser acceptance tests with Playwright. The engine works without a web server or browser.
- Root `make setup` installs locked dependencies and the browser needed by acceptance tests; `make check` runs all tests and, once the frontend exists, its type check, production build, and browser acceptance. No silent skip of missing prerequisites. `make dev` starts the local application and prints its URL. `make check` is Hive's landing gate from task 1 onward.
- A CLI can validate a scenario and run a seeded simulation to JSON without opening the dashboard. Document its actual commands in README as soon as implemented.

Keep process calculations, simulation state transitions, serialization, and presentation separate. A small explicit event queue is sufficient; avoid a generic plugin platform. The future extension seam is a documented process operation consuming resources and producing batches. The packaging station proves that the engine supports another operation without copying the molding scheduler.

## Domain and units

Store explicit units in input field names and display labels. Use seconds, kilograms, meters, pascals, kelvin differences, kilowatts, kilowatt-hours, and EUR internally; convert mm, g, MPa, kN, and °C at the boundary. Never confuse hydraulic pressure with polymer cavity pressure.

The minimum domain includes material, part geometry, mold, machine, process recipe, order, scenario, simulation snapshot, event, and batch. Parts have material and geometry; molds have cavity count and runner volume/projected area; orders request integer good-part quantities and have a release time and due time. Machines have compatible mold IDs, maximum shot mass, clamp capacity, plasticizing rate or explicit recovery time, and state power draws. Recipes carry melt/mold/ejection temperatures, average cavity pressure, holding time, cooling target multiplier, fill and motion times, and a reference ideal cycle time. Every entity has a stable ID; quantities are finite, valid, and nonnegative where applicable. References, integer cavity/order counts, and temperature ordering are validated before a run.

Machine states are mutually exclusive and time-accounted: idle, setup, molding, starved, blocked, and repair. A physical mold is exclusive: it cannot be installed on two presses at once. Copying a mold explicitly creates another physical tool. A machine can carry at most one in-process shot. Scheduled times and event order are deterministic.

## Process model

### Cooling

Use the first-term plane-wall center-temperature approximation, with full wall thickness `h`, effective diffusivity `alpha`, initial melt temperature `Tm`, wall temperature `Tw`, and target center ejection temperature `Te`:

```
alpha = conductivity / (density * specific_heat)
t_required = h² / (pi² * alpha) * ln((4/pi) * (Tm-Tw)/(Te-Tw))
```

Assume uniform initial temperature, constant wall temperature and effective properties, and conduction through the two broad faces. Require `Tw < Te < Tm`; reject invalid logarithm/domain inputs. Restrict `(Te-Tw)/(Tm-Tw)` to at most `0.6` in this release to keep this first-term approximation away from the early-time region. Describe this as a center-temperature estimate, not an average-temperature or full-field solution. The model does not explicitly solve crystallization or latent heat. [MIT Appendix I, p.16](https://ocw.mit.edu/courses/2-008-design-and-manufacturing-ii-spring-2025/mit2_008_s25_hw1.pdf); [BASF cooling assumptions](https://pmtools-na.basf.com/quickcost/cycletime.html).

### Cycle and machine fit

Cooling starts at the end of filling and continues through holding. Plasticizing for the next shot starts after holding and overlaps remaining cooling. For this release, the first shot is prepared before the observation window; material still enters the shot ledger when the shot starts.

```
t_target = cooling_multiplier * t_required
t_closed_after_fill = max(t_target, t_hold + t_recovery)
t_cycle = t_close + t_fill + t_closed_after_fill + t_open_eject
t_recovery = shot_mass / plasticizing_rate   # or a documented fixed override
shot_mass = density * (cavities * part_volume + runner_volume)
clamp_required = safety_factor * average_cavity_pressure * total_projected_area
total_projected_area = cavities * part_projected_area + runner_projected_area
```

Expose phase timing and the active constraint; do not add cooling and recovery as though they were sequential. Reject a setup whose shot or required clamp exceeds machine capacity, whose temperatures violate the material's declared processing window, or whose mold is incompatible. The default clamp safety factor is a synthetic configurable engineering margin of 1.10. Cavity pressure is an input, not a solved injection-pressure distribution. [ENGEL on overlapping plasticizing](https://www.engelglobal.com/en/gb/blog/tips-for-process-optimisation-in-injection-moulding); [Autodesk clamp relationship](https://help.autodesk.com/cloudhelp/2014/ENU/MoldflowAdvisor/files/GUID-73BF9FA2-9CBE-46F4-B324-58FB7EB6E853.htm).

### Quality and reliability

Keep physical constraints distinct from the stochastic quality model. Each completed cavity yields exactly one good or rejected part. A rejected part still consumes its full nominal mass. Runners are scrap, never saleable output. Report rejection counts by cause without counting a part twice.

Begin with an explicit baseline reject probability and optional configurable penalties for insufficient attained cooling and insufficient holding pressure/time relative to scenario reference values. Use actual closed-after-fill time for cooling risk: lowering the target cannot increase risk when recovery already provides sufficient cooling. Specify the probability equation and its coefficients in a versioned scenario; bound its output to [0,1]. These are deliberately synthetic response curves, not BASF defect-rate predictions. Qualitative relationships are supported by [BASF's sink-mark and demolding discussion, pp.14–19](https://download.basf.com/p1/8a8082587fd4b608017fd6631d5a24b1/en/Injection-Molding_Problems_in_Engineering_Thermoplastics_-_Causes_and_Solutions).

Model failure time in cumulative molding operating seconds, with a configurable exponential mean time to failure and positive fixed repair duration initially. A failure interrupts a shot; all in-process shot material becomes scrap, no finished parts appear, and the stale completion event cannot later create output. Setup and repairs do not manufacture parts. Zero failure rate and zero reject probability provide deterministic reference mode. Document the chosen random generator and event tie-break order.

## Factory accounting and decisions

An order is dispatched only after release and receives only matching good parts. FIFO is the baseline; earliest due date is a selectable alternative with stable ID tie breaks. Do not begin another shot once good inventory plus compatible in-process output covers the remaining order; permit at most `cavities - 1` excess good parts on its final successful shot. Scrap may require extra shots. Prevent multiple presses from reserving the same remaining demand. Log why a machine is waiting.

Changing mold or material takes scenario-defined setup time and consumes explicit purge mass once. Keep purge distinct from cycle scrap. If a full shot or purge is unavailable, wait for a delivery; never create negative inventory. Deliveries arrive at explicit simulation times. A running shot retains its captured recipe; user recipe changes affect subsequent shots.

At every event, within numeric tolerance:

```
initial_resin + delivered_resin
  = remaining_resin + in_process_resin + good_part_mass + scrap_mass
completed_cavity_parts = good_parts + rejected_parts
completed_order_quantity <= requested_order_quantity
```

Good mass includes unshipped, packaged, and shipped parts exactly once. Packaging moves stock; it creates no resin or parts. Interrupted shots, runners, and purge all enter scrap. Time cannot go backward, capacity cannot be oversubscribed, and identical seed/scenario/actions cannot change with rendering speed or wall-clock pacing.

Report completed/late orders, finished good parts/hour, yield, resin consumption/scrap, energy, and operating cost. Energy is the time integral of mutually exclusive machine-state power; `kWh = sum(kW * seconds)/3600`. Cost is input resin consumed plus energy plus configured machine-hour cost; show its components and EUR/good part only when good count is positive. It excludes revenue, financing, depreciation, and amortization unless later explicitly added.

Use per-machine OEE only for a defined observation window with a fixed reference ideal cycle and product. Availability is operating time/planned time, performance is ideal cycle times completed shots/operating time, and quality is good parts/completed cavity parts. Changeovers, starvation, blocking, and repair count as availability loss inside the scenario's planned production window. Show unavailable ratios as “N/A”, never silently as zero or 100%. Label partial-cycle boundary loss. If performance exceeds 100%, report an invalid ideal-cycle reference instead of clamping it. Do not average mixed-product OEE percentages into a factory score. [Vorne's factor definitions](https://www.oee.com/oee-factors/).

## Reference cases and acceptance evidence

All numbers below are synthetic benchmark inputs, not a resin grade or machine recommendation. Implement fixtures independently of production calculation helpers.

| Case | Inputs | Expected result |
|---|---|---|
| Thermal baseline | `h=2 mm`, `alpha=0.1 mm²/s`, `Tm=220 °C`, `Tw=40 °C`, `Te=80 °C` | Required cooling `7.074820027656181 s`, absolute tolerance `1e-8 s` |
| Wall scaling | Same properties/temperatures, `h=4 mm` | `28.299280110624725 s`; exactly 4× within floating tolerance |
| Recovery overlap | Baseline cooling, close `1 s`, fill `1 s`, hold `4 s`, recovery `6 s`, open/eject `3 s`, multiplier `1` | Cycle `15 s`, recovery-limited; lowering cooling multiplier cannot shorten it |
| Clamp | Total projected area `0.005 m²`, average cavity pressure `40 MPa`, margin `1.10` | Required clamp `220 kN`; a `200 kN` press is incompatible |
| One-hour conservation | Two `50×45×2 mm` plaques, density `1000 kg/m³`, runner `1 cm³`, cycle `15 s`; no setup, failures, scrap defects, or demand limit; `3 kg` resin | At `t=3600 s` exactly `240` completed shots, `480` good parts, good mass `2.16 kg`, runner scrap `0.24 kg`, resin remaining `0.60 kg`, no new shot/WIP after the horizon |
| Starvation and recovery | Above case with `0.025 kg` resin; `0.015 kg` arrives at `t=60 s` | Two shots finish by `t=30`, then starvation; delivery permits two further shots; no negative inventory |
| Partial order | Five good parts, two cavities, zero rejects | Three shots, five allocated/shipped, one excess good part; no fourth shot |
| State energy | `10 kW` molding for `1800 s`, `2 kW` idle for `1800 s` | `6 kWh`; at `0.20 EUR/kWh`, energy cost `1.20 EUR` |
| OEE arithmetic | Planned `3600 s`, operating `3000 s`, ideal cycle `10 s`, `270` completed single-cavity shots, `243` good parts | Availability `5/6`, performance `0.9`, quality `0.9`, OEE `0.675` |

In addition to these hand-computed cases, test simultaneous events, a repair interrupted mid-shot, a delivery at a completion boundary, canceled pending actions, invalid saved data, save/load in the middle of a shot, and reserve collisions between two presses. A complete run and a checkpoint/resume run must produce identical canonical event records and final metrics. Persist the RNG state, pending event sequence, next IDs, future commands, and in-process recipe/material reservations. Never serialize executable code or pickle user uploads.

## Interaction and scaling experiment

The dashboard should make engineering cause and production effect visible together: a machine/cycle diagram, phase timing, current bottleneck, order queue, inventory/scrap, production/energy/cost charts, and an inspectable event log. Provide pause, step-event, advance-time, pacing speed, reset with seed, save, and load. Editing a pending order or future recipe is an explicit logged action; past output does not change. An invalid edit explains the field and leaves state unchanged.

Ship a baseline scenario and two independently rerunnable variants: a faster press/duplicate mold, and a downstream packaging improvement. With packaging enabled, presses discharge good batches into a finite buffer; buffer-full blocking prevents another shot, with completed material held and counted once. Transfer a whole completed batch atomically when space permits; validate that buffer capacity in individual parts is at least the largest mold's cavity count. Packaging has explicit seconds/part, consumes ready parts, and completes orders only after packaging. Compare variants under the same saved inputs/seed and report confidence limits from multiple seeds only when stochastic behavior is enabled. Additional press capacity should help while molding limits output, then stop helping once packaging becomes the bottleneck. The UI must explain that transition from measured utilization, blocked time, and queue history.

After these tasks, the extension backlog can include maintenance policies, calibrated material/process windows, secondary operations, workers, shift patterns, inventory purchasing, and game objectives. They are future work, not placeholders to build now.
