"""Research orchestrator (tech section 7, plan M3 task 3).

One async ``run_research(request, deps)`` drives the deterministic pipeline:

normalize input -> Exa identity search -> identity gate -> bounded identity
fallback (search 1b, only when the domain yields no pages) -> Exa searches
2-4 (concurrency <= 2, failures isolated) -> optional MoltSets enrichment ->
normalization -> status/data-mode computation -> validated ``Report``.

Identity resolution rules (unit-tested, docstring is binding):

- Hit hosts are compared with the same normalization as input
  (``compare_key``: lowercase, ``www.`` stripped). A hit "domain-matches"
  when its host key equals the key or ends with ``"." + key``.
- Name matching: the supplied name and the hit's title / first host label
  are squashed (casefolded, non-alphanumerics stripped). A hit
  "name-matches" when the squashed name is a substring of the squashed
  title or of the first host label.
- Domain supplied (with or without a name): ``resolved`` iff >=1 hit
  domain-matches AND (no name was supplied OR >=1 domain-matching hit also
  name-matches). Domain-matching hits that all fail the name check ->
  ambiguous, note "supplied name and domain do not agree". No
  domain-matching hit at all -> unresolved, note "no evidence for this
  domain".
- Name only: take the top 5 hits that name-match. ``resolved`` iff >=1 such
  hit exists AND all share one compare_key. Multiple hosts -> ambiguous;
  zero matching hits -> unresolved.
- Identity fallback (search 1b): when a domain was supplied and the primary
  identity search found no pages on it (note "no evidence for this domain"),
  exactly one broader fallback search runs — same subject and
  ``include_domains``, ``category`` omitted — and its result goes through
  the SAME gate (no acceptance rule is loosened). A ``ProviderError`` from
  the fallback raises ``ResearchUnavailable`` with the provider code,
  exactly like a failed primary identity search.
- Search budget: at most four logical Exa searches per report. When the
  fallback (1b) runs and resolves, the leadership search is skipped
  (identity + fallback + overview + growth = 4); when it runs and still
  finds nothing, the run ends after 2 searches. ``providers.exa.note``
  discloses the fallback in both cases.
- Resolved ``company_name``/``domain`` are ``verified`` and carry the
  supporting hit(s) as sources. The user's input alone is NEVER a source.
  The name comes from the supporting hit's title, cleaned minimally (text
  before the first " | ", " - " or " — "); when the title is empty the host
  label is title-cased and the field ``note`` says so.

Provider failures: ``ProviderError`` is the only swallowed exception type —
it becomes a ``ReportError`` (searches 2-4) or a provider summary state
(MoltSets). Every other exception propagates to the 500 handler. The
adapters are the single retry boundary: no retry logic lives here.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.errors.provider_error import ProviderError, ResearchUnavailable, Stage
from app.normalize.domain import NormalizedDomain, normalize_domain
from app.normalize.report import (
    EvidenceBundle,
    first_label_key_or_none,
    hit_host_key,
    hit_source,
    host_matches,
    normalize_fields,
    squash,
)
from app.policy.status import (
    FACTUAL_FIELDS,
    compute_data_mode,
    compute_missing_fields,
    compute_overall_status,
)
from app.providers.types import (
    ExaHit,
    ExaProvider,
    ExaSearchRequest,
    ExaSearchResult,
    MoltSetsCompanyResult,
    MoltSetsPeopleResult,
    MoltSetsProvider,
)
from app.research.queries import (
    SearchIdentity,
    growth_query,
    identity_fallback_query,
    identity_query,
    leadership_query,
    overview_query,
)
from app.schemas.report import (
    CompanyIdentity,
    Field,
    Providers,
    ProviderState,
    ProviderSummary,
    Report,
    ReportError,
    Source,
)
from app.schemas.research import ResearchRequest

logger = logging.getLogger("outmate.orchestrator")

PEOPLE_SENIORITY = ["C Suite", "VP", "Owner"]
PEOPLE_LIMIT = 5
MAX_SEARCH_CONCURRENCY = 2

_NAME_SPLIT_CHARS = (" | ", " - ", " — ")


@dataclass
class ResearchDeps:
    """Injected dependencies — tests substitute fakes, never the network."""

    exa: ExaProvider | None
    moltsets: MoltSetsProvider | None
    moltsets_mode: str
    exa_mode: str
    clock: Callable[[], datetime]
    request_id: str


@dataclass
class IdentityResolution:
    """Outcome of the identity gate."""

    resolved: bool
    note: str | None
    domain_key: str | None
    name: str | None
    name_note: str | None
    supporting: list[ExaHit]
    candidates: list[ExaHit]


def _clean_title_name(title: str | None) -> str | None:
    """Minimal cleanup: text before the first separator, trimmed."""
    if not title:
        return None
    name = title
    for sep in _NAME_SPLIT_CHARS:
        if sep in name:
            name = name.split(sep, 1)[0]
    name = name.strip()
    return name or None


def _name_matches_hit(name: str, hit: ExaHit) -> bool:
    target = squash(name)
    if not target:
        return False
    if target in squash(hit.title):
        return True
    host_key = hit_host_key(hit.url)
    if host_key:
        return target in squash(host_key.split(".", 1)[0])
    return False


def resolve_identity(
    name: str | None,
    domain: NormalizedDomain | None,
    result: ExaSearchResult,
    clock: Callable[[], datetime],
) -> IdentityResolution:
    """Apply the identity rules in the module docstring."""
    hits = result.hits
    if domain is not None:
        domain_hits = [h for h in hits if host_matches(hit_host_key(h.url), domain.compare_key)]
        if not domain_hits:
            return IdentityResolution(
                resolved=False,
                note="no evidence for this domain",
                domain_key=None,
                name=None,
                name_note=None,
                supporting=[],
                candidates=hits[:5],
            )
        if name is not None:
            supporting = [h for h in domain_hits if _name_matches_hit(name, h)]
            if not supporting:
                return IdentityResolution(
                    resolved=False,
                    note="supplied name and domain do not agree",
                    domain_key=None,
                    name=None,
                    name_note=None,
                    supporting=[],
                    candidates=domain_hits[:5],
                )
        else:
            supporting = domain_hits
        resolved_key: str | None = domain.compare_key
    else:
        if not name:
            return IdentityResolution(
                resolved=False,
                note="no evidence for the supplied identity",
                domain_key=None,
                name=None,
                name_note=None,
                supporting=[],
                candidates=hits[:5],
            )
        matching = [h for h in hits if _name_matches_hit(name, h)][:5]
        if not matching:
            return IdentityResolution(
                resolved=False,
                note="no public-source hits matched the supplied company name",
                domain_key=None,
                name=None,
                name_note=None,
                supporting=[],
                candidates=hits[:5],
            )
        keys = {hit_host_key(h.url) for h in matching}
        keys.discard(None)
        if len(keys) != 1:
            return IdentityResolution(
                resolved=False,
                note="top name-matching hits point to more than one host",
                domain_key=None,
                name=None,
                name_note=None,
                supporting=[],
                candidates=matching,
            )
        supporting = matching
        resolved_key = next(iter(keys))

    lead = supporting[0]
    resolved_name = _clean_title_name(lead.title)
    name_note = None
    if resolved_name is None:
        label = first_label_key_or_none(lead.url)
        resolved_name = label or (resolved_key.split(".", 1)[0] if resolved_key else None)
        if resolved_name:
            resolved_name = resolved_name.replace("-", " ").title()
        name_note = "Company name was derived from the host label."
    return IdentityResolution(
        resolved=True,
        note=None,
        domain_key=resolved_key,
        name=resolved_name,
        name_note=name_note,
        supporting=supporting,
        candidates=supporting,
    )


def _identity_fields(
    resolution: IdentityResolution,
    result: ExaSearchResult | None,
    clock: Callable[[], datetime],
) -> CompanyIdentity:
    """Build the ``company_identity`` block for resolved and unresolved runs."""
    if result is not None:
        sources = [hit_source(h, result, clock) for h in resolution.supporting]
        candidate_sources = [hit_source(h, result, clock) for h in resolution.candidates]
    else:
        sources = []
        candidate_sources = []
    if resolution.resolved:
        verified = any(s.url is not None for s in sources)
        return CompanyIdentity(
            resolved=True,
            company_name=Field[str](
                value=resolution.name,
                status="verified" if verified else "unavailable",
                sources=sources,
                note=resolution.name_note,
            ),
            domain=Field[str](
                value=resolution.domain_key,
                status="verified" if verified else "unavailable",
                sources=sources,
            ),
            note=resolution.note,
        )
    return CompanyIdentity(
        resolved=False,
        company_name=Field[str](value=None, status="unavailable", sources=candidate_sources),
        domain=Field[str](value=None, status="unavailable", sources=candidate_sources),
        note=resolution.note,
    )


def _moltsets_skipped_summary(deps: ResearchDeps) -> ProviderSummary:
    """Provider summary when the identity gate blocks all MoltSets calls."""
    if deps.moltsets_mode == "off":
        state: ProviderState = "disabled"
        mock = False
    elif deps.moltsets_mode == "mock":
        state = "mock"
        mock = True
    elif deps.moltsets is None:
        state = "not_configured"
        mock = False
    else:
        state = "disabled"
        mock = False
    return ProviderSummary(
        state=state,
        mock=mock,
        calls=0,
        note="skipped: identity not resolved",
    )


def _moltsets_mode_summary(deps: ResearchDeps) -> ProviderSummary | None:
    """Early-exit summary for modes that never make a call (off / missing
    provider). Returns ``None`` when real calls should run."""
    if deps.moltsets_mode == "off":
        return ProviderSummary(
            state="disabled",
            mock=False,
            calls=0,
            note="MoltSets enrichment is disabled (MOLTSETS_MODE=off).",
        )
    if deps.moltsets is None:
        return ProviderSummary(
            state="not_configured",
            mock=False,
            calls=0,
            note="MoltSets is not configured; enrichment fields are unavailable.",
        )
    return None


def _collect_sources(report: Report) -> list[Source]:
    """Every source attached anywhere in the report (for ``data_mode``)."""
    sources: list[Source] = []
    sources.extend(report.company_identity.company_name.sources)
    sources.extend(report.company_identity.domain.sources)
    for field_name in FACTUAL_FIELDS:
        field_value = getattr(report, field_name)
        sources.extend(field_value.sources)
        value = field_value.value
        if isinstance(value, list):
            for item in value:
                item_sources = getattr(item, "sources", None)
                if item_sources:
                    sources.extend(item_sources)
    hypothesis = report.potential_gtm_opportunity.value
    if hypothesis is not None:
        for point in hypothesis.rationale:
            sources.extend(point.sources)
    return sources


async def run_research(request: ResearchRequest, deps: ResearchDeps) -> Report:
    """Run the deterministic research workflow and return a valid Report.

    Raises ``ResearchUnavailable`` only when no report can exist at all;
    every other provider failure degrades into report errors / unavailable
    fields while usable evidence is preserved.
    """
    clock = deps.clock
    warnings: list[str] = []
    errors: list[ReportError] = []

    domain = normalize_domain(request.domain) if request.domain else None

    exa = deps.exa
    if exa is None:
        raise ResearchUnavailable(
            code="exa_not_configured",
            message="Exa search is not configured: set EXA_API_KEY on the backend.",
        )

    # -- identity search -----------------------------------------------------
    try:
        identity_result = await exa.search(identity_query(request.company_name, domain))
    except ProviderError as exc:
        # The adapter already retried; any ProviderError here is final and
        # there is no identity evidence, so no report can be produced.
        logger.info("identity search failed code=%s request_id=%s", exc.code, deps.request_id)
        raise ResearchUnavailable(code=exc.code, message=exc.message) from exc

    exa_calls = identity_result.calls
    exa_mock = identity_result.mock
    exa_usage = identity_result.usage
    exa_failures = 0

    resolution = resolve_identity(request.company_name, domain, identity_result, clock)

    # -- bounded identity fallback (search 1b) --------------------------------
    # Only a domain whose pages the company-category search could not see gets
    # the broader, category-less retry; the gate itself is never loosened.
    fallback_used = False
    if (
        domain is not None
        and not resolution.resolved
        and resolution.note == "no evidence for this domain"
    ):
        fallback_used = True
        try:
            fallback_result = await exa.search(
                identity_fallback_query(request.company_name, domain)
            )
        except ProviderError as exc:
            # Same rule as a failed primary identity search: without identity
            # evidence no report can be produced.
            logger.info(
                "identity fallback search failed code=%s request_id=%s",
                exc.code,
                deps.request_id,
            )
            raise ResearchUnavailable(code=exc.code, message=exc.message) from exc
        exa_calls += fallback_result.calls
        exa_mock = exa_mock or fallback_result.mock
        exa_usage = fallback_result.usage if fallback_result.usage is not None else exa_usage
        identity_result = fallback_result
        resolution = resolve_identity(request.company_name, domain, fallback_result, clock)

    # -- unresolved identity -> early report, no enrichment ------------------
    if not resolution.resolved or resolution.domain_key is None:
        exa_summary = ProviderSummary(
            state="ok",
            mock=exa_mock,
            calls=exa_calls,
            usage=exa_usage,
            note=(
                "The identity search and a broader fallback search both "
                "returned no pages on this domain."
                if fallback_used
                else None
            ),
        )
        moltsets_summary = _moltsets_skipped_summary(deps)
        report = Report(
            data_mode="live",  # placeholder, recomputed below
            request=request,
            company_identity=_identity_fields(resolution, identity_result, clock),
            description=Field[str](value=None, status="unavailable", sources=[]),
            industry=Field[str](value=None, status="unavailable", sources=[]),
            target_market=Field[str](value=None, status="unavailable", sources=[]),
            employee_size=Field[str](value=None, status="unavailable", sources=[]),
            growth_signals=Field(value=[], status="unavailable", sources=[]),
            decision_makers=Field(value=[], status="unavailable", sources=[]),
            contact_information=Field(value=[], status="unavailable", sources=[]),
            potential_gtm_opportunity=Field(value=None, status="unavailable", sources=[]),
            overall_status="unavailable",
            missing_fields=[],
            errors=errors,
            warnings=warnings,
            providers=Providers(exa=exa_summary, moltsets=moltsets_summary),
            request_id=deps.request_id,
            generated_at=clock(),
        )
        data_mode = compute_data_mode(
            _collect_sources(report),
            {"exa": exa_summary, "moltsets": moltsets_summary},
        )
        return report.model_copy(
            update={
                "missing_fields": compute_missing_fields(report),
                "data_mode": data_mode,
            }
        )

    # -- searches 2-4, concurrency capped, failures isolated -----------------
    domain_key = resolution.domain_key
    identity = SearchIdentity(name=resolution.name, domain=domain_key)
    semaphore = asyncio.Semaphore(MAX_SEARCH_CONCURRENCY)

    async def _search(request_obj: ExaSearchRequest) -> ExaSearchResult:
        async with semaphore:
            return await exa.search(request_obj)

    stage_plan: list[tuple[Stage, ExaSearchRequest]] = [
        ("overview", overview_query(identity, request.research_objective)),
        ("growth", growth_query(identity, clock())),
    ]
    if not fallback_used:
        stage_plan.append(("leadership", leadership_query(identity)))

    stage_results = await asyncio.gather(
        *(_search(request_obj) for _, request_obj in stage_plan),
        return_exceptions=True,
    )

    overview: ExaSearchResult | None = None
    growth: ExaSearchResult | None = None
    leadership: ExaSearchResult | None = None
    for (stage, _), outcome in zip(stage_plan, stage_results, strict=True):
        if isinstance(outcome, ProviderError):
            outcome.with_stage(stage)
            errors.append(outcome.to_report_error(deps.request_id))
            exa_calls += outcome.calls
            exa_failures += 1
            continue
        if isinstance(outcome, BaseException):
            raise outcome  # unexpected -> the 500 handler
        if stage == "overview":
            overview = outcome
        elif stage == "growth":
            growth = outcome
        else:
            leadership = outcome
        exa_calls += outcome.calls
        exa_mock = exa_mock or outcome.mock
        exa_usage = outcome.usage if outcome.usage is not None else exa_usage

    # -- MoltSets enrichment --------------------------------------------------
    moltsets_company: MoltSetsCompanyResult | None = None
    moltsets_people: MoltSetsPeopleResult | None = None
    moltsets_mode_summary = _moltsets_mode_summary(deps)
    if moltsets_mode_summary is None:
        assert deps.moltsets is not None
        ms_calls = 0
        ms_mock = deps.moltsets_mode == "mock"
        ms_usage = None
        state: ProviderState = "ok"
        try:
            moltsets_company = await deps.moltsets.search_company(domain_key)
            ms_calls += moltsets_company.calls
            ms_usage = moltsets_company.usage
            ms_mock = ms_mock or moltsets_company.mock
            if moltsets_company.found:
                try:
                    moltsets_people = await deps.moltsets.search_people(
                        domain_key,
                        PEOPLE_SENIORITY,
                        limit=PEOPLE_LIMIT,
                    )
                    ms_calls += moltsets_people.calls
                    ms_usage = (
                        moltsets_people.usage if moltsets_people.usage is not None else ms_usage
                    )
                    ms_mock = ms_mock or moltsets_people.mock
                    state = "mock" if ms_mock else "ok"
                except ProviderError as exc:
                    exc.with_stage("enrichment")
                    errors.append(exc.to_report_error(deps.request_id))
                    ms_calls += exc.calls
                    state = (
                        "quota_exhausted" if exc.code == "moltsets_quota_exhausted" else "partial"
                    )
            else:
                state = "not_found"
        except ProviderError as exc:
            exc.with_stage("enrichment")
            errors.append(exc.to_report_error(deps.request_id))
            ms_calls += exc.calls
            state = "quota_exhausted" if exc.code == "moltsets_quota_exhausted" else "error"
        moltsets_summary = ProviderSummary(
            state=state, mock=ms_mock, calls=ms_calls, usage=ms_usage
        )
    else:
        moltsets_summary = moltsets_mode_summary

    # -- normalize + statuses --------------------------------------------------
    bundle = EvidenceBundle(
        domain_key=domain_key,
        company_squash=squash(resolution.name),
        overview=overview,
        growth=growth,
        leadership=leadership,
        moltsets_company=moltsets_company,
        moltsets_people=moltsets_people,
        clock=clock,
    )
    fields = normalize_fields(bundle, request.research_objective, resolution.name, warnings)

    if exa_failures == 0:
        exa_state: ProviderState = "ok"
    elif overview is None and growth is None and leadership is None:
        exa_state = "error"
    else:
        exa_state = "partial"
    exa_summary = ProviderSummary(
        state=exa_state,
        mock=exa_mock,
        calls=exa_calls,
        usage=exa_usage,
        note=(
            "Identity confirmed via a broader fallback search; the leadership "
            "search was skipped to stay within the four-search budget."
            if fallback_used
            else None
        ),
    )

    report = Report(
        data_mode="live",  # placeholder, recomputed below
        request=request,
        company_identity=_identity_fields(resolution, identity_result, clock),
        description=fields.description,
        industry=fields.industry,
        target_market=fields.target_market,
        employee_size=fields.employee_size,
        growth_signals=fields.growth_signals,
        decision_makers=fields.decision_makers,
        contact_information=fields.contact_information,
        potential_gtm_opportunity=fields.potential_gtm_opportunity,
        overall_status="unavailable",  # placeholder, recomputed below
        missing_fields=[],
        errors=errors,
        warnings=warnings,
        providers=Providers(exa=exa_summary, moltsets=moltsets_summary),
        request_id=deps.request_id,
        generated_at=clock(),
    )
    return report.model_copy(
        update={
            "overall_status": compute_overall_status(report),
            "missing_fields": compute_missing_fields(report),
            "data_mode": compute_data_mode(
                _collect_sources(report),
                {"exa": exa_summary, "moltsets": moltsets_summary},
            ),
        }
    )
