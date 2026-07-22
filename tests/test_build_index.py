from models import CityRecord, NormalizedCity
from build_index import build_index


def make_city(city: str, state: str, county: str, slug: str) -> CityRecord:
    return CityRecord(
        input_city_state=f"{city}, {state}",
        normalized=NormalizedCity(city=city, state=state, county=county),
        slug=slug,
        categories={},
    )


def test_build_index_empty_state(isolated_dirs):
    path = build_index()
    content = path.read_text()
    assert "No cities evaluated yet" in content


def test_build_index_lists_cities_sorted_alphabetically(isolated_dirs):
    from fetch import save_city_record
    save_city_record(make_city("Denver", "CO", "Denver County", "denver-co"))
    save_city_record(make_city("Austin", "TX", "Travis County", "austin-tx"))
    (isolated_dirs["reports"] / "denver-co.md").write_text("# Denver report")
    (isolated_dirs["reports"] / "austin-tx.md").write_text("# Austin report")

    path = build_index()
    content = path.read_text()

    austin_pos = content.index("Austin")
    denver_pos = content.index("Denver")
    assert austin_pos < denver_pos, "Austin should be listed before Denver (alphabetical)"
    assert "reports/austin-tx.md" in content
    assert "reports/denver-co.md" in content


def test_build_index_links_to_hash_when_report_not_yet_rendered(isolated_dirs):
    """A city can exist in cities/*.json before render.py has run for it —
    the index must not link to a report file that doesn't exist yet."""
    from fetch import save_city_record
    save_city_record(make_city("Austin", "TX", "Travis County", "austin-tx"))
    path = build_index()
    content = path.read_text()
    assert 'href="#"' in content
    assert "reports/austin-tx.md" not in content


def test_build_index_escapes_html_special_characters(isolated_dirs):
    from fetch import save_city_record
    save_city_record(make_city("O'Brien<test>", "TX", "Some & County", "obrien-tx"))
    path = build_index()
    content = path.read_text()
    assert "<test>" not in content  # must be escaped, not raw
