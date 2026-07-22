from models import (
    build_category_response_model,
    build_field_value_model,
    derived_fields,
    fetchable_fields,
    load_schema,
)


def test_schema_loads_and_has_eight_categories():
    schema = load_schema()
    assert len(schema["categories"]) == 8


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
