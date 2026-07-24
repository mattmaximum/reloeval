from models import CityRecord, FieldStatus, NormalizedCity, StoredFieldValue, load_schema
from scoring import (
    _build_scored_reason,
    _check_dealbreaker,
    _lens_dealbreaker_status,
    _score_to_label,
    _top_pros_cons_item,
    category_score,
    compute_all_scores,
    compute_field_ranges,
    field_sub_score,
    get_field_value,
    lens_score,
    load_preferences,
)


def _record(slug, city, state, categories=None):
    return CityRecord(
        input_city_state=f"{city}, {state}",
        normalized=NormalizedCity(city=city, state=state, county=f"{city} County"),
        slug=slug,
        categories=categories or {},
    )


def test_load_preferences_has_both_lenses():
    prefs = load_preferences()
    assert set(prefs["lenses"].keys()) == {"family", "self_sufficiency"}
    assert prefs["lenses"]["self_sufficiency"]["category_weights"]["education_healthcare"] == 0


def test_get_field_value_computes_derived_bd_score_on_the_fly():
    schema = load_schema()
    record = _record("a", "A", "TX", {"geographic_hazards": {
        "elevation_ft": StoredFieldValue(value=500.0, status=FieldStatus.VALID, schema_version=1),
        "distance_to_ocean_mi": StoredFieldValue(value=200.0, status=FieldStatus.VALID, schema_version=1),
    }})
    assert get_field_value(schema, record, "geographic_hazards", "bd_score") == 700.0


def test_get_field_value_returns_none_for_missing_or_unresolved():
    schema = load_schema()
    record = _record("a", "A", "TX", {"power_energy": {
        "solar_score": StoredFieldValue(status=FieldStatus.UNRESOLVED, schema_version=1),
    }})
    assert get_field_value(schema, record, "power_energy", "solar_score") is None
    assert get_field_value(schema, record, "power_energy", "electricity_rate_cents_per_kwh") is None


def test_compute_field_ranges_skips_risk_and_retail_presence_fields():
    schema = load_schema()
    records = [_record("a", "A", "TX", {"geographic_hazards": {
        "avg_aqi": StoredFieldValue(value=30.0, status=FieldStatus.VALID, schema_version=1),
    }})]
    ranges = compute_field_ranges(schema, records)
    assert ("geographic_hazards", "avg_aqi") in ranges
    assert ("geographic_hazards", "wildfire_risk") not in ranges
    assert ("amenities_food_travel", "retail_costco") not in ranges


def test_field_sub_score_higher_better_normalizes_relative_to_range():
    schema = load_schema()
    record = _record("a", "A", "TX", {"power_energy": {
        "solar_score": StoredFieldValue(value=75.0, status=FieldStatus.VALID, schema_version=1),
    }})
    ranges = {("power_energy", "solar_score"): (50.0, 100.0)}
    score = field_sub_score(schema, "power_energy", "solar_score", record, ranges, {})
    assert score == 50.0  # (75-50)/(100-50)*100


def test_field_sub_score_lower_better_normalizes_relative_to_range():
    schema = load_schema()
    record = _record("a", "A", "TX", {"economy_housing_land": {
        "median_home_price": StoredFieldValue(value=300000.0, status=FieldStatus.VALID, schema_version=1),
    }})
    ranges = {("economy_housing_land", "median_home_price"): (200000.0, 400000.0)}
    score = field_sub_score(schema, "economy_housing_land", "median_home_price", record, ranges, {})
    assert score == 50.0  # (400k-300k)/(400k-200k)*100


def test_field_sub_score_no_variance_returns_neutral_fifty():
    schema = load_schema()
    record = _record("a", "A", "TX", {"power_energy": {
        "solar_score": StoredFieldValue(value=80.0, status=FieldStatus.VALID, schema_version=1),
    }})
    ranges = {("power_energy", "solar_score"): (80.0, 80.0)}
    assert field_sub_score(schema, "power_energy", "solar_score", record, ranges, {}) == 50.0


def test_field_sub_score_risk_field_uses_severity_curve():
    schema = load_schema()
    record = _record("a", "A", "TX", {"geographic_hazards": {
        "wildfire_risk": StoredFieldValue(value="Moderate - some exposure", status=FieldStatus.VALID, schema_version=1),
    }})
    severity_scores = {"low": 90, "moderate": 55, "high": 20}
    score = field_sub_score(schema, "geographic_hazards", "wildfire_risk", record, {}, severity_scores)
    assert score == 55.0


def test_field_sub_score_retail_presence_is_binary():
    schema = load_schema()
    available = _record("a", "A", "TX", {"amenities_food_travel": {
        "retail_costco": StoredFieldValue(value={"available": True, "distance_mi": 3.0}, status=FieldStatus.VALID, schema_version=1),
    }})
    unavailable = _record("b", "B", "TX", {"amenities_food_travel": {
        "retail_costco": StoredFieldValue(value={"available": False}, status=FieldStatus.VALID, schema_version=1),
    }})
    assert field_sub_score(schema, "amenities_food_travel", "retail_costco", available, {}, {}) == 100.0
    assert field_sub_score(schema, "amenities_food_travel", "retail_costco", unavailable, {}, {}) == 0.0


def test_field_sub_score_none_for_unscorable_field():
    schema = load_schema()
    record = _record("a", "A", "TX", {"geographic_hazards": {
        "volcano_proximity": StoredFieldValue(value="Far away", status=FieldStatus.VALID, schema_version=1),
    }})
    assert field_sub_score(schema, "geographic_hazards", "volcano_proximity", record, {}, {}) is None


def test_category_score_averages_available_sub_scores_only():
    schema = load_schema()
    record = _record("a", "A", "TX", {"power_energy": {
        "solar_score": StoredFieldValue(value=100.0, status=FieldStatus.VALID, schema_version=1),
        "electricity_rate_cents_per_kwh": StoredFieldValue(value=10.0, status=FieldStatus.VALID, schema_version=1),
    }})
    ranges = {
        ("power_energy", "solar_score"): (0.0, 100.0),
        ("power_energy", "electricity_rate_cents_per_kwh"): (10.0, 20.0),
    }
    score = category_score(schema, "power_energy", record, ranges, {})
    # solar_score sub-score = 100, electricity sub-score (lower_better, at the low end) = 100
    assert score == 100.0


def test_category_score_none_when_nothing_scorable():
    schema = load_schema()
    record = _record("a", "A", "TX", {})
    assert category_score(schema, "power_energy", record, {}, {}) is None


def test_check_dealbreaker_passes_when_condition_satisfied():
    schema = load_schema()
    record = _record("a", "A", "TX", {"geographic_hazards": {
        "elevation_ft": StoredFieldValue(value=500.0, status=FieldStatus.VALID, schema_version=1),
        "distance_to_ocean_mi": StoredFieldValue(value=1000.0, status=FieldStatus.VALID, schema_version=1),
    }})
    # bd_score = 500 + 1000 = 1500
    db = {"description": "x", "category": "geographic_hazards", "field": "bd_score", "condition": "gte", "value": 1000}
    assert _check_dealbreaker(schema, record, db) is None  # 1500 >= 1000 passes


def test_check_dealbreaker_violation_returns_description():
    schema = load_schema()
    record = _record("a", "A", "TX", {"geographic_hazards": {
        "elevation_ft": StoredFieldValue(value=100.0, status=FieldStatus.VALID, schema_version=1),
        "distance_to_ocean_mi": StoredFieldValue(value=50.0, status=FieldStatus.VALID, schema_version=1),
    }})
    db = {"description": "BD score must be at least 2000", "category": "geographic_hazards", "field": "bd_score", "condition": "gte", "value": 2000}
    assert _check_dealbreaker(schema, record, db) == "BD score must be at least 2000 (actual: 150)"


def test_check_dealbreaker_unknown_when_data_missing():
    schema = load_schema()
    record = _record("a", "A", "TX", {})
    db = {"category": "geographic_hazards", "field": "nearest_large_metro_drive_time_min", "condition": "gte", "value": 60}
    assert _check_dealbreaker(schema, record, db) == "UNKNOWN"


def test_lens_dealbreaker_status_definite_violation_wins_over_unknown():
    schema = load_schema()
    record = _record("a", "A", "TX", {"geographic_hazards": {
        "elevation_ft": StoredFieldValue(value=100.0, status=FieldStatus.VALID, schema_version=1),
        "distance_to_ocean_mi": StoredFieldValue(value=50.0, status=FieldStatus.VALID, schema_version=1),
        # nearest_large_metro_drive_time_min left missing -> UNKNOWN for that one
    }})
    dealbreakers = [
        {"description": "BD too low", "category": "geographic_hazards", "field": "bd_score", "condition": "gte", "value": 2000},
        {"description": "Too close to a metro", "category": "geographic_hazards", "field": "nearest_large_metro_drive_time_min", "condition": "gte", "value": 60},
    ]
    status, reason = _lens_dealbreaker_status(schema, record, dealbreakers)
    assert status == "dealbreaker"
    assert reason == "BD too low (actual: 150)"


def test_lens_dealbreaker_status_needs_data_when_only_unknown():
    schema = load_schema()
    record = _record("a", "A", "TX", {})
    dealbreakers = [
        {"description": "BD too low", "category": "geographic_hazards", "field": "bd_score", "condition": "gte", "value": 2000},
    ]
    status, reason = _lens_dealbreaker_status(schema, record, dealbreakers)
    assert status == "needs_data"
    assert reason == "Still waiting on: BD too low"


def test_score_to_label_picks_correct_tier():
    thresholds = [{"min": 75, "label": "Strong Fit"}, {"min": 50, "label": "Good Fit"}, {"min": 0, "label": "Weak Fit"}]
    assert _score_to_label(80, thresholds) == "Strong Fit"
    assert _score_to_label(60, thresholds) == "Good Fit"
    assert _score_to_label(10, thresholds) == "Weak Fit"
    assert _score_to_label(75, thresholds) == "Strong Fit"  # boundary is inclusive


def test_lens_score_family_lens_has_no_dealbreakers_and_scores_normally():
    schema = load_schema()
    prefs = load_preferences()
    record = _record("a", "A", "TX", {"education_healthcare": {
        "district_rating": StoredFieldValue(value=9.0, status=FieldStatus.VALID, schema_version=1),
    }})
    ranges = {("education_healthcare", "district_rating"): (5.0, 9.0)}
    result = lens_score(schema, prefs, "family", record, ranges)
    assert result["state"] == "scored"
    assert result["fit_label"] == "Strong Fit"  # only scorable field is at the top of its range


def test_lens_score_self_sufficiency_triggers_dealbreaker():
    schema = load_schema()
    prefs = load_preferences()
    record = _record("a", "A", "TX", {"geographic_hazards": {
        "elevation_ft": StoredFieldValue(value=100.0, status=FieldStatus.VALID, schema_version=1),
        "distance_to_ocean_mi": StoredFieldValue(value=50.0, status=FieldStatus.VALID, schema_version=1),
        "nearest_large_metro_drive_time_min": StoredFieldValue(value=90.0, status=FieldStatus.VALID, schema_version=1),
    }})
    result = lens_score(schema, prefs, "self_sufficiency", record, {})
    assert result["state"] == "dealbreaker"
    assert "BD score" in result["reason"]


def test_lens_score_self_sufficiency_needs_data_when_dealbreaker_fields_missing():
    schema = load_schema()
    prefs = load_preferences()
    record = _record("a", "A", "TX", {})
    result = lens_score(schema, prefs, "self_sufficiency", record, {})
    assert result["state"] == "needs_data"


def test_compute_all_scores_end_to_end_across_multiple_cities():
    schema = load_schema()
    cheap = _record("cheap", "Cheap Town", "TX", {
        "geographic_hazards": {
            "elevation_ft": StoredFieldValue(value=3000.0, status=FieldStatus.VALID, schema_version=1),
            "distance_to_ocean_mi": StoredFieldValue(value=500.0, status=FieldStatus.VALID, schema_version=1),
            "nearest_large_metro_drive_time_min": StoredFieldValue(value=120.0, status=FieldStatus.VALID, schema_version=1),
        },
        "economy_housing_land": {
            "median_home_price": StoredFieldValue(value=150000.0, status=FieldStatus.VALID, schema_version=1),
        },
    })
    pricey = _record("pricey", "Pricey Town", "TX", {
        "geographic_hazards": {
            "elevation_ft": StoredFieldValue(value=3000.0, status=FieldStatus.VALID, schema_version=1),
            "distance_to_ocean_mi": StoredFieldValue(value=500.0, status=FieldStatus.VALID, schema_version=1),
            "nearest_large_metro_drive_time_min": StoredFieldValue(value=120.0, status=FieldStatus.VALID, schema_version=1),
        },
        "economy_housing_land": {
            "median_home_price": StoredFieldValue(value=800000.0, status=FieldStatus.VALID, schema_version=1),
        },
    })
    results = compute_all_scores(schema, [cheap, pricey])
    assert set(results.keys()) == {"cheap", "pricey"}
    # cheap town's home price should score higher (cheaper is better) than pricey town's
    cheap_econ = results["cheap"]["family"]["score"]
    pricey_econ = results["pricey"]["family"]["score"]
    assert cheap_econ > pricey_econ


def test_top_pros_cons_item_returns_first_item():
    record = _record("a", "A", "TX", {"power_energy": {
        "category_pros_cons": StoredFieldValue(
            value={"pros": ["cheap electricity", "reliable grid"], "cons": []},
            status=FieldStatus.VALID, schema_version=1),
    }})
    assert _top_pros_cons_item(record, "power_energy", "pros") == "cheap electricity"
    assert _top_pros_cons_item(record, "power_energy", "cons") is None


def test_top_pros_cons_item_none_when_missing():
    record = _record("a", "A", "TX", {})
    assert _top_pros_cons_item(record, "power_energy", "pros") is None


def test_build_scored_reason_names_best_and_worst_category_with_detail():
    schema = load_schema()
    record = _record("a", "A", "TX", {
        "power_energy": {
            "category_pros_cons": StoredFieldValue(
                value={"pros": ["cheap electricity"], "cons": []},
                status=FieldStatus.VALID, schema_version=1),
        },
        "water_supply": {
            "category_pros_cons": StoredFieldValue(
                value={"pros": [], "cons": ["elevated drought risk"]},
                status=FieldStatus.VALID, schema_version=1),
        },
    })
    category_scores = {"power_energy": 90.0, "water_supply": 20.0}
    reason = _build_scored_reason(schema, record, category_scores)
    assert "Power, Energy & Grid Infrastructure is a strength (cheap electricity)" in reason
    assert "Water Supply & Security is a weak point (elevated drought risk)" in reason


def test_build_scored_reason_single_category_has_no_contrast():
    schema = load_schema()
    record = _record("a", "A", "TX", {})
    reason = _build_scored_reason(schema, record, {"power_energy": 75.0})
    assert reason == "Primarily driven by Power, Energy & Grid Infrastructure."


def test_build_scored_reason_none_when_no_categories_scored():
    schema = load_schema()
    record = _record("a", "A", "TX", {})
    assert _build_scored_reason(schema, record, {}) is None


def test_lens_score_scored_state_includes_reason():
    schema = load_schema()
    prefs = load_preferences()
    record = _record("a", "A", "TX", {
        "education_healthcare": {
            "district_rating": StoredFieldValue(value=9.0, status=FieldStatus.VALID, schema_version=1),
            "category_pros_cons": StoredFieldValue(
                value={"pros": ["top-rated district"], "cons": []},
                status=FieldStatus.VALID, schema_version=1),
        },
        "power_energy": {
            "solar_score": StoredFieldValue(value=10.0, status=FieldStatus.VALID, schema_version=1),
            "category_pros_cons": StoredFieldValue(
                value={"pros": [], "cons": ["poor solar potential"]},
                status=FieldStatus.VALID, schema_version=1),
        },
    })
    ranges = {
        ("education_healthcare", "district_rating"): (5.0, 9.0),
        ("power_energy", "solar_score"): (10.0, 90.0),
    }
    result = lens_score(schema, prefs, "family", record, ranges)
    assert result["state"] == "scored"
    assert "top-rated district" in result["reason"]
