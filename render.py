"""Render step: turn a city's JSON into reports/{slug}.md via Jinja2.

All logic (formatting, placeholder text, derived-field computation) happens
here in Python — the template just prints what it's given, no branching
logic buried in Jinja.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader

from atomic_write import atomic_write
from models import CityRecord, FieldStatus, StoredFieldValue, load_schema

REPORTS_DIR = Path(__file__).parent / "reports"
TEMPLATES_DIR = Path(__file__).parent / "templates"
CITIES_DIR = Path(__file__).parent / "cities"

_UNIT_SUFFIXES = [
    ("_ft", " (ft)"),
    ("_mi", " (mi)"),
    ("_in", " (in)"),
    ("_f", " (°F)"),
]

_ACRONYMS = {"Bd": "BD", "Hoa": "HOA", "Aqi": "AQI", "Usda": "USDA", "2Hr": "2hr", "6Hr": "6hr"}

PLACEHOLDER_TEXT = {
    "missing": "_(not yet evaluated — run backfill)_",
    "unresolved": "_(not yet evaluated — run backfill)_",
    "flagged": "_(flagged as incorrect — pending recheck)_",
}


def humanize(field_key: str) -> str:
    label = field_key
    suffix_note = ""
    for suf, note in _UNIT_SUFFIXES:
        if label.endswith(suf):
            label = label[: -len(suf)]
            suffix_note = note
            break
    words = [_ACRONYMS.get(w, w) for w in label.replace("_", " ").title().split(" ")]
    return " ".join(words) + suffix_note


def format_number(value: float) -> str:
    """Thousands-separated, trailing-zero-free. Deliberately not `{:g}` --
    that switches to scientific notation above ~1M (a real risk here:
    county_population routinely exceeds it), which is unreadable."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def format_scalar(value: Any, unit: Optional[str] = None) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        formatted = format_number(value)
        if unit == "currency":
            return f"${formatted}"
        if unit == "percent":
            return f"{formatted}%"
        return formatted
    return str(value)


def md_table_cell(text: str) -> str:
    """Escape a string for safe use inside a markdown table cell — a
    literal `|` or embedded newline in an LLM-generated field would
    otherwise break the row structure."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def format_retail_presence(value: dict) -> str:
    if not value.get("available"):
        return "No"
    distance = value.get("distance_mi")
    return f"Yes ({distance:g} mi away)" if distance is not None else "Yes"


def citation_text(field: StoredFieldValue) -> Optional[str]:
    if field.status != FieldStatus.VALID or not field.source_url:
        return None
    return f"[source]({field.source_url}), checked {field.fetched_date}"


def compute_bd_score(category_fields: dict[str, StoredFieldValue]) -> Optional[float]:
    """The one derived field in v1: elevation_ft + distance_to_ocean_mi.
    Computed here from already-stored raw fields, never fetched. If either
    input isn't valid yet, bd_score isn't available either."""
    elevation = category_fields.get("elevation_ft")
    distance = category_fields.get("distance_to_ocean_mi")
    if (
        elevation is None or elevation.status != FieldStatus.VALID
        or distance is None or distance.status != FieldStatus.VALID
    ):
        return None
    return elevation.value + distance.value


def bd_score_emoji(value: float) -> str:
    """Fixed absolute thresholds (not relative to other cities, unlike the
    scoring.py sub-scores) -- a personal quick-glance cue, plain text so it
    shows up in reports/*.md too, not just the HTML pages."""
    if value < 2000:
        return "\U0001F534"  # red circle
    if value < 3000:
        return "\U0001F7E0"  # orange circle
    if value < 5000:
        return "\U0001F7E1"  # yellow circle
    return "\U0001F7E2"  # green circle


def build_field_context(field_key: str, field_def: dict, stored: Optional[StoredFieldValue]) -> dict:
    label = humanize(field_key)
    highlight = field_def.get("highlight", False)
    risk_field = field_def.get("risk_field", False)
    description = field_def.get("description")

    if stored is None:
        return {"key": field_key, "label": label, "type": field_def["type"], "is_table": False,
                "status": "missing", "highlight": highlight, "risk_field": risk_field, "description": description,
                "display_value": PLACEHOLDER_TEXT["missing"], "citation": None,
                "citation_url": None, "citation_date": None, "caveat": None}

    if stored.status != FieldStatus.VALID:
        return {"key": field_key, "label": label, "type": field_def["type"], "is_table": False,
                "status": stored.status.value, "highlight": highlight, "risk_field": risk_field, "description": description,
                "display_value": PLACEHOLDER_TEXT[stored.status.value], "citation": None,
                "citation_url": None, "citation_date": None, "caveat": None}

    field_type = field_def["type"]
    if field_type == "table":
        display_value = stored.value  # list[dict] — template renders it as a table
        is_table = True
    elif field_type == "retail_presence":
        display_value = format_retail_presence(stored.value)
        is_table = False
    else:
        display_value = format_scalar(stored.value, field_def.get("unit"))
        is_table = False

    caveat = field_def.get("caveat") if field_def.get("low_confidence") else None
    return {
        "key": field_key, "label": label, "type": field_type, "is_table": is_table,
        "status": "valid", "highlight": highlight, "risk_field": risk_field, "description": description,
        "display_value": display_value, "citation": citation_text(stored), "caveat": caveat,
        "citation_url": stored.source_url, "citation_date": stored.fetched_date,
    }


def build_derived_field_context(field_key: str, field_def: dict, category_fields: dict[str, StoredFieldValue]) -> dict:
    label = humanize(field_key)
    if field_key == "bd_score":
        value = compute_bd_score(category_fields)
    else:
        value = None  # future derived fields: extend here when a second one exists

    if value is None:
        display_value = PLACEHOLDER_TEXT["missing"]
        status = "missing"
    else:
        display_value = format_scalar(value, field_def.get("unit"))
        if field_key == "bd_score":
            display_value = f"{bd_score_emoji(value)} {format_number(round(value))}"
        status = "valid"
    return {"key": field_key, "label": label, "type": "number", "is_table": False,
            "status": status, "highlight": field_def.get("highlight", False), "risk_field": False,
            "description": field_def.get("description"),
            "display_value": display_value, "value": value, "citation": None,
            "citation_url": None, "citation_date": None, "caveat": None}


def build_render_context(schema: dict, record: CityRecord) -> dict:
    """Synthesized fields (category_summary, overall_summary) are pulled
    out of the normal field list here -- they're prose, not a fact to show
    as a table row, and the "summary" pseudo-category has no fields worth
    its own section, just the one overall_summary value surfaced at the
    top of the report instead."""
    categories = []
    overall_summary = None
    for category_key, category in schema["categories"].items():
        stored_fields = record.categories.get(category_key, {})

        if category_key == "summary":
            for field_key, field_def in category["fields"].items():
                if field_def.get("synthesized"):
                    stored = stored_fields.get(field_key)
                    if stored and stored.status == FieldStatus.VALID:
                        overall_summary = stored.value
            continue

        field_contexts = []
        category_summary = None
        pros_cons = None
        for field_key, field_def in category["fields"].items():
            if field_def.get("synthesized"):
                stored = stored_fields.get(field_key)
                if stored and stored.status == FieldStatus.VALID:
                    if field_key == "category_summary":
                        category_summary = stored.value
                    elif field_key == "category_pros_cons":
                        pros_cons = stored.value
                continue
            if field_def.get("derived"):
                field_contexts.append(build_derived_field_context(field_key, field_def, stored_fields))
            else:
                field_contexts.append(build_field_context(field_key, field_def, stored_fields.get(field_key)))
        categories.append({
            "key": category_key, "label": category["label"],
            "summary": category_summary, "pros_cons": pros_cons, "fields": field_contexts,
        })
    return {
        "normalized": record.normalized,
        "generated_date": date.today().isoformat(),
        "categories": categories,
        "overall_summary": overall_summary,
    }


def render_city(slug: str) -> Path:
    schema = load_schema()
    path = CITIES_DIR / f"{slug}.json"
    record = CityRecord.model_validate_json(path.read_text())
    context = build_render_context(schema, record)

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), trim_blocks=True, lstrip_blocks=True)
    env.filters["md_cell"] = md_table_cell
    template = env.get_template("report.md.j2")
    markdown = template.render(**context)

    out_path = REPORTS_DIR / f"{slug}.md"
    atomic_write(out_path, markdown)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python render.py <slug>", file=sys.stderr)
        sys.exit(1)
    out_path = render_city(sys.argv[1])
    print(f"Wrote {out_path}")
