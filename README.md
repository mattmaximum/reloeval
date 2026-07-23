# Relocation Evaluator

**This is a personal project**, built to help research US cities for a real
family relocation decision. It's not a product, not open for contributions,
and not something you can use against your own data — see "How to request a
city" below for exactly who can trigger it.

**Browse evaluated cities:** https://mattmaximum.github.io/reloeval/

## What it does

For any US city, this researches and reports on ~65 fields across 8
categories, using an LLM with live web search for grounding (not just
model memory):

- **Geographic & Natural Hazards** — elevation, distance to ocean, fault
  lines, wildfire/earthquake/flood/hurricane risk, air quality
- **Climate & Growing Conditions** — USDA hardiness zone, frost dates,
  growing season, a full monthly temperature/precipitation table
- **Water Supply & Security** — water source, drought risk, well legality,
  water rights
- **Power, Energy & Grid Infrastructure** — electricity rates, grid
  reliability, solar potential
- **Civic, Demographic & Legal Profile** — population, crime rates, safety
  index, gun/self-defense law
- **Education & Healthcare** — school ratings, homeschool regulation,
  nearest trauma center
- **Economy, Housing & Land** — home prices, land cost, taxes, permitting,
  walkability
- **Amenities, Food & Travel** — specific grocery chains, farmers markets,
  raw dairy legality, nearby airports

Every field carries a source citation and fetch date where applicable.
Fields that can't be confidently resolved are marked as such rather than
guessed at.

## How to request a city

1. Open a new issue using the **["New city request"](https://github.com/mattmaximum/reloeval/issues/new/choose)**
   template.
2. Set the title to `City, ST` (e.g. `Boise, ID`) — that's the only input
   that matters; the issue body is ignored.
3. Submit.

**Only the repo owner can actually trigger a run** — the workflow checks
that the issue author matches the repo owner before doing anything. Anyone
else opening an issue (even using the template) is silently inert: no run,
no cost, no notification beyond GitHub's normal "someone opened an issue"
behavior.

## How it works

```
Issue opened ("City, ST" + evaluate label)
        │
        ▼
GitHub Actions: owner + label gate
        │
        ▼
Fetch (LLM + web search, per category) → gap-check → render
        │
        ▼
Rebuild site → commit → deploy to GitHub Pages
        │
        ▼
Comment on the issue with the live link → close it
```

Everything is hosted on GitHub — no server, no database, no credentials
outside a single repo secret (`OPENROUTER_API_KEY`). Data is cached per
city; re-requesting an already-evaluated city costs nothing unless the
underlying field set has changed. A separate manual trigger
(**Actions → "Backfill & redeploy"**, owner-only by GitHub's own
permissions) re-checks every past city for new/stale fields and
redeploys the site — useful after a schema or design change, not needed
for everyday use.
