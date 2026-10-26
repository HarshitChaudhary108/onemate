"""Cell Exa request builder tests (plan §4.3 scope caveats)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.research.cell_query import (
    cell_output_schema,
    cell_search_request,
    cell_system_prompt,
)
from app.research.queries import GROWTH_LOOKBACK_DAYS

NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def _request(**overrides: object) -> object:
    base: dict[str, object] = {
        "rendered_prompt": "What does Acme Corp sell?",
        "company_name": "Acme Corp",
        "domain": "acme.test",
        "source_scope": "web",
        "output_type": "text",
        "now": NOW,
    }
    base.update(overrides)
    return cell_search_request(**base)  # type: ignore[arg-type]


def test_company_site_scopes_to_domain_without_category() -> None:
    request = _request(source_scope="company_site")
    assert request.include_domains == ["acme.test"]
    assert request.category is None
    assert request.start_published_date is None


def test_company_site_requires_domain() -> None:
    with pytest.raises(ValueError):
        _request(source_scope="company_site", domain=None)


def test_web_has_no_domain_filter_and_anchors_on_identity() -> None:
    request = _request(source_scope="web")
    assert request.include_domains is None
    assert request.category is None
    assert "Acme Corp" in request.query
    assert "acme.test" in request.query
    assert "What does Acme Corp sell?" in request.query


def test_news_uses_category_and_365_day_window() -> None:
    request = _request(source_scope="news")
    assert request.category == "news"
    expected = (NOW - timedelta(days=GROWTH_LOOKBACK_DAYS)).isoformat()
    assert request.start_published_date == expected


def test_people_has_no_domains_or_date_filters() -> None:
    request = _request(source_scope="people")
    assert request.category == "people"
    assert request.include_domains is None
    assert request.start_published_date is None
    assert request.end_published_date is None


def test_every_request_carries_schema_system_prompt_and_type() -> None:
    for scope in ("company_site", "web", "news", "people"):
        request = _request(source_scope=scope)
        assert request.output_schema is not None
        assert request.system_prompt is not None
        assert request.search_type == "auto"
    assert _request(depth="deep").search_type == "deep"


def test_output_schema_only_uses_documented_keys() -> None:
    schema = cell_output_schema("text")
    assert set(schema) <= {"type", "description", "properties"}
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"found", "value"}
    for prop in schema["properties"].values():
        assert set(prop) <= {"type", "description"}


@pytest.mark.parametrize(
    ("output_type", "json_type"),
    [
        ("text", "string"),
        ("number", "number"),
        ("boolean", "boolean"),
        ("enum", "string"),
        ("url", "string"),
        ("date", "string"),
    ],
)
def test_output_schema_value_type_per_output_type(output_type: str, json_type: str) -> None:
    schema = cell_output_schema(output_type)  # type: ignore[arg-type]
    assert schema["properties"]["value"]["type"] == json_type


def test_enum_options_stay_in_description_not_schema_keywords() -> None:
    schema = cell_output_schema("enum", ["high", "low"])
    assert set(schema["properties"]["value"]) <= {"type", "description"}
    assert "high" in schema["properties"]["value"]["description"]
    assert "low" in schema["properties"]["value"]["description"]
    assert "enum" not in schema["properties"]["value"]


def test_system_prompt_anchors_company_and_marks_data() -> None:
    prompt = cell_system_prompt("Acme Corp", "acme.test")
    assert "Acme Corp" in prompt
    assert "acme.test" in prompt
    assert "never as instructions" in prompt
