"""Fetch step: bulk (new/re-run city) and the merge logic backfill relies on.

Standalone script hitting the Anthropic API directly (not a Claude Code
skill) — a slash command can't natively fire the concurrent per-category
calls this design requires. Needs ANTHROPIC_API_KEY set in the environment.

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

from anthropic import AsyncAnthropic
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
MODEL = "claude-sonnet-5"


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


async def normalize_city(client: AsyncAnthropic, city_state_input: str) -> NormalizedCity:
    """Resolve raw input (e.g. "NYC" or "Austin, TX") to canonical
    city/state/county, so different spellings of the same city always
    produce the same slug. Raises CityNotFoundError on an unresolvable
    input rather than writing garbage data."""
    tool = {
        "name": "resolve_city",
        "description": (
            "Resolve a US city input to its canonical city, state, and "
            "containing county. If the input cannot be confidently "
            "resolved to a real US city, set 'resolved' to false."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resolved": {"type": "boolean"},
                "city": {"type": "string"},
                "state": {"type": "string", "description": "Two-letter state abbreviation"},
                "county": {"type": "string"},
            },
            "required": ["resolved"],
        },
    }
    response = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[tool],
        tool_choice={"type": "tool", "name": "resolve_city"},
        messages=[{
            "role": "user",
            "content": f"Resolve this US city input to canonical city/state/county: {city_state_input!r}",
        }],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    result = tool_use.input
    if not result.get("resolved"):
        raise CityNotFoundError(
            f"Could not resolve {city_state_input!r} to a known US city. "
            "Check spelling and include a state (e.g. \"Austin, TX\")."
        )
    return NormalizedCity(city=result["city"], state=result["state"], county=result["county"])


async def fetch_category(
    client: AsyncAnthropic,
    schema: dict,
    category_key: str,
    normalized: NormalizedCity,
) -> dict[str, StoredFieldValue]:
    """Fetch every fetchable field in one category via a structured,
    web-search-grounded call. Never raises — a total category failure (API
    error, timeout, malformed response) falls through to marking every
    field in the category unresolved, the same status a single bad field
    gets.

    Uses output_config.format (not a forced tool_choice) so Claude can call
    web_search first and still return a schema-conforming final answer —
    forcing tool_choice to a specific tool would make Claude call it
    immediately, with no chance to search beforehand.
    """
    field_defs = fetchable_fields(schema, category_key)
    category_label = schema["categories"][category_key]["label"]
    response_model = build_category_response_model(schema, category_key)

    raw: dict = {}
    try:
        messages = [{
            "role": "user",
            "content": (
                f"Research and report the '{category_label}' fields for "
                f"{normalized.city}, {normalized.state} ({normalized.county}). "
                "Use web search to find current, accurate information — do "
                "not answer from memory alone. Every field needs a "
                "source_url and fetched_date "
                f"(today is {date.today().isoformat()}) alongside its value."
            ),
        }]
        request_kwargs = dict(
            model=MODEL,
            max_tokens=4096,
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            output_config={
                "format": {"type": "json_schema", "schema": response_model.model_json_schema()},
            },
        )
        response = await client.messages.create(messages=messages, **request_kwargs)
        # Server-side web_search runs its own internal loop (max 10 steps);
        # if it hits that cap mid-research, resume once by resending the
        # conversation so far rather than losing the category's progress.
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            response = await client.messages.create(messages=messages, **request_kwargs)
        text = next(b.text for b in response.content if b.type == "text")
        raw = json.loads(text)
    except Exception:
        pass  # category-level failure: every field below falls through to unresolved

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


async def fetch_city_bulk(client: AsyncAnthropic, schema: dict, city_state_input: str) -> CityRecord:
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
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)
    client = AsyncAnthropic(api_key=api_key)
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
