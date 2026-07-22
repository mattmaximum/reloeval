import asyncio

import backfill
from models import CityRecord, FieldStatus, NormalizedCity, StoredFieldValue


def test_run_backfill_renders_each_city_after_fetching(monkeypatch, isolated_dirs):
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={
            "power_energy": {
                "solar_score": StoredFieldValue(
                    status=FieldStatus.UNRESOLVED, schema_version=1,
                )
            }
        },
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    monkeypatch.setattr(backfill, "list_cities", lambda: [record])

    async def fake_fetch(client, schema, city_state_input):
        return record

    monkeypatch.setattr(backfill, "fetch_city_bulk", fake_fetch)

    rendered = []
    monkeypatch.setattr(backfill, "render_city", lambda slug: rendered.append(slug))

    asyncio.run(backfill.run_backfill())

    assert rendered == ["austin-tx"]


def test_run_backfill_skips_render_when_no_gaps(monkeypatch, isolated_dirs):
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={},
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    monkeypatch.setattr(backfill, "list_cities", lambda: [])

    rendered = []
    monkeypatch.setattr(backfill, "render_city", lambda slug: rendered.append(slug))

    asyncio.run(backfill.run_backfill())

    assert rendered == []
