"""Per-lens fit scores (Family Fit / Self-Sufficiency & Resilience),
computed relative to the cities you've actually evaluated -- not against
some invented absolute scale. $430K isn't objectively good or bad, only
relative to your other options, so numeric fields are min-max normalized
across the full set of evaluated cities (compute_field_ranges), not
against a fixed reference range. That means scores shift slightly every
time a new city is added -- a deliberate tradeoff, not a bug.

Pure math over already-fetched data -- no API calls, computed fresh every
time the site builds. Reads preferences.json (weights, dealbreakers,
severity curve, label thresholds) the same way schema.json is read: a
config file, not something baked into code.

Not part of the tested pipeline (fetch/render/lint) -- this is a
deploy-time-only concern like build_pages_site.py, since relative scoring
inherently needs to see every city at once (something a single-city
render_city() call can't do).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from models import CityRecord, FieldStatus
from render import compute_bd_score
from severity import classify_severity

PREFERENCES_PATH = Path(__file__).parent / "preferences.json"


def load_preferences() -> dict:
    with open(PREFERENCES_PATH) as f:
        return json.load(f)


def get_field_value(schema: dict, record: CityRecord, category_key: str, field_key: str):
    """The raw value behind a field, whether it's stored data or (like
    bd_score) computed on the fly -- derived fields never appear in
    record.categories at all, so scoring has to special-case them the
    same way render.py's build_derived_field_context already does."""
    field_def = schema["categories"][category_key]["fields"][field_key]
    if field_def.get("derived"):
        if field_key == "bd_score":
            return compute_bd_score(record.categories.get(category_key, {}))
        return None  # future derived fields: extend here when a second one exists
    stored = record.categories.get(category_key, {}).get(field_key)
    if stored is None or stored.status != FieldStatus.VALID:
        return None
    return stored.value


def compute_field_ranges(schema: dict, records: list[CityRecord]) -> dict[tuple[str, str], tuple[float, float]]:
    """min/max observed for every score_direction-tagged numeric field,
    across all given records -- the basis for relative normalization.
    Skips risk_field/retail_presence fields, which score a different way."""
    ranges = {}
    for category_key, category in schema["categories"].items():
        for field_key, field_def in category["fields"].items():
            if field_def.get("score_direction") is None:
                continue
            if field_def.get("risk_field") or field_def.get("type") == "retail_presence":
                continue
            values = [
                v for r in records
                if isinstance(v := get_field_value(schema, r, category_key, field_key), (int, float))
            ]
            if values:
                ranges[(category_key, field_key)] = (min(values), max(values))
    return ranges


def field_sub_score(
    schema: dict, category_key: str, field_key: str, record: CityRecord,
    field_ranges: dict, risk_severity_scores: dict,
) -> Optional[float]:
    field_def = schema["categories"][category_key]["fields"][field_key]
    value = get_field_value(schema, record, category_key, field_key)
    if value is None:
        return None

    if field_def.get("risk_field"):
        classified = classify_severity(str(value))
        return float(risk_severity_scores[classified["level"]]) if classified else None

    if field_def.get("type") == "retail_presence":
        if not isinstance(value, dict) or "available" not in value:
            return None
        return 100.0 if value["available"] else 0.0

    direction = field_def.get("score_direction")
    if direction is None or not isinstance(value, (int, float)):
        return None
    lo, hi = field_ranges.get((category_key, field_key), (None, None))
    if lo is None:
        return None
    if hi == lo:
        return 50.0  # no variance in the evaluated set yet -- neutral, not a guess either way
    raw = (value - lo) / (hi - lo) * 100 if direction == "higher_better" else (hi - value) / (hi - lo) * 100
    return max(0.0, min(100.0, raw))


def category_score(
    schema: dict, category_key: str, record: CityRecord, field_ranges: dict, risk_severity_scores: dict,
) -> Optional[float]:
    sub_scores = [
        s for field_key in schema["categories"][category_key]["fields"]
        if (s := field_sub_score(schema, category_key, field_key, record, field_ranges, risk_severity_scores)) is not None
    ]
    return sum(sub_scores) / len(sub_scores) if sub_scores else None


def _check_dealbreaker(schema: dict, record: CityRecord, dealbreaker: dict) -> Optional[str]:
    """None = passed. "UNKNOWN" = data not available yet. Otherwise the
    dealbreaker's own description, as the violation reason."""
    value = get_field_value(schema, record, dealbreaker["category"], dealbreaker["field"])
    if value is None:
        return "UNKNOWN"
    threshold = dealbreaker["value"]
    condition = dealbreaker["condition"]
    passed = {
        "gte": value >= threshold, "gt": value > threshold,
        "lte": value <= threshold, "lt": value < threshold,
    }[condition]
    return None if passed else dealbreaker["description"]


def _lens_dealbreaker_status(schema: dict, record: CityRecord, dealbreakers: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """A definite violation wins even if another dealbreaker's data is
    still unknown -- we already know enough to fail it. Only falls back
    to "needs_data" when nothing definite has been found yet."""
    any_unknown = False
    for dealbreaker in dealbreakers:
        result = _check_dealbreaker(schema, record, dealbreaker)
        if result == "UNKNOWN":
            any_unknown = True
        elif result is not None:
            return "dealbreaker", result
    return ("needs_data", None) if any_unknown else (None, None)


def _score_to_label(score: float, label_thresholds: list[dict]) -> str:
    for tier in sorted(label_thresholds, key=lambda t: -t["min"]):
        if score >= tier["min"]:
            return tier["label"]
    return label_thresholds[-1]["label"]


def lens_score(schema: dict, preferences: dict, lens_key: str, record: CityRecord, field_ranges: dict) -> dict:
    lens_prefs = preferences["lenses"][lens_key]
    risk_severity_scores = preferences["risk_severity_scores"]

    status, reason = _lens_dealbreaker_status(schema, record, lens_prefs.get("dealbreakers", []))
    if status == "dealbreaker":
        return {"label": lens_prefs["label"], "score": None, "state": "dealbreaker", "reason": reason}
    if status == "needs_data":
        return {"label": lens_prefs["label"], "score": None, "state": "needs_data", "reason": None}

    weighted_sum = 0.0
    weight_total = 0.0
    for category_key, weight in lens_prefs["category_weights"].items():
        if weight <= 0:
            continue
        cat_score = category_score(schema, category_key, record, field_ranges, risk_severity_scores)
        if cat_score is None:
            continue
        weighted_sum += cat_score * weight
        weight_total += weight

    if weight_total == 0:
        return {"label": lens_prefs["label"], "score": None, "state": "needs_data", "reason": None}

    score = weighted_sum / weight_total
    fit_label = _score_to_label(score, preferences["label_thresholds"])
    return {"label": lens_prefs["label"], "score": score, "state": "scored", "fit_label": fit_label, "reason": None}


def compute_all_scores(schema: dict, records: list[CityRecord]) -> dict[str, dict]:
    """{slug: {lens_key: {label, score, state, fit_label, reason}}} for
    every record -- the single entry point build_pages_site.py needs."""
    preferences = load_preferences()
    field_ranges = compute_field_ranges(schema, records)
    return {
        record.slug: {
            lens_key: lens_score(schema, preferences, lens_key, record, field_ranges)
            for lens_key in preferences["lenses"]
        }
        for record in records
    }
