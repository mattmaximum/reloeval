"""Lint step: scan schema.json against every cities/*.json and report gaps.

A "gap" is any field that needs a backfill: missing, unresolved, flagged
(user identified it as wrong via spot-check), or behind the schema's
current schema_version for that field. All four states are backfill
candidates through the same path — this is the mechanism "update all files
easily" resolves to, not a blanket re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

from models import CityRecord, FieldStatus, fetchable_fields, load_schema

CITIES_DIR = Path(__file__).parent / "cities"

GapReason = str  # one of: "missing", "unresolved", "flagged", "stale_version"


def find_gaps(schema: dict, record: CityRecord) -> dict[str, dict[str, GapReason]]:
    """Returns {category_key: {field_key: reason}} for every field that
    needs a backfill on this city. Empty dict if the city is fully valid
    and current."""
    gaps: dict[str, dict[str, GapReason]] = {}
    for category_key, category in schema["categories"].items():
        field_defs = fetchable_fields(schema, category_key)
        existing_cat = record.categories.get(category_key, {})
        category_gaps: dict[str, GapReason] = {}
        for field_key, field_def in field_defs.items():
            current_version = field_def["schema_version"]
            existing = existing_cat.get(field_key)
            if existing is None:
                category_gaps[field_key] = "missing"
            elif existing.status == FieldStatus.UNRESOLVED:
                category_gaps[field_key] = "unresolved"
            elif existing.status == FieldStatus.FLAGGED:
                category_gaps[field_key] = "flagged"
            elif existing.status == FieldStatus.VALID and existing.schema_version < current_version:
                category_gaps[field_key] = "stale_version"
        if category_gaps:
            gaps[category_key] = category_gaps
    return gaps


def list_cities() -> list[CityRecord]:
    if not CITIES_DIR.exists():
        return []
    records = []
    for path in sorted(CITIES_DIR.glob("*.json")):
        records.append(CityRecord.model_validate_json(path.read_text()))
    return records


def run_lint(records: list[CityRecord]) -> dict[str, dict[str, dict[str, GapReason]]]:
    """Returns {slug: {category_key: {field_key: reason}}} for every city
    that has at least one gap. Cities with zero gaps are omitted."""
    schema = load_schema()
    report: dict[str, dict[str, dict[str, GapReason]]] = {}
    for record in records:
        gaps = find_gaps(schema, record)
        if gaps:
            report[record.slug] = gaps
    return report


def print_report(report: dict[str, dict[str, dict[str, GapReason]]], n_cities: int) -> None:
    if n_cities == 0:
        print("No cities evaluated yet.")
        return
    if not report:
        print(f"All {n_cities} cities are fully valid and up to date. No gaps.")
        return
    for slug, categories in report.items():
        print(f"\n{slug}:")
        for category_key, fields in categories.items():
            for field_key, reason in fields.items():
                print(f"  [{reason}] {category_key}.{field_key}")


if __name__ == "__main__":
    records = list_cities()
    report = run_lint(records)
    print_report(report, len(records))
    sys.exit(1 if report else 0)
