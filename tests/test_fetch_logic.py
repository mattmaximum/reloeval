import asyncio

import pytest

from fetch import (
    CityNotFoundError,
    category_needs_fetch,
    fetch_category,
    fetch_city_bulk,
    load_city_record,
    needs_fetch,
    normalize_city,
    save_city_record,
    slugify,
)
from models import FieldStatus, NormalizedCity, StoredFieldValue, load_schema

from .fakes import FakeAsyncAnthropic


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
    client = FakeAsyncAnthropic(handler=lambda name, kw: {
        "resolved": True, "city": "Austin", "state": "TX", "county": "Travis County",
    })
    result = asyncio.run(normalize_city(client, "austin tx"))
    assert result.city == "Austin"
    assert result.state == "TX"


def test_normalize_city_unresolvable_raises_city_not_found():
    client = FakeAsyncAnthropic(handler=lambda name, kw: {"resolved": False})
    with pytest.raises(CityNotFoundError):
        asyncio.run(normalize_city(client, "asdkjfhaksjdhf not a real place"))


def test_fetch_category_total_failure_marks_every_field_unresolved():
    schema = load_schema()
    client_handler = lambda name, kw: None  # simulates API error / timeout
    client = FakeAsyncAnthropic(handler=client_handler)
    normalized = NormalizedCity(city="Austin", state="TX", county="Travis County")

    result = asyncio.run(fetch_category(client, schema, "power_energy", normalized))

    field_defs = schema["categories"]["power_energy"]["fields"]
    assert set(result.keys()) == set(field_defs.keys())
    assert all(v.status == FieldStatus.UNRESOLVED for v in result.values())


def test_fetch_category_partial_validation_failure_isolated_to_one_field():
    schema = load_schema()

    def handler(name, kw):
        return {
            "electricity_rate_cents_per_kwh": {"value": "not a number!", "source_url": "https://x.com", "fetched_date": "2026-07-22"},
            "grid_reliability": {"value": "rare outages", "source_url": "https://x.com", "fetched_date": "2026-07-22"},
            "solar_score": {"value": 88.0, "source_url": "https://x.com", "fetched_date": "2026-07-22"},
            "net_metering_policy": {"value": "1:1", "source_url": "https://x.com", "fetched_date": "2026-07-22"},
        }

    client = FakeAsyncAnthropic(handler=handler)
    normalized = NormalizedCity(city="Austin", state="TX", county="Travis County")
    result = asyncio.run(fetch_category(client, schema, "power_energy", normalized))

    assert result["electricity_rate_cents_per_kwh"].status == FieldStatus.UNRESOLVED
    assert result["grid_reliability"].status == FieldStatus.VALID
    assert result["grid_reliability"].value == "rare outages"


def test_fetch_city_bulk_new_city_writes_all_categories(isolated_dirs):
    schema = load_schema()

    def handler(name, kw):
        if name == "resolve_city":
            return {"resolved": True, "city": "Austin", "state": "TX", "county": "Travis County"}
        # Return a plausible value for every field in whichever category was asked for.
        props = kw["tools"][0]["input_schema"]["properties"]
        return {
            field_key: {"value": "placeholder", "source_url": "https://x.com", "fetched_date": "2026-07-22"}
            for field_key in props
        }

    # power_energy and civic fields are numeric — "placeholder" string will fail
    # validation for those, which is fine: exercises the unresolved path too.
    client = FakeAsyncAnthropic(handler=handler)

    record = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))

    assert record.slug == "austin-tx"
    assert set(record.categories.keys()) == set(schema["categories"].keys())
    loaded = load_city_record("austin-tx")
    assert loaded is not None
    assert loaded.slug == "austin-tx"


def test_fetch_city_bulk_skips_categories_that_are_fully_valid_and_current(isolated_dirs):
    schema = load_schema()

    def fail_if_called(name, kw):
        if name == "resolve_city":
            return {"resolved": True, "city": "Austin", "state": "TX", "county": "Travis County"}
        raise AssertionError(f"should not have been called for {name}")

    # Pre-populate a city where every fetchable field in every category is
    # already valid at the current schema_version.
    from fetch import fetchable_fields
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

    client = FakeAsyncAnthropic(handler=fail_if_called)
    # Should complete without ever invoking the (failing) handler.
    result = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))
    assert result.slug == "austin-tx"


def test_fetch_city_bulk_never_overwrites_flagged_field_even_when_category_refetched(isolated_dirs):
    schema = load_schema()
    from fetch import fetchable_fields
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
        props = kw["tools"][0]["input_schema"]["properties"]
        return {
            key: {"value": 12.5, "source_url": "https://new.com", "fetched_date": "2026-07-22"}
            for key in props
        }

    client = FakeAsyncAnthropic(handler=handler)
    result = asyncio.run(fetch_city_bulk(client, schema, "Austin, TX"))

    flagged_field = result.categories["power_energy"]["electricity_rate_cents_per_kwh"]
    assert flagged_field.status == FieldStatus.FLAGGED
    assert flagged_field.value == 999.0  # untouched by the refetch
    # the other field in the same category, which WAS missing, did get filled in
    # (solar_score is numeric — 12.5 is a valid value for it)
    other_field = result.categories["power_energy"]["solar_score"]
    assert other_field.status == FieldStatus.VALID
    assert other_field.value == 12.5
