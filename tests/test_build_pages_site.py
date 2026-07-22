from pathlib import Path

import pytest

import build_pages_site
from models import CityRecord, NormalizedCity


@pytest.fixture
def isolated_site(tmp_path, monkeypatch):
    cities_dir = tmp_path / "cities"
    reports_dir = tmp_path / "reports"
    site_dir = tmp_path / "_site"
    cities_dir.mkdir()
    reports_dir.mkdir()

    import build_index
    monkeypatch.setattr(build_index, "CITIES_DIR", cities_dir)
    monkeypatch.setattr(build_index, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(build_pages_site, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(build_pages_site, "SITE_DIR", site_dir)

    return {"cities": cities_dir, "reports": reports_dir, "site": site_dir}


def _write_city(cities_dir, reports_dir, slug, city, state):
    record = CityRecord(
        input_city_state=f"{city}, {state}",
        normalized=NormalizedCity(city=city, state=state, county=f"{city} County"),
        slug=slug,
        categories={},
    )
    (cities_dir / f"{slug}.json").write_text(record.model_dump_json())
    (reports_dir / f"{slug}.md").write_text(f"# {city}, {state}\n\n| Field | Value |\n|---|---|\n| Schools | Good |\n")


def test_build_site_converts_markdown_report_to_html(isolated_site):
    _write_city(isolated_site["cities"], isolated_site["reports"], "austin-tx", "Austin", "TX")

    build_pages_site.build_site()

    report_html = (isolated_site["site"] / "reports" / "austin-tx.html").read_text()
    assert "<table>" in report_html  # markdown table extension converted the pipe table
    assert "Austin, TX" in report_html

    index_html = (isolated_site["site"] / "index.html").read_text()
    assert 'href="reports/austin-tx.html"' in index_html


def test_build_site_links_hash_when_report_missing(isolated_site):
    # City JSON exists but its .md report was never rendered.
    record = CityRecord(
        input_city_state="Boise, ID",
        normalized=NormalizedCity(city="Boise", state="ID", county="Ada County"),
        slug="boise-id",
        categories={},
    )
    (isolated_site["cities"] / "boise-id.json").write_text(record.model_dump_json())

    build_pages_site.build_site()

    index_html = (isolated_site["site"] / "index.html").read_text()
    assert 'href="#"' in index_html
    assert not (isolated_site["site"] / "reports" / "boise-id.html").exists()


def test_build_site_empty_state_when_no_cities(isolated_site):
    build_pages_site.build_site()
    index_html = (isolated_site["site"] / "index.html").read_text()
    assert "No cities evaluated yet." in index_html
