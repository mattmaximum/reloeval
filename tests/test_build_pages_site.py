import pytest

import build_pages_site
from models import CityRecord, FieldStatus, NormalizedCity, StoredFieldValue, load_schema


@pytest.fixture
def isolated_site(tmp_path, monkeypatch):
    cities_dir = tmp_path / "cities"
    site_dir = tmp_path / "_site"
    cities_dir.mkdir()

    import build_index
    monkeypatch.setattr(build_index, "CITIES_DIR", cities_dir)
    monkeypatch.setattr(build_pages_site, "SITE_DIR", site_dir)

    return {"cities": cities_dir, "site": site_dir}


def _write_city(cities_dir, slug, city, state, categories=None):
    record = CityRecord(
        input_city_state=f"{city}, {state}",
        normalized=NormalizedCity(city=city, state=state, county=f"{city} County"),
        slug=slug,
        categories=categories or {},
    )
    (cities_dir / f"{slug}.json").write_text(record.model_dump_json())
    return record


def test_completion_stats_counts_valid_vs_total():
    schema = load_schema()
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={"power_energy": {
            "electricity_rate_cents_per_kwh": StoredFieldValue(
                value=12.5, status=FieldStatus.VALID, schema_version=1),
        }},
    )
    stats = build_pages_site.completion_stats(schema, record)
    total = sum(len(build_pages_site.fetchable_fields(schema, k)) for k in schema["categories"])
    assert stats["total"] == total
    assert stats["valid"] == 1
    assert stats["percent"] == round(100 * 1 / total)


def test_render_climate_chart_svg_returns_inline_svg_markup():
    rows = [
        {"month": m, "avg_high_f": 70.0, "avg_low_f": 50.0, "avg_rainfall_in": 1.0, "avg_snowfall_in": 0.0}
        for m in ["January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]
    ]
    svg = build_pages_site.render_climate_chart_svg(rows)
    assert svg.startswith("<svg")
    assert "</svg>" in svg
    assert "<?xml" not in svg  # stripped for safe inline embedding


def test_build_site_writes_report_with_risk_badge_and_chart(isolated_site):
    categories = {
        "geographic_hazards": {
            "elevation_ft": StoredFieldValue(value=4646.0, status=FieldStatus.VALID, schema_version=1),
            "distance_to_ocean_mi": StoredFieldValue(value=598.0, status=FieldStatus.VALID, schema_version=1),
            "flood_risk": StoredFieldValue(
                value="Moderate to High - frequent flash flooding", source_url="https://x.com",
                fetched_date="2026-07-22", status=FieldStatus.VALID, schema_version=1,
            ),
        },
        "geographic_climate": {
            "monthly_climate_table": StoredFieldValue(
                value=[
                    {"month": m, "avg_high_f": 70.0, "avg_low_f": 50.0, "avg_rainfall_in": 1.0, "avg_snowfall_in": 0.0}
                    for m in ["January", "February", "March", "April", "May", "June",
                               "July", "August", "September", "October", "November", "December"]
                ],
                source_url="https://x.com", fetched_date="2026-07-22",
                status=FieldStatus.VALID, schema_version=1,
            ),
        },
    }
    _write_city(isolated_site["cities"], "grand-junction-co", "Grand Junction", "CO", categories)

    build_pages_site.build_site()

    report_html = (isolated_site["site"] / "reports" / "grand-junction-co.html").read_text()
    assert "Grand Junction, CO" in report_html
    assert "risk-badge" in report_html
    assert "<svg" in report_html

    index_html = (isolated_site["site"] / "index.html").read_text()
    assert 'href="reports/grand-junction-co.html"' in index_html
    assert "fields" in index_html  # completion count shown on the index too


def test_build_site_empty_state_when_no_cities(isolated_site):
    build_pages_site.build_site()
    index_html = (isolated_site["site"] / "index.html").read_text()
    assert "No cities evaluated yet." in index_html


def test_build_site_handles_city_with_no_gaps_and_no_risk_fields(isolated_site):
    # A fully-missing city (categories={}) should render placeholders, not crash.
    _write_city(isolated_site["cities"], "boise-id", "Boise", "ID")
    build_pages_site.build_site()
    report_html = (isolated_site["site"] / "reports" / "boise-id.html").read_text()
    assert "Not yet evaluated" in report_html


def test_build_site_shows_fit_badges_and_dealbreaker_reason(isolated_site):
    categories = {
        "geographic_hazards": {
            "elevation_ft": StoredFieldValue(value=100.0, status=FieldStatus.VALID, schema_version=1),
            "distance_to_ocean_mi": StoredFieldValue(value=50.0, status=FieldStatus.VALID, schema_version=1),
        },
    }
    _write_city(isolated_site["cities"], "low-bd-tx", "Low BD", "TX", categories)

    build_pages_site.build_site()

    report_html = (isolated_site["site"] / "reports" / "low-bd-tx.html").read_text()
    assert "fit-badge" in report_html
    assert "Dealbreaker" in report_html  # bd_score = 150, well under the 2000 threshold
    assert "BD score" in report_html

    index_html = (isolated_site["site"] / "index.html").read_text()
    assert "fit-badge" in index_html
    assert "Self-Sufficiency" in index_html
    assert "Self-Sufficiency & Resilience" not in index_html


def test_comparison_payload_strips_chart_svg_and_citation_keeps_rest():
    from render import build_render_context
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
    context = build_render_context(schema, record)
    context["categories"] = build_pages_site.enrich_categories(context["categories"])
    context["lens_scores"] = {}

    payload = build_pages_site.comparison_payload(record, context)

    assert payload["city"] == "Austin"
    power = next(c for c in payload["categories"] if c["key"] == "power_energy")
    field = next(f for f in power["fields"] if f["key"] == "electricity_rate_cents_per_kwh")
    assert field["display_value"] == "12.5"
    assert field["citation_url"] == "https://x.com"
    assert "chart_svg" not in field
    assert "citation" not in field


def test_build_site_writes_cities_data_json_and_compare_page(isolated_site):
    _write_city(isolated_site["cities"], "a-tx", "A", "TX")
    _write_city(isolated_site["cities"], "b-tx", "B", "TX")

    build_pages_site.build_site()

    import json
    data = json.loads((isolated_site["site"] / "cities-data.json").read_text())
    assert set(data.keys()) == {"a-tx", "b-tx"}
    assert data["a-tx"]["city"] == "A"
    assert "lens_scores" in data["a-tx"]

    compare_html = (isolated_site["site"] / "compare.html").read_text()
    assert "compare-a" in compare_html
    assert "compare-b" in compare_html
    assert "Need at least 2" not in compare_html


def test_build_site_compare_page_shows_message_when_fewer_than_two_cities(isolated_site):
    _write_city(isolated_site["cities"], "only-one-tx", "Only One", "TX")

    build_pages_site.build_site()

    compare_html = (isolated_site["site"] / "compare.html").read_text()
    assert "Need at least 2 evaluated cities" in compare_html
    assert 'id="compare-a"' not in compare_html

    index_html = (isolated_site["site"] / "index.html").read_text()
    assert "Compare two cities" not in index_html  # link only shown with 2+ cities
