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
