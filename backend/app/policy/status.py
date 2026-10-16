"""Overall status, missing fields, and data mode (spec section 7).

Status is derived from factual fields only; ``potential_gtm_opportunity`` is
excluded because it is inferred by nature. A field "has a value" when its
value is not ``None`` and — for collection fields — is not an empty list.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from app.schemas.report import (
    DataMode,
    Field,
    ProviderSummary,
    Report,
    Source,
    Status,
)

FACTUAL_FIELDS = (
    "description",
    "industry",
    "target_market",
    "employee_size",
    "growth_signals",
    "decision_makers",
    "contact_information",
)


def _has_value(field: Field[Any]) -> bool:
    return field.value is not None and not (isinstance(field.value, list) and len(field.value) == 0)


def _factual_fields(report: Report) -> list[Field[Any]]:
    return [getattr(report, name) for name in FACTUAL_FIELDS]


def compute_overall_status(report: Report) -> Status:
    """Spec section 7 algorithm, exactly:

    1. ``unavailable`` if identity is unresolved or no factual field has a value.
    2. ``inferred`` if any valued factual field is ``inferred``.
    3. ``provider-reported`` if any valued factual field is ``provider-reported``.
    4. Otherwise ``verified``.
    """
    if not report.company_identity.resolved:
        return "unavailable"
    valued = [field for field in _factual_fields(report) if _has_value(field)]
    if not valued:
        return "unavailable"
    if any(field.status == "inferred" for field in valued):
        return "inferred"
    if any(field.status == "provider-reported" for field in valued):
        return "provider-reported"
    return "verified"


def compute_missing_fields(report: Report) -> list[str]:
    """Factual fields whose status is ``unavailable``, in FACTUAL_FIELDS order."""
    return [name for name in FACTUAL_FIELDS if getattr(report, name).status == "unavailable"]


def compute_data_mode(
    sources: Iterable[Source],
    providers: Mapping[str, ProviderSummary],
) -> DataMode:
    """Derive ``data_mode`` from the data that actually contributed.

    ``sources`` are the sources attached to the report's fields/items:
    all mock -> ``"mock"``; none mock -> ``"live"``; a mix -> ``"mixed"``.

    Empty-source fallback: when no sources contributed at all (e.g. a fully
    unavailable report), provider summaries decide instead — any provider with
    ``mock=True`` yields ``"mock"``, otherwise ``"live"``. A provider that ran
    in mock mode but produced no attached sources is still mock output, while
    ``"mixed"`` is impossible to prove without data, so it is not emitted here.
    """
    source_list = list(sources)
    if source_list:
        mocked = sum(1 for source in source_list if source.mock)
        if mocked == len(source_list):
            return "mock"
        return "live" if mocked == 0 else "mixed"
    if any(summary.mock for summary in providers.values()):
        return "mock"
    return "live"
