import asyncio

import pytest

from fetch import (
    CityNotFoundError,
    _fill_category_summaries,
    _fill_overall_summary,
    category_needs_fetch,
    fetch_category,
    fetch_city_bulk,
    load_city_record,
    needs_fetch,
    normalize_city,
    save_city_record,
    slugify,
)
from models import CityRecord, FieldStatus, NormalizedCity, StoredFieldValue, load_schema

from .fakes import FakeAsyncOpenAI


_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _field_type_lookup(schema):
    lookup = {}
    for category in schema["categories"].values():
        for key, field_def in category["fields"].items():
            lookup[key] = field_def["type"]
    return lookup


def _plausible_value(field_type):
    if field_type == "number":
        return 42.0
    if field_type == "retail_presence":
        return {"available": True, "distance_mi": 3.0}
    if field_type == "table":
        return [
            {"month": m, "avg_high_f": 70.0, "avg_low_f": 50.0, "avg_rainfall_in": 1.0, "avg_snowfall_in": 0.0}
            for m in _MONTHS
        ]
    return "placeholder text"


def _fully_valid_research_categories(schema):
    """Every web-search field valid, every category_summary and
    overall_summary still missing -- the exact state fetch_city_bulk hits
    right after merging real data, before the summary pass runs."""
    from fetch import web_search_fields
    categories = {}
    for category_key in schema["categories"]:
        field_defs = web_search_fields(schema, category_key)
        categories[category_key] = {
            key: StoredFieldValue(
                value=_plausible_value(field_def["type"]), source_url="https://x.com",
                fetched_date="2026-07-22", status=FieldStatus.VALID, schema_version=field_def["schema_version"],
            )
            for key, field_def in field_defs.items()
        }
    return categories


def test_slugify_is_case_and_space_insensitive():
    a = NormalizedCity(city="Austin", state="TX", county="Travis County")
    b = NormalizedCity(city="austin", state="tx", county="Travis County")
    assert slugify(a) == slugify(b) == "austin-tx"


def test_slugify_includes_state_to_disambiguate_same_named_cities():
    springfield_il = NormalizedCity(city="Springfield", state="IL", county="Sangamon County")
    springfield_mo = NormalizedCity(city="Springfield", state="MO", county="Greene County")
    assert slugify(springfield_il) != slugify(springfield_mo)


@pytest.mark.parametrize(
    "existing,current_version,expected",
    [
        (None, 1, True),
        (StoredFieldValue(status=FieldStatus.VALID, schema_version=1), 1, False),
        (StoredFieldValue(status=FieldStatus.VALID, schema_version=1), 2, True),
        (StoredFieldValue(status=FieldStatus.UNRESOLVED, schema_version=1), 1, True),
        (StoredFieldValue(status=FieldStatus.FLAGGED, schema_version=1), 1, False),
        (StoredFieldValue(status=FieldStatus.FLAGGED, schema_version=1), 2, False),
    ],
)
def test_needs_fetch_state_machine(existing, current_version, expected):
    assert needs_fetch(existing, current_version) == expected


def test_category_needs_fetch_true_if_any_field_needs_it():
    field_defs = {"a": {"schema_version": 1}, "b": {"schema_version": 1}}
    existing = {"a": StoredFieldValue(status=FieldStatus.VALID, schema_version=1)}
    assert category_needs_fetch(existing, field_defs) is True  # "b" is missing


def test_category_needs_fetch_false_when_all_current():
    field_defs = {"a": {"schema_version": 1}}
    existing = {"a": StoredFieldValue(status=FieldStatus.VALID, schema_version=1)}
    assert category_needs_fetch(existing, field_defs) is False


def test_normalize_city_success():
    client = FakeAsyncOpenAI(handler=lambda name, kw: {
        "resolved": True, "city": "Austin", "state": "TX", "county": "Travis County",
    })
    result = asyncio.run(normalize_city(client, "austin tx"))
    assert result.city == "Austin"
    assert result.state == "TX"


def test_normalize_city_unresolvable_raises_city_not_found():
    client = FakeAsyncOpenAI(handler=lambda name, kw: {"resolved": False})
    with pytest.raises(CityNotFoundError):
        asyncio.run(normalize_city(client, "asdkjfhaksjdhf not a real place"))


def test_normalize_city_resolved_true_but_missing_county_raises_city_not_found():
    # Real failure seen on "Penrose, CO": resolved=true but the model left
    # county out entirely. Must not crash with a bare KeyError.
    client = FakeAsyncOpenAI(handler=lambda name, kw: {"resolved": True, "city": "Penrose", "state": "CO"})
    with pytest.raises(CityNotFoundError):
        asyncio.run(normalize_city(client, "Penrose, CO"))


def test_fetch_category_total_failure_marks_every_field_unresolved(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr("fetch.asyncio.sleep", lambda *a, **kw: real_sleep(0))
    schema = load_schema()
    client_handler = lambda name, kw: None  # simulates API error / timeout on every attempt
    client = FakeAsyncOpenAI(handler=client_handler)
    normalized = NormalizedCity(city="Austin", state="TX", county="Travis County")

    result = asyncio.run(fetch_category(client, schema, "power_energy", normalized))

    from fetch import web_search_fields
    field_defs = web_search_fields(schema, "power_energy")  # fetch_category never handles category_summary
    assert set(result.keys()) == set(field_defs.keys())
    assert all(v.status == FieldStatus.UNRESOLVED for v in result.values())


def test_fetch_category_retries_once_and_succeeds_on_second_attempt(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr("fetch.asyncio.sleep", lambda *a, **kw: real_sleep(0))
    schema = load_schema()
    calls = {"count": 0}

    # power_energy field types: electricity_rate_cents_per_kwh/solar_score are
    # numbers, grid_reliability/net_metering_policy are strings — give each a
    # type-appropriate value so every field actually validates.
    values_by_field = {
        "electricity_rate_cents_per_kwh": 12.5,
        "solar_score": 88.0,
        "grid_reliability": "rare outages",
        "net_metering_policy": "1:1",
    }

    def handler(name, kw):
        calls["count"] += 1
        if calls["count"] == 1:
            return None  # first attempt fails (simulated transient error)
        props = kw["response_format"]["json_schema"]["schema"]["properties"]
        return {
            key: {"value": values_by_field[key], "source_url": "https://x.com", "fetched_date": "2026-07-22"}
            for key in props
        }

    client = FakeAsyncOpenAI(handler=handler)
    normalized = NormalizedCity(city="Austin", state="TX", county="Travis County")
    result = asyncio.run(fetch_category(client, schema, "power_energy", normalized))

    assert calls["count"] == 2
    assert all(v.status == FieldStatus.VALID for v in result.values())


def test_fetch_category_partial_validation_failure_isolated_to_one_field():
    schema = load_schema()

    def handler(name, kw):
        return {
            "electricity_rate_cents_per_kwh": {"value": "not a number!", "source_url": "https://x.com", "fetched_date": "2026-07-22"},
            "grid_reliability": {"value": "rare outages", "source_url": "https://x.com", "fetched_date": "2026-07-22"},
            "solar_score": {"value": 88.0, "source_url": "https://x.com", "fetched_date": "2026-07-22"},
            "net_metering_policy": {"value": "1:1", "source_url": "https://x.com", "fetched_date": "2026-07-22"},
        }

    client = FakeAsyncOpenAI(handler=handler)
    normalized = NormalizedCity(city="Austin", state="TX", county="Travis County")
    result = asyncio.run(fetch_category(client, schema, "power_energy", normalized))

    assert result["electricity_rate_cents_per_kwh"].status == FieldStatus.UNRESOLVED
    assert result["grid_reliability"].status == FieldStatus.VALID
    assert result["grid_reliability"].value == "rare outages"


def test_fetch_category_includes_closed_metro_list_for_geographic_hazards():
    # Regression test: the model repeatedly answered nearest_large_metro_name
    # with the nearest city of ANY size (Grand Junction for Collbran CO,
    # Spokane for Coeur d'Alene ID) instead of one actually clearing the
    # field's own "population over 1,000,000" bar, producing false
    # Self-Sufficiency dealbreakers. The prompt now hands the model a closed
    # list to choose from instead of trusting it to verify population itself.
    schema = load_schema()
    captured = {}

    def handler(name, kw):
        captured["content"] = kw["messages"][0]["content"]
        return None  # content capture is all this test needs; let it fail fast

    client = FakeAsyncOpenAI(handler=handler)
    normalized = NormalizedCity(city="Austin", state="TX", county="Travis County")
    asyncio.run(fetch_category(client, schema, "geographic_hazards", normalized))

    assert "closed list" in captured["content"]
    assert "Denver-Aurora-Centennial, CO" in captured["content"]
    assert "Seattle-Tacoma-Bellevue, WA" in captured["content"]


def test_fetch_category_omits_metro_list_for_categories_without_that_field():
    schema = load_schema()
    captured = {}

    def handler(name, kw):
        captured["content"] = kw["messages"][0]["content"]
        return None

    client = FakeAsyncOpenAI(handler=handler)
    normalized = NormalizedCity(city="Austin", state="TX", county="Travis County")
    asyncio.run(fetch_category(client, schema, "power_energy", normalized))

    assert "closed list" not in captured["content"]


def test_fetch_city_bulk_new_city_writes_all_categories(isolated_dirs):
    schema = load_schema()

    def handler(name, kw):
        if name == "resolve_city":
            return {"resolved": True, "city": "Austin", "state": "TX", "county": "Travis County"}
        # Return a plausible value for every field in whichever category was asked for.
        props = kw["response_format"]["json_schema"]["schema"]["properties"]
        return {
            field_key: {"value": "placeholder", "source_url": "https://x.com", "fetched_date": "2026-07-22"}
            for field_key in props
        }

    # power_energy and civic fields are numeric — "placeholder" string will fail
    # validation for those, which is fine: exercises the unresolved path too.
    client = FakeAsyncOpenAI(handler=handler)

    record = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))

    assert record.slug == "austin-tx"
    assert set(record.categories.keys()) == set(schema["categories"].keys())
    loaded = load_city_record("austin-tx")
    assert loaded is not None
    assert loaded.slug == "austin-tx"


def test_fetch_city_bulk_sets_first_evaluated_date_for_new_city(isolated_dirs):
    from datetime import date
    schema = load_schema()

    def handler(name, kw):
        if name == "resolve_city":
            return {"resolved": True, "city": "Austin", "state": "TX", "county": "Travis County"}
        return {"summary": "x", "pros": [], "cons": []}

    client = FakeAsyncOpenAI(handler=handler)
    record = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))
    assert record.first_evaluated_date == date.today().isoformat()


def test_fetch_city_bulk_preserves_first_evaluated_date_on_backfill(isolated_dirs):
    schema = load_schema()
    from models import CityRecord
    existing = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={"power_energy": {
            "solar_score": StoredFieldValue(status=FieldStatus.UNRESOLVED, schema_version=1),
        }},
        first_evaluated_date="2020-01-01",
    )
    save_city_record(existing)

    def handler(name, kw):
        if name == "resolve_city":
            return {"resolved": True, "city": "Austin", "state": "TX", "county": "Travis County"}
        props = kw["response_format"]["json_schema"]["schema"]["properties"]
        return {
            key: {"value": 12.5, "source_url": "https://x.com", "fetched_date": "2026-07-22"}
            for key in props
        }

    client = FakeAsyncOpenAI(handler=handler)
    result = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))
    assert result.first_evaluated_date == "2020-01-01"  # untouched by the backfill


def test_fetch_city_bulk_skips_categories_that_are_fully_valid_and_current(isolated_dirs):
    schema = load_schema()

    def fail_if_called(name, kw):
        if name == "resolve_city":
            return {"resolved": True, "city": "Austin", "state": "TX", "county": "Travis County"}
        raise AssertionError(f"should not have been called for {name}")

    # Pre-populate a city where every fetchable field in every category is
    # already valid at the current schema_version.
    from models import fetchable_fields
    categories = {}
    for cat_key in schema["categories"]:
        field_defs = fetchable_fields(schema, cat_key)
        categories[cat_key] = {
            key: StoredFieldValue(value="x", source_url="https://x.com", fetched_date="2026-07-22",
                                   status=FieldStatus.VALID, schema_version=field_def["schema_version"])
            for key, field_def in field_defs.items()
        }
    from models import CityRecord
    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories=categories,
    )
    save_city_record(record)

    client = FakeAsyncOpenAI(handler=fail_if_called)
    # Should complete without ever invoking the (failing) handler.
    result = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))
    assert result.slug == "austin-tx"


def test_fetch_city_bulk_never_overwrites_flagged_field_even_when_category_refetched(isolated_dirs):
    schema = load_schema()
    from models import fetchable_fields
    field_defs = fetchable_fields(schema, "power_energy")

    # electricity_rate_cents_per_kwh is flagged (user found it wrong);
    # everything else in the category is missing, forcing a refetch.
    existing_categories = {
        "power_energy": {
            "electricity_rate_cents_per_kwh": StoredFieldValue(
                value=999.0, source_url="https://old.com", fetched_date="2026-01-01",
                status=FieldStatus.FLAGGED, schema_version=1,
            ),
        }
    }
    from models import CityRecord
    save_city_record(CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories=existing_categories,
    ))

    def handler(name, kw):
        if name == "resolve_city":
            return {"resolved": True, "city": "Austin", "state": "TX", "county": "Travis County"}
        props = kw["response_format"]["json_schema"]["schema"]["properties"]
        return {
            key: {"value": 12.5, "source_url": "https://new.com", "fetched_date": "2026-07-22"}
            for key in props
        }

    client = FakeAsyncOpenAI(handler=handler)
    result = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))

    flagged_field = result.categories["power_energy"]["electricity_rate_cents_per_kwh"]
    assert flagged_field.status == FieldStatus.FLAGGED
    assert flagged_field.value == 999.0  # untouched by the refetch
    # the other field in the same category, which WAS missing, did get filled in
    # (solar_score is numeric — 12.5 is a valid value for it)
    other_field = result.categories["power_energy"]["solar_score"]
    assert other_field.status == FieldStatus.VALID
    assert other_field.value == 12.5


def test_fill_category_summaries_generates_summary_when_category_fully_valid():
    schema = load_schema()
    merged = _fully_valid_research_categories(schema)

    def handler(name, kw):
        return {"summary": "A short summary.", "pros": ["cheap power"], "cons": ["moderate flood risk"]}

    client = FakeAsyncOpenAI(handler=handler)
    asyncio.run(_fill_category_summaries(client, schema, merged))

    for category_key in schema["categories"]:
        if category_key == "summary":
            continue
        assert merged[category_key]["category_summary"].value == "A short summary."
        assert merged[category_key]["category_summary"].status == FieldStatus.VALID
        assert merged[category_key]["category_pros_cons"].status == FieldStatus.VALID
        assert merged[category_key]["category_pros_cons"].value == {"pros": ["cheap power"], "cons": ["moderate flood risk"]}


def test_summarize_category_falls_back_to_summary_only_when_pros_cons_malformed():
    from fetch import summarize_category
    schema = load_schema()
    from fetch import web_search_fields
    field_defs = web_search_fields(schema, "power_energy")
    category_fields = {
        key: StoredFieldValue(value=42.0, status=FieldStatus.VALID, schema_version=1)
        for key in field_defs
    }

    # Malformed: "pros" is a string, not a list -- ProsCons validation should reject it.
    client = FakeAsyncOpenAI(handler=lambda name, kw: {"summary": "Still a valid summary.", "pros": "not a list", "cons": []})
    result = asyncio.run(summarize_category(client, "Power, Energy & Grid Infrastructure", category_fields, field_defs))

    assert result["summary"] == "Still a valid summary."
    assert result["pros_cons"] is None


def test_summarize_category_returns_none_when_no_valid_facts():
    from fetch import summarize_category
    schema = load_schema()
    from fetch import web_search_fields
    field_defs = web_search_fields(schema, "power_energy")
    category_fields = {}  # nothing valid to summarize

    calls = []
    client = FakeAsyncOpenAI(handler=lambda name, kw: calls.append(name) or {"summary": "x", "pros": [], "cons": []})
    result = asyncio.run(summarize_category(client, "Power, Energy & Grid Infrastructure", category_fields, field_defs))
    assert result is None
    assert calls == []  # confirms it returned early, not that a call happened to fail silently


def test_fill_category_summaries_skips_category_with_real_gaps():
    schema = load_schema()
    merged = _fully_valid_research_categories(schema)
    # Break one field in power_energy so the category still has a gap.
    merged["power_energy"]["solar_score"] = StoredFieldValue(status=FieldStatus.UNRESOLVED, schema_version=1)

    def fail_if_power_energy(name, kw):
        raise AssertionError("should not summarize power_energy while it still has a gap")

    client = FakeAsyncOpenAI(handler=lambda name, kw: {"summary": "A short summary."})
    asyncio.run(_fill_category_summaries(client, schema, merged))

    assert "category_summary" not in merged["power_energy"]
    # every other, still-fully-valid category did get summarized
    assert merged["water_supply"]["category_summary"].status == FieldStatus.VALID


def test_fill_category_summaries_makes_no_calls_when_summary_already_current():
    schema = load_schema()
    merged = _fully_valid_research_categories(schema)
    for category_key in schema["categories"]:
        if category_key == "summary":
            continue
        merged[category_key]["category_summary"] = StoredFieldValue(
            value="Already summarized.", status=FieldStatus.VALID, schema_version=1)
        merged[category_key]["category_pros_cons"] = StoredFieldValue(
            value={"pros": [], "cons": []}, status=FieldStatus.VALID, schema_version=1)

    calls = []
    client = FakeAsyncOpenAI(handler=lambda name, kw: calls.append(name) or {"summary": "x", "pros": [], "cons": []})
    asyncio.run(_fill_category_summaries(client, schema, merged))
    assert calls == []  # confirms the API was never actually called, not just that nothing raised


def test_fill_overall_summary_waits_for_every_category_summary():
    schema = load_schema()
    merged = _fully_valid_research_categories(schema)
    category_keys = [k for k in schema["categories"] if k != "summary"]
    for category_key in category_keys[:-1]:  # all but one
        merged[category_key]["category_summary"] = StoredFieldValue(
            value="Summary.", status=FieldStatus.VALID, schema_version=1)

    def fail_if_called(name, kw):
        raise AssertionError("should not generate the overall summary until every category has one")

    client = FakeAsyncOpenAI(handler=fail_if_called)
    normalized = NormalizedCity(city="Austin", state="TX", county="Travis County")
    asyncio.run(_fill_overall_summary(client, schema, normalized, merged))
    assert "overall_summary" not in merged.get("summary", {})


def test_fill_overall_summary_generates_once_every_category_has_one():
    schema = load_schema()
    merged = _fully_valid_research_categories(schema)
    for category_key in schema["categories"]:
        if category_key == "summary":
            continue
        merged[category_key]["category_summary"] = StoredFieldValue(
            value=f"Summary for {category_key}.", status=FieldStatus.VALID, schema_version=1)

    client = FakeAsyncOpenAI(handler=lambda name, kw: {"summary": "The overall paragraph."})
    normalized = NormalizedCity(city="Austin", state="TX", county="Travis County")
    asyncio.run(_fill_overall_summary(client, schema, normalized, merged))
    assert merged["summary"]["overall_summary"].value == "The overall paragraph."
    assert merged["summary"]["overall_summary"].status == FieldStatus.VALID


def test_fetch_city_bulk_generates_category_and_overall_summaries_end_to_end(isolated_dirs):
    schema = load_schema()
    type_lookup = _field_type_lookup(schema)

    def handler(name, kw):
        if name == "resolve_city":
            return {"resolved": True, "city": "Austin", "state": "TX", "county": "Travis County"}
        if name == "report_summary":
            return {"summary": "A short category summary."}
        if name == "report_overall_summary":
            return {"summary": "A one-paragraph overall summary."}
        props = kw["response_format"]["json_schema"]["schema"]["properties"]
        return {
            field_key: {"value": _plausible_value(type_lookup[field_key]), "source_url": "https://x.com", "fetched_date": "2026-07-22"}
            for field_key in props
        }

    client = FakeAsyncOpenAI(handler=handler)
    record = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))

    for category_key in schema["categories"]:
        if category_key == "summary":
            continue
        assert record.categories[category_key]["category_summary"].value == "A short category summary."
    assert record.categories["summary"]["overall_summary"].value == "A one-paragraph overall summary."


def test_fetch_city_bulk_makes_zero_calls_when_fully_summarized_and_current(isolated_dirs):
    schema = load_schema()
    categories = _fully_valid_research_categories(schema)
    for category_key in schema["categories"]:
        if category_key == "summary":
            categories.setdefault("summary", {})["overall_summary"] = StoredFieldValue(
                value="Already summarized.", status=FieldStatus.VALID, schema_version=1)
        else:
            categories[category_key]["category_summary"] = StoredFieldValue(
                value="Already summarized.", status=FieldStatus.VALID, schema_version=1)
            categories[category_key]["category_pros_cons"] = StoredFieldValue(
                value={"pros": ["cheap"], "cons": ["hot"]}, status=FieldStatus.VALID, schema_version=1)

    record = CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories=categories,
    )
    save_city_record(record)

    calls = []

    def track_calls(name, kw):
        calls.append(name)
        if name == "resolve_city":
            return {"resolved": True, "city": "Austin", "state": "TX", "county": "Travis County"}
        raise AssertionError(f"should not have called the API for {name}")

    client = FakeAsyncOpenAI(handler=track_calls)
    result = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))
    assert calls == ["resolve_city"]  # confirms report_summary/report_overall_summary were never invoked
    assert result.categories["summary"]["overall_summary"].value == "Already summarized."
