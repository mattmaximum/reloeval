"""One-command entrypoint: fetch a city, then render its report.

Success criteria from the design doc: running one command against a city
produces cities/{slug}.json AND a rendered reports/{slug}.md. fetch.py and
render.py stay separate modules (single responsibility, independently
testable) — this is the thin orchestrator that chains them.
"""
from __future__ import annotations

import asyncio
import os
import sys

from openai import AsyncOpenAI

from fetch import OPENROUTER_BASE_URL, CityNotFoundError, fetch_city_bulk
from models import load_schema
from render import render_city


async def evaluate_city(city_state_input: str) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)
    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    schema = load_schema()
    try:
        record = await fetch_city_bulk(client, schema, city_state_input)
    except CityNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    render_city(record.slug)
    return record.slug


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('Usage: python evaluate.py "City, ST"', file=sys.stderr)
        sys.exit(1)
    slug = asyncio.run(evaluate_city(sys.argv[1]))
    print(f"Done — cities/{slug}.json and reports/{slug}.md are ready.")
