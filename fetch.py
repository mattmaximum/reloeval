"""Fetch step: bulk (new/re-run city) and the merge logic backfill relies on.

Standalone script hitting OpenRouter's OpenAI-compatible chat completions
API (not Anthropic directly, and not a Claude Code skill — a slash command
can't natively fire the concurrent per-category calls this design
requires). Needs OPENROUTER_API_KEY set in the environment. Still targets
a Claude model (anthropic/claude-sonnet-5) — only the transport changed.

Bulk mode re-derives what needs fetching from current state every time, so
"backfill" (see backfill.py) is just calling fetch_city_bulk again after a
schema change — no separate fetch implementation, same merge-not-overwrite
logic either way.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI
from pydantic import ValidationError

from atomic_write import atomic_write
from models import (
    CityRecord,
    FieldStatus,
    NormalizedCity,
    StoredFieldValue,
    build_category_response_model,
    build_field_value_model,
    fetchable_fields,
    load_schema,
)

CITIES_DIR = Path(__file__).parent / "cities"
MODEL = "anthropic/claude-sonnet-5"
# Category fetches are search-and-extract, not multi-step reasoning -- a
# cheaper model handles it fine and the web plugin's search results are the
# real cost driver anyway. normalize_city stays on the pricier MODEL since
# it's a single cheap forced tool-call regardless of model.
CATEGORY_MODEL = "anthropic/claude-haiku-4.5"
WEB_PLUGIN_MAX_RESULTS = 2
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class CityNotFoundError(ValueError):
    """Raised when the input city cannot be confidently resolved to a real
    US city. Callers must not write anything when this is raised."""


def slugify(normalized: NormalizedCity) -> str:
    """Lowercase, hyphenated `city-state` — always includes state so cities
    that share a name (e.g. two "Springfield"s) don't collide."""
    city = normalized.city.strip().lower().replace(" ", "-")
    state = normalized.state.strip().lower()
    return f"{city}-{state}"


def needs_fetch(existing: Optional[StoredFieldValue], current_schema_version: int) -> bool:
    """A field needs (re)fetching if it's missing, or valid but behind the
    schema's current version. A `flagged` field is NEVER auto-refetched by
    bulk — only an explicit backfill targeting that exact field corrects it."""
    if existing is None:
        return True
    if existing.status == FieldStatus.FLAGGED:
        return False
    if existing.status == FieldStatus.VALID and existing.schema_version >= current_schema_version:
        return False
    return True


def category_needs_fetch(existing_category: dict[str, StoredFieldValue], field_defs: dict) -> bool:
    return any(
        needs_fetch(existing_category.get(key), field_def["schema_version"])
        for key, field_def in field_defs.items()
    )


async def normalize_city(client: AsyncOpenAI, city_state_input: str) -> NormalizedCity:
    """Resolve raw input (e.g. "NYC" or "Austin, TX") to canonical
    city/state/county, so different spellings of the same city always
    produce the same slug. Raises CityNotFoundError on an unresolvable
    input rather than writing garbage data."""
    tools = [{
        "type": "function",
        "function": {
            "name": "resolve_city",
            "description": (
                "Resolve a US city input to its canonical city, state, and "
                "containing county. If the input cannot be confidently "
                "resolved to a real US city, set 'resolved' to false."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resolved": {"type": "boolean"},
                    "city": {"type": "string"},
                    "state": {"type": "string", "description": "Two-letter state abbreviation"},
                    "county": {"type": "string"},
                },
                "required": ["resolved"],
            },
        },
    }]
    response = await client.chat.completions.create(
        model=MODEL,
        max_tokens=1024,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "resolve_city"}},
        messages=[{
            "role": "user",
            "content": f"Resolve this US city input to canonical city/state/county: {city_state_input!r}",
        }],
    )
    tool_call = response.choices[0].message.tool_calls[0]
    result = json.loads(tool_call.function.arguments)
    if not result.get("resolved"):
        raise CityNotFoundError(
            f"Could not resolve {city_state_input!r} to a known US city. "
            "Check spelling and include a state (e.g. \"Austin, TX\")."
        )
    return NormalizedCity(city=result["city"], state=result["state"], county=result["county"])


async def fetch_category(
    client: AsyncOpenAI,
    schema: dict,
    category_key: str,
    normalized: NormalizedCity,
) -> dict[str, StoredFieldValue]:
    """Fetch every fetchable field in one category via a structured,
    web-search-grounded call. Never raises — a total category failure (API
    error, timeout, malformed response) falls through to marking every
    field in the category unresolved, the same status a single bad field
    gets.

    Grounding uses OpenRouter's own web-search plugin (a separate service
    from Anthropic's native web_search tool — that tool isn't reachable
    through OpenRouter's OpenAI-compatible endpoint) alongside a
    JSON-schema-constrained response, so the model can search and still
    return a schema-conforming final answer.

    Retries once on failure: firing all categories concurrently
    (fetch_city_bulk) has been observed to trip transient errors that the
    same call succeeds at when run alone — a rate limit or timeout under
    concurrent load, not a structural problem with the category's schema.
    """
    field_defs = fetchable_fields(schema, category_key)
    category_label = schema["categories"][category_key]["label"]
    response_model = build_category_response_model(schema, category_key)

    raw: dict = {}
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            response = await client.chat.completions.create(
                model=CATEGORY_MODEL,
                max_tokens=4096,
                extra_body={"plugins": [{"id": "web", "max_results": WEB_PLUGIN_MAX_RESULTS}]},
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "category_data",
                        "schema": response_model.model_json_schema(),
                    },
                },
                messages=[{
                    "role": "user",
                    "content": (
                        f"Research and report the '{category_label}' fields for "
                        f"{normalized.city}, {normalized.state} ({normalized.county}). "
                        "Use web search to find current, accurate information — do "
                        "not answer from memory alone. Every field needs a "
                        "source_url and fetched_date "
                        f"(today is {date.today().isoformat()}) alongside its value."
                    ),
                }],
            )
            raw = json.loads(response.choices[0].message.content)
            last_error = None
            break
        except Exception as e:
            last_error = e
            if attempt == 0:
                await asyncio.sleep(2)
    if last_error is not None:
        # Category-level failure after retry: every field below falls
        # through to unresolved, but say so — silently swallowing this
        # is what made the last two failures take a special diagnostic
        # script to even see.
        print(
            f"WARNING: {category_label} failed after retry for "
            f"{normalized.city}, {normalized.state}: "
            f"{type(last_error).__name__}: {last_error}",
            file=sys.stderr,
        )

    result: dict[str, StoredFieldValue] = {}
    for field_key, field_def in field_defs.items():
        schema_version = field_def["schema_version"]
        raw_field = raw.get(field_key)
        if raw_field is None:
            result[field_key] = StoredFieldValue(status=FieldStatus.UNRESOLVED, schema_version=schema_version)
            continue
        try:
            field_model = build_field_value_model(field_def)
            validated = field_model(**raw_field).model_dump()
            result[field_key] = StoredFieldValue(
                value=validated["value"],
                source_url=validated["source_url"],
                fetched_date=validated["fetched_date"],
                status=FieldStatus.VALID,
                schema_version=schema_version,
            )
        except ValidationError:
            result[field_key] = StoredFieldValue(status=FieldStatus.UNRESOLVED, schema_version=schema_version)
    return result


def load_city_record(slug: str) -> Optional[CityRecord]:
    path = CITIES_DIR / f"{slug}.json"
    if not path.exists():
        return None
    return CityRecord.model_validate_json(path.read_text())


def save_city_record(record: CityRecord) -> Path:
    path = CITIES_DIR / f"{record.slug}.json"
    atomic_write(path, record.model_dump_json(indent=2))
    return path


async def fetch_city_bulk(client: AsyncOpenAI, schema: dict, city_state_input: str) -> CityRecord:
    """Evaluate a city end to end: normalize input, fetch only what's
    missing/stale (merge, never overwrite valid/flagged fields), write.
    Safe to call repeatedly on the same city — a fully up-to-date city
    makes zero API calls."""
    normalized = await normalize_city(client, city_state_input)
    slug = slugify(normalized)
    existing = load_city_record(slug)
    existing_categories = existing.categories if existing else {}

    categories_to_fetch = [
        category_key
        for category_key in schema["categories"]
        if category_needs_fetch(
            existing_categories.get(category_key, {}),
            fetchable_fields(schema, category_key),
        )
    ]

    # No dependency between categories — fetch concurrently, not in a loop.
    fetched_results = await asyncio.gather(*[
        fetch_category(client, schema, category_key, normalized)
        for category_key in categories_to_fetch
    ])
    fetched_by_category = dict(zip(categories_to_fetch, fetched_results))

    merged_categories: dict[str, dict[str, StoredFieldValue]] = {}
    for category_key in schema["categories"]:
        field_defs = fetchable_fields(schema, category_key)
        existing_cat = existing_categories.get(category_key, {})
        new_cat = fetched_by_category.get(category_key, {})
        merged = dict(existing_cat)
        for field_key, field_def in field_defs.items():
            if needs_fetch(existing_cat.get(field_key), field_def["schema_version"]) and field_key in new_cat:
                merged[field_key] = new_cat[field_key]
        merged_categories[category_key] = merged

    record = CityRecord(
        input_city_state=city_state_input,
        normalized=normalized,
        slug=slug,
        categories=merged_categories,
    )
    save_city_record(record)
    return record


async def _main(city_state_input: str) -> CityRecord:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)
    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    schema = load_schema()
    try:
        return await fetch_city_bulk(client, schema, city_state_input)
    except CityNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python fetch.py \"City, ST\"", file=sys.stderr)
        sys.exit(1)
    record = asyncio.run(_main(sys.argv[1]))
    print(f"Wrote cities/{record.slug}.json")
