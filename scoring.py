"""Per-lens fit scores (Family Fit / Self-Sufficiency),
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
from render import compute_bd_score, humanize
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
    dealbreaker's own description plus the actual value that failed it,
    as the violation reason -- "BD score must be at least 2000 (actual:
    150)" is a real answer to "why," not just a restated rule."""
    value = get_field_value(schema, record, dealbreaker["category"], dealbreaker["field"])
    if value is None:
        return "UNKNOWN"
    threshold = dealbreaker["value"]
    condition = dealbreaker["condition"]
    passed = {
        "gte": value >= threshold, "gt": value > threshold,
        "lte": value <= threshold, "lt": value < threshold,
    }[condition]
    if passed:
        return None
    return f"{dealbreaker['description']} (actual: {value:g})"


def _lens_dealbreaker_status(schema: dict, record: CityRecord, dealbreakers: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """A definite violation wins even if another dealbreaker's data is
    still unknown -- we already know enough to fail it. Only falls back
    to "needs_data" when nothing definite has been found yet."""
    unknown_descriptions = []
    for dealbreaker in dealbreakers:
        result = _check_dealbreaker(schema, record, dealbreaker)
        if result == "UNKNOWN":
            unknown_descriptions.append(dealbreaker["description"])
        elif result is not None:
            return "dealbreaker", result
    if unknown_descriptions:
        return "needs_data", "Still waiting on: " + "; ".join(unknown_descriptions)
    return None, None


def _top_pros_cons_item(record: CityRecord, category_key: str, key: str) -> Optional[str]:
    stored = record.categories.get(category_key, {}).get("category_pros_cons")
    if stored is None or stored.status != FieldStatus.VALID or not isinstance(stored.value, dict):
        return None
    items = stored.value.get(key) or []
    return items[0] if items else None


def _build_scored_reason(schema: dict, record: CityRecord, category_scores: dict[str, float]) -> Optional[str]:
    """A 1-sentence 'why' naming the biggest positive and negative factor
    -- reuses category_pros_cons (already fetched, zero new API cost)
    rather than re-explaining the score in the abstract."""
    if not category_scores:
        return None
    best_key = max(category_scores, key=category_scores.get)
    worst_key = min(category_scores, key=category_scores.get)
    best_label = schema["categories"][best_key]["label"]

    if best_key == worst_key:
        return f"Primarily driven by {best_label}."

    worst_label = schema["categories"][worst_key]["label"]
    best_detail = _top_pros_cons_item(record, best_key, "pros")
    worst_detail = _top_pros_cons_item(record, worst_key, "cons")

    best_part = f"{best_label} is a strength" + (f" ({best_detail})" if best_detail else "")
    worst_part = f"{worst_label} is a weak point" + (f" ({worst_detail})" if worst_detail else "")
    return f"{best_part}; {worst_part}."


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
        return {"label": lens_prefs["label"], "score": None, "state": "needs_data", "reason": reason}

    category_scores = {}
    weighted_sum = 0.0
    weight_total = 0.0
    for category_key, weight in lens_prefs["category_weights"].items():
        if weight <= 0:
            continue
        cat_score = category_score(schema, category_key, record, field_ranges, risk_severity_scores)
        if cat_score is None:
            continue
        category_scores[category_key] = cat_score
        weighted_sum += cat_score * weight
        weight_total += weight

    if weight_total == 0:
        return {"label": lens_prefs["label"], "score": None, "state": "needs_data", "reason": "No scorable data yet."}

    score = weighted_sum / weight_total
    fit_label = _score_to_label(score, preferences["label_thresholds"])
    reason = _build_scored_reason(schema, record, category_scores)
    return {"label": lens_prefs["label"], "score": score, "state": "scored", "fit_label": fit_label, "reason": reason}


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


_WEIGHT_TIER_NAMES = {3: "Critical", 2: "Important", 1: "Minor", 0: "Not scored"}


def _unscored_fields(schema: dict) -> list[dict]:
    """Fields that exist and are shown in reports but never contribute to
    any score -- no reliable direction (walkability_score), or a
    free-text legal/regulatory field with no controlled vocabulary to
    classify against (carry_law_type and friends). Derived from schema.json
    directly rather than hardcoded, so this list can never go stale."""
    result = []
    for category_key, category in schema["categories"].items():
        if category_key == "summary":
            continue
        unscored_labels = [
            field_key
            for field_key, field_def in category["fields"].items()
            if not field_def.get("derived") and not field_def.get("synthesized")
            and not field_def.get("risk_field") and field_def.get("type") != "retail_presence"
            and field_def.get("score_direction") is None
        ]
        if unscored_labels:
            result.append({
                "category_label": category["label"],
                "fields": [humanize(key) for key in unscored_labels],
            })
    return result


def scoring_methodology(schema: dict) -> dict:
    """Everything needed to render a human-readable explanation of how
    fit scores are actually computed, straight from preferences.json --
    if you edit your weights later, this page updates itself, nothing to
    keep in sync by hand."""
    preferences = load_preferences()
    lenses = []
    for lens_key, lens_prefs in preferences["lenses"].items():
        categories = [
            {
                "label": schema["categories"][category_key]["label"],
                "weight": weight,
                "tier": _WEIGHT_TIER_NAMES.get(weight, str(weight)),
            }
            for category_key, weight in lens_prefs["category_weights"].items()
        ]
        lenses.append({
            "label": lens_prefs["label"],
            "categories": categories,
            "dealbreakers": [d["description"] for d in lens_prefs.get("dealbreakers", [])],
        })
    return {
        "lenses": lenses,
        "risk_severity_scores": preferences["risk_severity_scores"],
        "label_thresholds": sorted(preferences["label_thresholds"], key=lambda t: -t["min"]),
        "unscored_fields": _unscored_fields(schema),
    }
