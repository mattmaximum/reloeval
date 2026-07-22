from models import CityRecord, FieldStatus, NormalizedCity, StoredFieldValue, load_schema
from lint import find_gaps, run_lint, print_report


def make_record(power_energy_fields: dict) -> CityRecord:
    return CityRecord(
        input_city_state="Austin, TX",
        normalized=NormalizedCity(city="Austin", state="TX", county="Travis County"),
        slug="austin-tx",
        categories={"power_energy": power_energy_fields},
    )


def test_find_gaps_missing_field():
    schema = load_schema()
    record = make_record({})  # nothing fetched at all
    gaps = find_gaps(schema, record)
    assert gaps["power_energy"]["electricity_rate_cents_per_kwh"] == "missing"


def test_find_gaps_unresolved_field():
    schema = load_schema()
    record = make_record({
        "electricity_rate_cents_per_kwh": StoredFieldValue(status=FieldStatus.UNRESOLVED, schema_version=1),
    })
    gaps = find_gaps(schema, record)
    assert gaps["power_energy"]["electricity_rate_cents_per_kwh"] == "unresolved"


def test_find_gaps_flagged_field():
    schema = load_schema()
    record = make_record({
        "electricity_rate_cents_per_kwh": StoredFieldValue(
            value=1.0, status=FieldStatus.FLAGGED, schema_version=1),
    })
    gaps = find_gaps(schema, record)
    assert gaps["power_energy"]["electricity_rate_cents_per_kwh"] == "flagged"


def test_find_gaps_stale_schema_version():
    schema = load_schema()
    record = make_record({
        "electricity_rate_cents_per_kwh": StoredFieldValue(
            value=1.0, status=FieldStatus.VALID, schema_version=0),  # behind current version (1)
    })
    gaps = find_gaps(schema, record)
    assert gaps["power_energy"]["electricity_rate_cents_per_kwh"] == "stale_version"


def test_find_gaps_empty_when_all_fields_valid_and_current():
    schema = load_schema()
    from fetch import fetchable_fields
    fields = {
        key: StoredFieldValue(value="x", status=FieldStatus.VALID, schema_version=field_def["schema_version"])
        for key, field_def in fetchable_fields(schema, "power_energy").items()
    }
    record = make_record(fields)
    # Also need every OTHER category fully populated for a true zero-gap city.
    for cat_key in schema["categories"]:
        if cat_key == "power_energy":
            continue
        record.categories[cat_key] = {
            key: StoredFieldValue(value="x", status=FieldStatus.VALID, schema_version=field_def["schema_version"])
            for key, field_def in fetchable_fields(schema, cat_key).items()
        }
    gaps = find_gaps(schema, record)
    assert gaps == {}


def test_run_lint_omits_cities_with_zero_gaps_and_includes_cities_with_gaps():
    clean = make_record({})
    from fetch import fetchable_fields
    all_valid_categories = {}
    schema = load_schema()
    for cat_key in schema["categories"]:
        all_valid_categories[cat_key] = {
            key: StoredFieldValue(value="x", status=FieldStatus.VALID, schema_version=field_def["schema_version"])
            for key, field_def in fetchable_fields(schema, cat_key).items()
        }
    clean_record = CityRecord(
        input_city_state="Denver, CO",
        normalized=NormalizedCity(city="Denver", state="CO", county="Denver County"),
        slug="denver-co",
        categories=all_valid_categories,
    )
    dirty_record = make_record({})  # everything missing

    report = run_lint([clean_record, dirty_record])
    assert "denver-co" not in report
    assert "austin-tx" in report


def test_print_report_zero_cities(capsys):
    print_report({}, 0)
    assert "No cities evaluated yet" in capsys.readouterr().out


def test_print_report_all_clean(capsys):
    print_report({}, 3)
    assert "3 cities are fully valid" in capsys.readouterr().out
