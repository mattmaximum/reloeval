from models import CityRecord, FieldStatus, NormalizedCity, StoredFieldValue, load_schema
from render import build_render_context, compute_bd_score, format_retail_presence, humanize, md_table_cell, render_city


def test_md_table_cell_escapes_pipes_and_newlines():
    assert md_table_cell("Moderate | High\nrisk") == "Moderate \\| High risk"


def test_build_render_context_category_carries_schema_key():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={},
    )
    context = build_render_context(schema, record)
    assert context["categories"][0]["key"] == "geographic_hazards"


def test_build_render_context_field_status_reflects_missing_unresolved_flagged_valid():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={"power_energy": {
            "solar_score": StoredFieldValue(status=FieldStatus.UNRESOLVED, schema_version=1),
            "grid_reliability": StoredFieldValue(status=FieldStatus.FLAGGED, schema_version=1),
            "electricity_rate_cents_per_kwh": StoredFieldValue(
                value=12.5, source_url="https://x.com", fetched_date="2026-07-22",
                status=FieldStatus.VALID, schema_version=1),
        }},
    )
    context = build_render_context(schema, record)
    power = next(c for c in context["categories"] if c["key"] == "power_energy")
    by_key = {f["key"]: f for f in power["fields"]}
    assert by_key["net_metering_policy"]["status"] == "missing"
    assert by_key["solar_score"]["status"] == "unresolved"
    assert by_key["grid_reliability"]["status"] == "flagged"
    assert by_key["electricity_rate_cents_per_kwh"]["status"] == "valid"
    assert by_key["electricity_rate_cents_per_kwh"]["citation_url"] == "https://x.com"
    assert by_key["electricity_rate_cents_per_kwh"]["citation_date"] == "2026-07-22"


def test_build_render_context_highlight_and_risk_field_flags_from_schema():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={},
    )
    context = build_render_context(schema, record)
    hazards = next(c for c in context["categories"] if c["key"] == "geographic_hazards")
    by_key = {f["key"]: f for f in hazards["fields"]}
    assert by_key["bd_score"]["highlight"] is True
    assert by_key["wildfire_risk"]["risk_field"] is True
    assert by_key["county"]["highlight"] is False
    assert by_key["county"]["risk_field"] is False


def test_humanize_applies_unit_suffix_and_acronym():
    assert humanize("elevation_ft") == "Elevation (ft)"
    assert humanize("hoa_prevalence") == "HOA Prevalence"


def test_compute_bd_score_sums_elevation_and_distance():
    fields = {
        "elevation_ft": StoredFieldValue(value=489.0, status=FieldStatus.VALID, schema_version=1),
        "distance_to_ocean_mi": StoredFieldValue(value=150.0, status=FieldStatus.VALID, schema_version=1),
    }
    assert compute_bd_score(fields) == 639.0


def test_compute_bd_score_unavailable_if_one_input_missing():
    fields = {
        "elevation_ft": StoredFieldValue(value=489.0, status=FieldStatus.VALID, schema_version=1),
        "distance_to_ocean_mi": StoredFieldValue(status=FieldStatus.UNRESOLVED, schema_version=1),
    }
    assert compute_bd_score(fields) is None


def test_format_retail_presence_available_with_distance():
    assert format_retail_presence({"available": True, "distance_mi": 4.2}) == "Yes (4.2 mi away)"


def test_format_retail_presence_not_available():
    assert format_retail_presence({"available": False}) == "No"


def test_build_render_context_missing_field_gets_placeholder():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={},
    )
    context = build_render_context(schema, record)
    power_category = next(c for c in context["categories"] if c["label"] == "Power, Energy & Grid Infrastructure")
    field = next(f for f in power_category["fields"] if f["key"] == "electricity_rate_cents_per_kwh")
    assert "not yet evaluated" in field["display_value"]


def test_build_render_context_flagged_field_gets_flagged_placeholder():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={"power_energy": {
            "electricity_rate_cents_per_kwh": StoredFieldValue(
                value=99.0, status=FieldStatus.FLAGGED, schema_version=1),
        }},
    )
    context = build_render_context(schema, record)
    power_category = next(c for c in context["categories"] if c["label"] == "Power, Energy & Grid Infrastructure")
    field = next(f for f in power_category["fields"] if f["key"] == "electricity_rate_cents_per_kwh")
    assert "flagged as incorrect" in field["display_value"]


def test_build_render_context_low_confidence_field_carries_caveat():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={"water_supply": {
            "well_depth_to_water_table_ft": StoredFieldValue(
                value=80.0, source_url="https://x.com", fetched_date="2026-07-22",
                status=FieldStatus.VALID, schema_version=1),
        }},
    )
    context = build_render_context(schema, record)
    water_category = next(c for c in context["categories"] if c["label"] == "Water Supply & Security")
    field = next(f for f in water_category["fields"] if f["key"] == "well_depth_to_water_table_ft")
    assert field["caveat"] is not None
    assert "parcel" in field["caveat"]


def test_build_render_context_derived_field_present_and_computed():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={"geographic_hazards": {
            "elevation_ft": StoredFieldValue(value=489.0, status=FieldStatus.VALID, schema_version=1),
            "distance_to_ocean_mi": StoredFieldValue(value=150.0, status=FieldStatus.VALID, schema_version=1),
        }},
    )
    context = build_render_context(schema, record)
    geo_category = context["categories"][0]  # geographic_hazards is the first category in schema.json
    field = next(f for f in geo_category["fields"] if f["key"] == "bd_score")
    assert field["display_value"] == "639"


def test_render_city_end_to_end(isolated_dirs):
    from fetch import save_city_record
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={"power_energy": {
            "electricity_rate_cents_per_kwh": StoredFieldValue(
                value=12.5, source_url="https://x.com", fetched_date="2026-07-22",
                status=FieldStatus.VALID, schema_version=1),
        }},
    )
    save_city_record(record)
    out_path = render_city("austin-tx")
    assert out_path.exists()
    content = out_path.read_text()
    assert "Austin, TX" in content
    assert "12.5" in content
    assert "| Field | Value | Source |" in content


def test_build_render_context_excludes_summary_pseudo_category_from_categories_list():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={},
    )
    context = build_render_context(schema, record)
    assert "summary" not in [c["key"] for c in context["categories"]]
    assert len(context["categories"]) == 8


def test_build_render_context_surfaces_category_and_overall_summaries():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={
            "power_energy": {
                "category_summary": StoredFieldValue(
                    value="Power is cheap and reliable here.", status=FieldStatus.VALID, schema_version=1),
                "category_pros_cons": StoredFieldValue(
                    value={"pros": ["cheap electricity"], "cons": []}, status=FieldStatus.VALID, schema_version=1),
            },
            "summary": {
                "overall_summary": StoredFieldValue(
                    value="Overall, a solid choice.", status=FieldStatus.VALID, schema_version=1),
            },
        },
    )
    context = build_render_context(schema, record)
    power = next(c for c in context["categories"] if c["key"] == "power_energy")
    assert power["summary"] == "Power is cheap and reliable here."
    assert power["pros_cons"] == {"pros": ["cheap electricity"], "cons": []}
    assert context["overall_summary"] == "Overall, a solid choice."
    # neither synthesized field shows up as a regular field row
    field_keys = [f["key"] for f in power["fields"]]
    assert "category_summary" not in field_keys
    assert "category_pros_cons" not in field_keys


def test_render_city_escapes_pipe_in_field_value(isolated_dirs):
    from fetch import save_city_record
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={"geographic_hazards": {
            "volcano_proximity": StoredFieldValue(
                value="None | negligible risk", source_url="https://x.com", fetched_date="2026-07-22",
                status=FieldStatus.VALID, schema_version=1),
        }},
    )
    save_city_record(record)
    out_path = render_city("austin-tx")
    content = out_path.read_text()
    assert "None \\| negligible risk" in content
