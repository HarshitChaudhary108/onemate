"""One cell execution (plan §4.2–§4.3).

Render the prompt (``{{Company}}``/``{{Website}}`` from the resolved
identity, ``{{Column}}`` from upstream values inside explicit delimiters,
length-capped — upstream values are web text and a prompt-injection
surface), call Exa once (MVP strategy ``research``: one ``/search`` +
``outputSchema`` per cell), validate the value per ``output_type``, and map
to a ``Field`` exactly per the §4.2 table. ``ProviderError`` becomes an
``error`` outcome carrying the safe message and code — the runner decides
whether a code stops the whole run.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.engine.graph import _REF_RE
from app.errors.provider_error import ProviderError
from app.normalize.report import clean_text, exa_source
from app.providers.types import ExaProvider, ExaSearchResult
from app.research.cell_query import cell_search_request
from app.schemas.report import Field, Source
from app.sheets.models import CellValue, Column, OutputType, Row
from app.utils.safe_url import safe_url

TEXT_VALUE_LIMIT = 500
UPSTREAM_VALUE_LIMIT = 500

_NO_CITED_SOURCE = "no cited source"

_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class CellOutcome:
    """The result of one cell execution.

    ``status`` is the cell status to write (``done`` or ``error``);
    ``error_code`` carries the ``ProviderError`` code so the runner can
    stop the whole run on 401/402.
    """

    status: Literal["done", "error"]
    field: Field[CellValue] | None
    cost: float | None
    rendered_prompt: str | None
    error: str | None = None
    error_code: str | None = None


def render_prompt(
    template: str,
    *,
    company: str | None,
    website: str | None,
    upstream: Mapping[str, Field[CellValue] | None],
) -> str:
    """Substitute every ``{{...}}`` in the template.

    ``{{Company}}``/``{{Website}}`` render the resolved identity; column
    references render the upstream cell's value inside explicit
    ``<upstream column="...">`` delimiters, control-stripped and capped at
    ``UPSTREAM_VALUE_LIMIT`` characters — the value is data, never part of
    the instruction text.
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name == "Company":
            return company or ""
        if name == "Website":
            return website or ""
        field = upstream.get(name)
        if field is None or field.value is None:
            text = "(unavailable)"
        else:
            raw = str(field.value)
            text = clean_text(raw, UPSTREAM_VALUE_LIMIT) or "(unavailable)"
        safe_name = name.replace('"', "'")
        return f'<upstream column="{safe_name}">\n{text}\n</upstream>'

    return _REF_RE.sub(_replace, template)


def validate_value(
    output_type: OutputType, raw: object, enum_options: list[str] | None
) -> tuple[CellValue | None, str | None]:
    """Validate one ``output.value`` per §4.2. Returns ``(value, None)`` on
    success or ``(None, reason)`` — the reason lands in the field note."""
    if output_type == "text":
        if not isinstance(raw, str):
            return None, "wrong type for text"
        cleaned = clean_text(raw, TEXT_VALUE_LIMIT)
        return (cleaned, None) if cleaned else (None, "empty value")
    if output_type == "number":
        # bool must be excluded first — bool is an int subclass.
        if isinstance(raw, bool):
            return None, "wrong type for number"
        if isinstance(raw, int | float):
            return raw, None
        if isinstance(raw, str):
            text = raw.strip()
            if _INT_RE.match(text):
                return int(text), None
            if _NUM_RE.match(text):
                return float(text), None
        return None, "wrong type for number"
    if output_type == "boolean":
        if isinstance(raw, bool):
            return raw, None
        return None, "wrong type for boolean"
    if output_type == "date":
        if not isinstance(raw, str) or not _DATE_RE.match(raw.strip()):
            return None, "wrong type for date"
        try:
            return date.fromisoformat(raw.strip()).isoformat(), None
        except ValueError:
            return None, "wrong type for date"
    if output_type == "url":
        if not isinstance(raw, str):
            return None, "wrong type for url"
        safe = safe_url(raw)
        return (safe, None) if safe else (None, "not a safe http(s) URL")
    # enum: case-insensitive match to an option; the canonical option wins.
    if not isinstance(raw, str):
        return None, "wrong type for enum"
    options = {option.casefold(): option for option in (enum_options or [])}
    matched = options.get(raw.strip().casefold())
    return (matched, None) if matched is not None else (None, "not one of the enum options")


def result_cost(result: ExaSearchResult) -> float | None:
    """A cell's cost is ``usage["total"]`` (the ``costDollars.total``
    object member) when present."""
    usage = result.usage
    if not isinstance(usage, dict):
        return None
    total = usage.get("total")
    return float(total) if isinstance(total, int | float) and not isinstance(total, bool) else None


def map_exa_result(
    *,
    output_type: OutputType,
    enum_options: list[str] | None,
    result: ExaSearchResult,
    clock: Callable[[], datetime],
) -> Field[CellValue]:
    """Map one structured Exa result to a ``Field`` exactly per §4.2."""
    output = result.output
    if output is None or not isinstance(output.content, dict):
        return Field[CellValue](
            value=None, status="unavailable", note="no structured output returned"
        )
    content = output.content
    if content.get("found") is not True:
        return Field[CellValue](value=None, status="unavailable", note="no reliable answer found")
    raw = content.get("value")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return Field[CellValue](value=None, status="unavailable", note="empty value")
    value, reason = validate_value(output_type, raw, enum_options)
    if reason is not None or value is None:
        return Field[CellValue](value=None, status="unavailable", note=reason)

    # Grounding: only entries for "value*" count; citations must carry a
    # usable http(s) URL. No usable grounding -> never guessed.
    sources: list[Source] = []
    best_confidence: str | None = None
    if output.grounding:
        for entry in output.grounding:
            if not (entry.field == "value" or entry.field.startswith("value")):
                continue
            usable = [c for c in entry.citations if c.url is not None and safe_url(c.url)]
            if not usable:
                continue
            if best_confidence is None or (
                _CONFIDENCE_RANK[entry.confidence] > _CONFIDENCE_RANK[best_confidence]
            ):
                best_confidence = entry.confidence
            for citation in usable:
                assert citation.url is not None
                sources.append(exa_source(citation.url, citation.title, None, None, result, clock))
    if not sources:
        return Field[CellValue](
            value=value,
            status="unavailable",
            sources=[],
            note=_NO_CITED_SOURCE,
        )
    if best_confidence == "high":
        return Field[CellValue](value=value, status="verified", sources=sources)
    return Field[CellValue](
        value=value,
        status="inferred",
        sources=sources,
        note=f"confidence: {best_confidence}",
    )


async def execute_cell(
    *,
    exa: ExaProvider,
    row: Row,
    column: Column,
    upstream: Mapping[str, Field[CellValue] | None],
    clock: Callable[[], datetime],
) -> CellOutcome:
    """Render, search once, validate and map — one cell end to end."""
    company = row.resolved_name or row.input_name
    website = row.resolved_domain or row.input_domain
    rendered = render_prompt(
        column.prompt_template,
        company=company,
        website=website,
        upstream=upstream,
    )
    request = cell_search_request(
        rendered_prompt=rendered,
        company_name=company,
        domain=website,
        source_scope=column.source_scope,
        output_type=column.output_type,
        enum_options=column.enum_options,
        depth=column.depth,
        now=clock(),
    )
    try:
        result = await exa.search(request)
    except ProviderError as exc:
        return CellOutcome(
            status="error",
            field=None,
            cost=None,
            rendered_prompt=rendered,
            error=exc.message,
            error_code=exc.code,
        )
    field = map_exa_result(
        output_type=column.output_type,
        enum_options=column.enum_options,
        result=result,
        clock=clock,
    )
    return CellOutcome(
        status="done",
        field=field,
        cost=result_cost(result),
        rendered_prompt=rendered,
    )
