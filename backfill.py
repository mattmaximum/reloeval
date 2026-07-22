"""Backfill: re-run fetch for every city lint flags as having a gap.

Not a separate fetch implementation — fetch_city_bulk already re-derives
what needs fetching from current state and never touches valid/flagged
fields, so backfilling is just calling it again for each city lint names.
"""
from __future__ import annotations

import asyncio
import os
import sys

from anthropic import AsyncAnthropic

from fetch import fetch_city_bulk
from lint import list_cities, run_lint
from models import load_schema


async def run_backfill() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    schema = load_schema()
    records = list_cities()
    report = run_lint(records)
    if not report:
        print("No gaps found — nothing to backfill.")
        return

    client = AsyncAnthropic(api_key=api_key)
    for slug in report:
        record = next(r for r in records if r.slug == slug)
        print(f"Backfilling {slug}...")
        await fetch_city_bulk(client, schema, record.input_city_state)
    print(f"Backfilled {len(report)} cities.")


if __name__ == "__main__":
    asyncio.run(run_backfill())
