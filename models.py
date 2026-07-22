"""Pydantic models mirroring schema.json.

schema.json is the single source of truth for field names, types, and
schema_version. This module never hardcodes the ~65 field list — it builds
Pydantic models dynamically from schema.json so the two never drift apart.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Type

from pydantic import BaseModel, Field, create_model

SCHEMA_PATH = Path(__file__).parent / "schema.json"


class FieldStatus(str, Enum):
    VALID = "valid"
    UNRESOLVED = "unresolved"
    FLAGGED = "flagged"


class RetailPresence(BaseModel):
    available: bool
    distance_mi: Optional[float] = None


class ClimateMonthRow(BaseModel):
    month: str
    avg_high_f: float
    avg_low_f: float
    avg_rainfall_in: float
    avg_snowfall_in: float


# Maps a schema.json field "type" to the Python/Pydantic type of its raw value.
_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "number": float,
    "table": list[ClimateMonthRow],
    "retail_presence": RetailPresence,
}


class NormalizedCity(BaseModel):
    city: str
    state: str
    county: str


class StoredFieldValue(BaseModel):
    """The shape a field takes once written into cities/{slug}.json."""
    value: Optional[Any] = None
    source_url: Optional[str] = None
    fetched_date: Optional[str] = None
    status: FieldStatus
    schema_version: int


class CityRecord(BaseModel):
    input_city_state: str
    normalized: NormalizedCity
    slug: str
    categories: dict[str, dict[str, StoredFieldValue]]


def load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def fetchable_fields(schema: dict, category_key: str) -> dict[str, dict]:
    """Fields in a category that should actually be fetched — excludes
    derived fields (e.g. bd_score), which are computed at render time."""
    fields = schema["categories"][category_key]["fields"]
    return {k: v for k, v in fields.items() if not v.get("derived", False)}


def build_field_value_model(field_def: dict) -> Type[BaseModel]:
    """The {value, source_url, fetched_date} shape the LLM must return for
    one field. Reused both to build the category's structured-output schema
    and to re-validate each field individually after the call returns."""
    value_type = _TYPE_MAP[field_def["type"]]
    return create_model(
        "FetchedFieldValue",
        value=(value_type, ...),
        source_url=(str, ...),
        fetched_date=(str, ...),
    )


def build_category_response_model(schema: dict, category_key: str) -> Type[BaseModel]:
    """The full structured-output schema for one category's fetch call —
    passed to Claude's tool-use definition so the model knows the exact
    shape to return. Excludes derived fields."""
    fields = fetchable_fields(schema, category_key)
    field_defs = {
        key: (build_field_value_model(field_def), ...)
        for key, field_def in fields.items()
    }
    return create_model(f"{category_key}_response", **field_defs)


def derived_fields(schema: dict, category_key: str) -> dict[str, dict]:
    fields = schema["categories"][category_key]["fields"]
    return {k: v for k, v in fields.items() if v.get("derived", False)}
