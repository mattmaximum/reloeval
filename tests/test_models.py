from models import (
    build_category_response_model,
    build_field_value_model,
    derived_fields,
    fetchable_fields,
    load_schema,
    synthesized_fields,
    web_search_fields,
)


def test_schema_loads_and_has_nine_categories():
    schema = load_schema()
    assert len(schema["categories"]) == 9  # 8 research categories + the "summary" pseudo-category


def test_derived_field_excluded_from_fetchable_and_response_model():
    schema = load_schema()
    fetchable = fetchable_fields(schema, "geographic_hazards")
    assert "bd_score" not in fetchable
    assert "elevation_ft" in fetchable

    derived = derived_fields(schema, "geographic_hazards")
    assert "bd_score" in derived

    model = build_category_response_model(schema, "geographic_hazards")
    assert "bd_score" not in model.model_fields
    assert "elevation_ft" in model.model_fields


def test_synthesized_field_included_in_fetchable_but_excluded_from_web_search():
    schema = load_schema()
    # fetchable_fields drives gap-tracking -- a missing summary must count
    # as a real gap, so it stays included here.
    fetchable = fetchable_fields(schema, "geographic_hazards")
    assert "category_summary" in fetchable

    synthesized = synthesized_fields(schema, "geographic_hazards")
    assert "category_summary" in synthesized
    assert "elevation_ft" not in synthesized

    # web_search_fields drives what's actually sent to the web-search fetch
    # call -- a summary can't be requested there, it needs the category's
    # OTHER fields already resolved first.
    web_fields = web_search_fields(schema, "geographic_hazards")
    assert "category_summary" not in web_fields
    assert "elevation_ft" in web_fields

    model = build_category_response_model(schema, "geographic_hazards")
    assert "category_summary" not in model.model_fields


def test_summary_pseudo_category_has_only_overall_summary():
    schema = load_schema()
    assert set(schema["categories"]["summary"]["fields"].keys()) == {"overall_summary"}
    assert schema["categories"]["summary"]["fields"]["overall_summary"]["synthesized"] is True


def test_category_pros_cons_field_present_and_synthesized():
    schema = load_schema()
    for category_key, category in schema["categories"].items():
        if category_key == "summary":
            continue
        assert "category_pros_cons" in category["fields"], category_key
        assert category["fields"]["category_pros_cons"]["synthesized"] is True


def test_pros_cons_model_validates():
    import pytest
    from pydantic import ValidationError
    from models import ProsCons

    instance = ProsCons(pros=["cheap electricity"], cons=["moderate flood risk"])
    assert instance.pros == ["cheap electricity"]

    with pytest.raises(ValidationError):
        ProsCons(pros="not a list", cons=[])


def test_field_value_model_validates_correct_shape():
    field_def = {"type": "number", "schema_version": 1}
    model = build_field_value_model(field_def)
    instance = model(value=489.0, source_url="https://x.com", fetched_date="2026-07-22")
    assert instance.value == 489.0


def test_field_value_model_rejects_wrong_type():
    import pytest
    from pydantic import ValidationError

    field_def = {"type": "number", "schema_version": 1}
    model = build_field_value_model(field_def)
    with pytest.raises(ValidationError):
        model(value="not a number", source_url="https://x.com", fetched_date="2026-07-22")


def test_retail_presence_field_type():
    field_def = {"type": "retail_presence", "schema_version": 1}
    model = build_field_value_model(field_def)
    instance = model(
        value={"available": True, "distance_mi": 4.2},
        source_url="https://x.com",
        fetched_date="2026-07-22",
    )
    assert instance.value.available is True
    assert instance.value.distance_mi == 4.2


def test_table_field_type():
    field_def = {"type": "table", "schema_version": 1}
    model = build_field_value_model(field_def)
    instance = model(
        value=[{"month": "Jan", "avg_high_f": 62.0, "avg_low_f": 41.0, "avg_rainfall_in": 2.5, "avg_snowfall_in": 0.0}],
        source_url="https://noaa.gov",
        fetched_date="2026-07-22",
    )
    assert instance.value[0].month == "Jan"
