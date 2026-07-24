import pytest

from severity import classify_severity


@pytest.mark.parametrize("text,expected", [
    ("Moderate to High - Flooding is common", "high"),
    ("Low-to-moderate - few earthquakes", "moderate"),
    ("None/Very Low - landlocked", "low"),
    ("High risk of wildfire", "high"),
    ("Moderate-to-strict: requires floodplain permits", "high"),
    ("Low - Colorado is a low-regulation state, no notification required", "low"),
    ("Very low / minimal regulation, no notification required", "low"),
    ("Lenient permitting, streamlined review", "low"),
])
def test_classify_severity_picks_highest_severity_mentioned(text, expected):
    result = classify_severity(text)
    assert result["level"] == expected


def test_classify_severity_returns_none_when_no_keyword_matches():
    assert classify_severity("Frequent outages during storms") is None
