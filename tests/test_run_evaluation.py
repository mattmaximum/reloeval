import asyncio

import pytest

import run_evaluation
from fetch import CityNotFoundError
from models import CityRecord, FieldStatus, NormalizedCity, StoredFieldValue, fetchable_fields, load_schema


def _fully_valid_record(schema: dict) -> CityRecord:
    categories = {}
    for cat_key in schema["categories"]:
        field_defs = fetchable_fields(schema, cat_key)
        categories[cat_key] = {
            key: StoredFieldValue(
                value="x", source_url="https://x.com", fetched_date="2026-07-22",
                status=FieldStatus.VALID, schema_version=field_def["schema_version"],
            )
            for key, field_def in field_defs.items()
        }
    return CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories=categories,
    )


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")


@pytest.fixture(autouse=True)
def _no_github_output(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


def test_total_and_gap_field_counts_sums_across_categories():
    schema = load_schema()
    gaps = {"power_energy": {"solar_score": "missing", "grid_reliability": "unresolved"}}
    total, gap_count = run_evaluation.total_and_gap_field_counts(schema, gaps)
    assert gap_count == 2
    assert total == sum(len(fetchable_fields(schema, k)) for k in schema["categories"])


def test_main_returns_1_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = asyncio.run(run_evaluation._main("Austin, TX"))
    assert result == 1


def test_main_returns_2_on_city_not_found(monkeypatch):
    async def fake_fetch(client, schema, city_state_input):
        raise CityNotFoundError("could not resolve this city")

    monkeypatch.setattr(run_evaluation, "fetch_city_bulk", fake_fetch)
    result = asyncio.run(run_evaluation._main("asdkjfh not a place"))
    assert result == 2


def test_main_returns_1_on_generic_fetch_failure(monkeypatch):
    async def fake_fetch(client, schema, city_state_input):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_evaluation, "fetch_city_bulk", fake_fetch)
    result = asyncio.run(run_evaluation._main("Austin, TX"))
    assert result == 1


def test_main_returns_3_when_gap_rate_exceeds_threshold(monkeypatch):
    schema = load_schema()

    async def fake_fetch(client, schema_arg, city_state_input):
        return CityRecord(
            input_city_state="Austin, TX",
            normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
            slug="austin-tx",
            categories={},  # every field in every category counts as "missing"
        )

    render_called = {"count": 0}
    monkeypatch.setattr(run_evaluation, "fetch_city_bulk", fake_fetch)
    monkeypatch.setattr(run_evaluation, "render_city", lambda slug: render_called.__setitem__("count", render_called["count"] + 1))

    result = asyncio.run(run_evaluation._main("Austin, TX"))
    assert result == 3
    assert render_called["count"] == 0  # never renders a near-empty report


def test_main_returns_0_and_renders_on_success(monkeypatch):
    schema = load_schema()
    record = _fully_valid_record(schema)

    async def fake_fetch(client, schema_arg, city_state_input):
        return record

    render_called = {"slug": None}
    monkeypatch.setattr(run_evaluation, "fetch_city_bulk", fake_fetch)
    monkeypatch.setattr(run_evaluation, "render_city", lambda slug: render_called.__setitem__("slug", slug))

    result = asyncio.run(run_evaluation._main("Austin, TX"))
    assert result == 0
    assert render_called["slug"] == "austin-tx"


def test_main_returns_1_when_render_fails(monkeypatch):
    schema = load_schema()
    record = _fully_valid_record(schema)

    async def fake_fetch(client, schema_arg, city_state_input):
        return record

    def fake_render(slug):
        raise RuntimeError("template broke")

    monkeypatch.setattr(run_evaluation, "fetch_city_bulk", fake_fetch)
    monkeypatch.setattr(run_evaluation, "render_city", fake_render)

    result = asyncio.run(run_evaluation._main("Austin, TX"))
    assert result == 1


def test_write_output_appends_to_github_output_file(tmp_path, monkeypatch):
    output_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    run_evaluation.write_output("slug", "austin-tx")
    run_evaluation.write_output("gap_summary", "line one\nline two")

    content = output_file.read_text()
    assert "slug=austin-tx" in content
    assert "gap_summary<<EOF\nline one\nline two\nEOF" in content
