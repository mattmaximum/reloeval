# TODOs

Carried over from the design doc's Deferred Work section (`/office-hours` +
`/plan-eng-review`, 2026-07-22).

- **Personalized scoring formula.** Design the weighted-sum scoring function across
  schema fields, spanning both the family-decision and resilience/self-sufficiency
  lenses. Unlocks the core delight feature from office-hours ("coolest version").
  Depends on the core pipeline being run against a few real cities first —
  designing weights before real data exists to weight would be premature.
- **Diff/compare mode.** A second template rendering two cities' JSON side by side.
  Confirmed during /plan-eng-review's outside-voice pass as nearly free once the
  JSON store + render template exist — no new fetch or architecture needed. Needs
  at least 2-3 cities evaluated first to be useful.
- **Measure the spot-check citation burden.** After evaluating the first 3-5 real
  cities, measure actual time spent spot-checking citations (~65 fields/city could
  mean 300+ citations across a small city set). Raised by /plan-eng-review's
  outside-voice pass as an uncosted risk to the "replaces manual research" premise;
  chose to build the full pipeline anyway — this is the checkpoint to revisit that
  call with real usage data.
- **Local workflow testing via `act`.** Set up the `act` CLI to run the GitHub
  Actions workflow locally in Docker instead of pushing and waiting for a real
  run. Not needed for the initial build; worth doing the first time the workflow
  needs debugging after it's live. Doesn't perfectly replicate GitHub-hosted
  runners, so treat as a fast local approximation, not a full substitute for a
  real E2E test.
- **Manual re-run trigger (workflow_dispatch).** Add a `workflow_dispatch` trigger
  alongside the Issue trigger so a city can be re-run from the Actions tab without
  opening a new issue — useful after a schema change or to recover from a failed
  run. Really "give `backfill.py` a remote trigger too," not a new capability —
  `backfill.py` already does this maintenance job locally. Deferred because the
  Actions-tab form isn't mobile-friendly and this only matters for occasional
  maintenance, not everyday use (raised during /plan-eng-review, 2026-07-22).
